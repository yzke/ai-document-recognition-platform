import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import fitz
from rapidocr_onnxruntime import RapidOCR

from .config import OCR_INTER_THREADS, OCR_INTRA_THREADS, PAGE_DIR_NAME, TEXT_DIR_NAME, VLM_DIR_NAME
from .page_ranges import parse_page_range


class OcrService:
    def __init__(self, store, schedule_vlm):
        self.store = store
        self.schedule_vlm = schedule_vlm
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
        lines, records = [], []
        for item in result or []:
            try:
                box, text, score = item[0], str(item[1]).strip(), float(item[2])
            except Exception:
                continue
            if text:
                lines.append(text)
                records.append({"text": text, "score": round(score, 4), "box": box})
        return "\n".join(lines), records

    def ocr_page(self, job_id, pdf_path, page_no, dpi, keywords):
        paths = self.store.job_path(job_id)
        image_path = paths / PAGE_DIR_NAME / f"page_{page_no:04d}.png"
        text_path = paths / TEXT_DIR_NAME / f"page_{page_no:04d}.txt"
        json_path = paths / TEXT_DIR_NAME / f"page_{page_no:04d}.json"
        t0 = time.time()
        self.render_page(pdf_path, page_no - 1, image_path, dpi)
        text, records = self.normalize(self.engine()(str(image_path)))
        hits = [k for k in keywords if k in text]
        text_path.write_text(text, encoding="utf-8")
        json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"page": page_no, "chars": len(text), "keywords": hits, "candidate": bool(hits), "seconds": round(time.time() - t0, 2)}

    def process_job(self, job_id, pdf_path, keywords, dpi, workers, max_pages=0):
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
                        pending[pool.submit(self.ocr_page, job_id, pdf_path, page_no, dpi, keywords)] = page_no
                        index += 1
                    done, _ = wait(pending, return_when=FIRST_COMPLETED, timeout=0.5)
                    for future in done:
                        pending.pop(future, None)
                        if future.cancelled():
                            continue
                        result = future.result()
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
