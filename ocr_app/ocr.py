import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import fitz
from rapidocr_onnxruntime import RapidOCR

from .config import OCR_INTER_THREADS, OCR_INTRA_THREADS, PAGE_DIR_NAME, TEXT_DIR_NAME, VLM_DIR_NAME
from .page_ranges import parse_page_range


class OcrService:
    def __init__(self, store, schedule_vlm, audit=None):
        self.store = store
        self.schedule_vlm = schedule_vlm
        self.audit = audit
        self._engine = None
        self._lock = threading.Lock()

    def engine(self):
        with self._lock:
            if self._engine is None:
                self._engine = RapidOCR(intra_op_num_threads=OCR_INTRA_THREADS, inter_op_num_threads=OCR_INTER_THREADS)
            return self._engine

    def render_page(self, pdf_path, page_index, out_path, dpi):
        if out_path.exists():
            return
        doc = fitz.open(pdf_path)
        try:
            pix = doc[page_index].get_pixmap(dpi=dpi, alpha=False)
            pix.save(out_path)
        finally:
            doc.close()

    def normalize(self, result):
        if isinstance(result, tuple):
            result = result[0]
        records = []
        for item in result or []:
            try:
                box, text, score = item[0], str(item[1]).strip(), float(item[2])
            except Exception:
                continue
            if text:
                records.append({"text": text, "score": round(score, 4), "box": box})
        return records

    def ordered_records(self, records):
        def center_y(record):
            ys = [point[1] for point in record.get("box") or [[0, 0]]]
            return sum(ys) / max(1, len(ys))

        def left_x(record):
            xs = [point[0] for point in record.get("box") or [[0, 0]]]
            return min(xs) if xs else 0

        if len(records) < 2:
            return records
        heights = []
        for record in records:
            ys = [point[1] for point in record.get("box") or []]
            if ys:
                heights.append(max(ys) - min(ys))
        line_gap = max(8, (sum(heights) / len(heights) if heights else 16) * 0.65)
        rows = []
        for record in sorted(records, key=lambda item: (center_y(item), left_x(item))):
            y = center_y(record)
            if not rows or abs(rows[-1]["y"] - y) > line_gap:
                rows.append({"y": y, "items": [record]})
            else:
                rows[-1]["items"].append(record)
                rows[-1]["y"] = sum(center_y(item) for item in rows[-1]["items"]) / len(rows[-1]["items"])
        ordered = []
        for row in rows:
            ordered.extend(sorted(row["items"], key=left_x))
        return ordered

    def text_from_records(self, records):
        return "\n".join(record["text"] for record in records if record.get("text"))

    def confidence_summary(self, records, low_conf_threshold):
        scores = [float(record.get("score", 0)) for record in records]
        if not scores:
            return {"avg_score": 0, "min_score": 0, "low_conf_count": 0, "low_conf_threshold": low_conf_threshold}
        low_conf_count = sum(1 for score in scores if score < low_conf_threshold)
        return {
            "avg_score": round(sum(scores) / len(scores), 4),
            "min_score": round(min(scores), 4),
            "low_conf_count": low_conf_count,
            "low_conf_threshold": low_conf_threshold,
        }

    def ocr_page(self, job_id, pdf_path, page_no, dpi, keywords, reorder=False, low_conf_threshold=0.75):
        paths = self.store.job_path(job_id)
        image_path = paths / PAGE_DIR_NAME / f"page_{page_no:04d}.png"
        text_path = paths / TEXT_DIR_NAME / f"page_{page_no:04d}.txt"
        json_path = paths / TEXT_DIR_NAME / f"page_{page_no:04d}.json"
        t0 = time.time()
        self.render_page(pdf_path, page_no - 1, image_path, dpi)
        records = self.normalize(self.engine()(str(image_path)))
        text_records = self.ordered_records(records) if reorder else records
        text = self.text_from_records(text_records)
        hits = [k for k in keywords if k in text]
        text_path.write_text(text, encoding="utf-8")
        json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "page": page_no,
            "chars": len(text),
            "keywords": hits,
            "candidate": bool(hits),
            "seconds": round(time.time() - t0, 2),
            "ocr_reordered": bool(reorder),
            **self.confidence_summary(records, low_conf_threshold),
        }

    def process_job(self, job_id, pdf_path, keywords, dpi, workers, max_pages=0, reorder=False, low_conf_threshold=0.75):
        try:
            job = self.store.load(job_id)
            doc = fitz.open(pdf_path)
            total_pdf_pages = doc.page_count
            doc.close()
            page_numbers, page_range_raw = parse_page_range(job.get("page_range_raw", ""), total_pdf_pages)
            if max_pages:
                page_numbers = page_numbers[:max_pages]
            total_pages = len(page_numbers)
            paths = self.store.job_path(job_id)
            for name in [PAGE_DIR_NAME, TEXT_DIR_NAME, VLM_DIR_NAME]:
                (paths / name).mkdir(parents=True, exist_ok=True)
            self.store.update(
                job_id,
                status="running",
                started_at=time.time(),
                total_pdf_pages=total_pdf_pages,
                total_pages=total_pages,
                page_numbers=page_numbers,
                page_range_raw=page_range_raw,
                done_pages=0,
                candidate_pages=[],
                page_results=[],
                message=f"开始 OCR，共 {total_pages} 页，并发 {workers}",
            )

            results, pending, index = [], {}, 0
            cancel_event = self.store.cancel_events[job_id]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                while index < total_pages or pending:
                    if cancel_event.is_set():
                        for future in pending:
                            future.cancel()
                        self.store.update(job_id, status="stopped", message=f"已停止，完成 {len(results)}/{total_pages} 页")
                        return
                    while index < total_pages and len(pending) < workers and not cancel_event.is_set():
                        page_no = page_numbers[index]
                        pending[pool.submit(self.ocr_page, job_id, pdf_path, page_no, dpi, keywords, reorder, low_conf_threshold)] = page_no
                        index += 1
                    done, _ = wait(pending, return_when=FIRST_COMPLETED, timeout=0.5)
                    for future in done:
                        pending.pop(future, None)
                        if future.cancelled():
                            continue
                        result = future.result()
                        if self.audit:
                            self.audit.write(job_id, "ocr_done", result["page"], detail={
                                "chars": result.get("chars", 0),
                                "avg_score": result.get("avg_score", 0),
                                "low_conf_count": result.get("low_conf_count", 0),
                            })
                        results.append(result)
                        results.sort(key=lambda item: item["page"])
                        candidates = [r["page"] for r in results if r["candidate"]]
                        self.store.update(job_id, done_pages=len(results), candidate_pages=candidates, page_results=results, message=f"已完成 {len(results)}/{total_pages} 页")
                        job = self.store.load(job_id)
                        if job.get("auto_vlm_candidates") and result["candidate"]:
                            self.schedule_vlm(job_id, result["page"])

            full_text = []
            for result in sorted(results, key=lambda item: item["page"]):
                text = (paths / TEXT_DIR_NAME / f"page_{result['page']:04d}.txt").read_text(encoding="utf-8", errors="ignore")
                full_text.append(f"===== Page {result['page']} =====\n{text}\n")
            (paths / "full_text.txt").write_text("\n".join(full_text), encoding="utf-8")
            self.store.update(job_id, status="done", message="OCR 完成")
        except Exception as exc:
            self.store.update(job_id, status="failed", message=str(exc))

    def rebuild_full_text(self, job_id):
        job = self.store.load(job_id)
        if not job:
            return
        paths = self.store.job_path(job_id)
        full_text = []
        for page_no in job.get("page_numbers") or []:
            text_path = paths / TEXT_DIR_NAME / f"page_{page_no:04d}.txt"
            if text_path.exists():
                text = text_path.read_text(encoding="utf-8", errors="ignore")
                full_text.append(f"===== Page {page_no} =====\n{text}\n")
        (paths / "full_text.txt").write_text("\n".join(full_text), encoding="utf-8")

    def rerun_page(self, job_id, page_no, dpi, keywords, reorder=False, low_conf_threshold=0.75):
        job = self.store.load(job_id)
        if not job:
            raise ValueError("任务不存在")
        if page_no not in (job.get("page_numbers") or []):
            raise ValueError("页面不在当前任务范围内")
        result = self.ocr_page(job_id, Path(job.get("_pdf_path")), page_no, dpi, keywords, reorder, low_conf_threshold)
        with self.store.lock:
            job = self.store.jobs[job_id]
            page_results = [item for item in job.get("page_results", []) if item.get("page") != page_no]
            page_results.append(result)
            page_results.sort(key=lambda item: item["page"])
            job["page_results"] = page_results
            job["candidate_pages"] = [item["page"] for item in page_results if item.get("candidate")]
            job["updated_at"] = time.time()
            vlm_results = dict(job.get("vlm_results") or {})
            current_vlm = dict(vlm_results.get(str(page_no)) or {})
            if current_vlm.get("status") == "done":
                current_vlm["stale_after_ocr"] = True
                current_vlm["message"] = "OCR 已重跑，建议重新 AI 提取"
                current_vlm["updated_at"] = time.time()
                vlm_results[str(page_no)] = current_vlm
                job["vlm_results"] = vlm_results
            self.store.save(job_id)
        self.rebuild_full_text(job_id)
        return result
