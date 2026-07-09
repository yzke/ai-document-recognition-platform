import base64
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import DEFAULT_VLM_MODEL, MAX_WORKERS, PAGE_DIR_NAME, SILICONFLOW_API_URL, TEXT_DIR_NAME, VLM_DIR_NAME, VLM_MAX_TOKENS, VLM_TIMEOUT_SECONDS

VLM_PROMPT = """请作为“文档字段核对助手”处理这页扫描件。

你会同时收到：
1. 页面图片
2. 本页 OCR 文本

核对规则：
1. OCR 文本排版可能混乱，但数字、编号、日期、金额通常更准确。
2. OCR 文本只能作为“独立字符串候选池”使用。OCR 字符串之间的先后顺序、换行、相邻关系、上下文关系可能完全混乱，不要根据 OCR 排版推断字段归属或逻辑关系。
3. 字段含义、位置、归属、表格关系、甲乙方关系必须以页面图片中的视觉版式和明确标签为准；如果图片没有明确标签，不要根据 OCR 字符串位置强行推断。
4. 请根据页面图片判断字段含义、位置和归属，再优先从 OCR 文本中选取精确字段值。
5. 如果图片清晰显示 OCR 错误，可以修正，但必须写明 OCR 值、AI 核对值和差异原因。
6. 如果 OCR 与图片/AI 判断不一致、图片不清晰、存在多个候选值、字段归属不确定，必须标记“需要人工复核”。
7. 不要猜测，不要补全不存在的信息。
8. 不要使用你的训练知识、知识截止时间或当前日期去判断文件日期是否“未来日期”“过期”“逻辑矛盾”。例如 20260615 只能作为字符串/日期值记录；除非页面明确写出业务规则或两个页面字段直接冲突，否则不要评价日期合理性。

输出格式必须包含下面这些标题：
## 复核结论
复核状态：通过 或 需要人工复核
复核原因：如果需要人工复核，列出原因；否则写“未发现明显差异”

## 字段结果
字段包括但不限于：合同编号、项目名称、甲方、乙方、金额、日期、发票信息、清单/工程量、签字盖章情况。每个字段给出值、依据、是否来自 OCR、是否经过图片核对。

## 差异清单
只列出 OCR 文本与图片/AI 核对结果不一致或不确定的地方。没有差异则写“无明显差异”。
"""


class VlmService:
    def __init__(self, store, render_page, audit=None, extraction=None):
        self.store = store
        self.render_page = render_page
        self.audit = audit
        self.extraction = extraction
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def get_api_key(self, job):
        return job.get("_api_key", "")

    def job_limit(self, job):
        try:
            return max(1, min(MAX_WORKERS, int(job.get("vlm_workers", 2) or 2)))
        except (TypeError, ValueError):
            return 2

    def is_stale_running(self, item):
        return item.get("status") == "running" and time.time() - float(item.get("updated_at") or 0) > VLM_TIMEOUT_SECONDS + 60

    def page_ocr_text(self, job_id, page_no):
        text_path = self.store.job_path(job_id) / TEXT_DIR_NAME / f"page_{page_no:04d}.txt"
        if not text_path.exists():
            return ""
        return text_path.read_text(encoding="utf-8", errors="ignore")

    def prompt(self, keywords, ocr_text=""):
        keyword_text = "、".join(keywords or [])
        parts = [VLM_PROMPT]
        if keyword_text:
            parts.append(
                "本文件的筛选关键词/目标线索是："
                + keyword_text
                + "\n请优先围绕这些关键词识别相关字段、上下文、数值、日期、单位名称、编号和页面证据；如果关键词只是定位线索，也要说明它在本页出现的位置和含义。"
            )
        if ocr_text:
            parts.append("本页 OCR 文本如下，请将它作为核对底稿：\n```text\n" + ocr_text[:12000] + "\n```")
        else:
            parts.append("本页没有可用 OCR 文本，请仅根据图片识别，并在复核原因中说明。")
        return "\n\n".join(parts)

    def assess_review(self, text):
        review_required = False
        reasons = []
        ignored_temporal = ("未来日期", "知识截止", "训练", "数据库时间", "当前日期")
        for line in (text or "").splitlines():
            compact = re.sub(r"\s+", "", line)
            if "复核状态" in compact:
                if "需要人工复核" in compact:
                    review_required = True
                    reasons.append("AI 标记需要人工复核")
                elif "通过" in compact:
                    review_required = False
            if any(word in compact for word in ("OCR不一致", "识别不一致", "存在差异", "无法确认", "不确定", "看不清", "多个候选", "冲突")):
                review_required = True
                if not any(word in compact for word in ignored_temporal):
                    reasons.append(line.strip()[:120])
        deduped = []
        for reason in reasons:
            if reason and reason not in deduped:
                deduped.append(reason)
        return review_required, deduped[:5]

    def set_state(self, job_id, page_no, state):
        with self.store.lock:
            job = self.store.jobs[job_id]
            results = dict(job.get("vlm_results") or {})
            current = dict(results.get(str(page_no)) or {})
            current.update(state)
            current["updated_at"] = time.time()
            results[str(page_no)] = current
            job["vlm_results"] = results
            job["updated_at"] = time.time()
            self.store.save(job_id)

    def call(self, image_path, api_key, model, prompt):
        image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ]}],
            "temperature": 0,
            "max_tokens": VLM_MAX_TOKENS,
        }
        req = urllib.request.Request(
            SILICONFLOW_API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=VLM_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("choices", [{}])[0].get("message", {}).get("content", ""), data.get("usage", {})

    def run_page(self, job_id, page_no):
        job = self.store.load(job_id)
        if not job:
            return
        api_key = self.get_api_key(job)
        if not api_key:
            self.set_state(job_id, page_no, {"status": "not_configured", "message": "大模型未配置，仅支持文本识别"})
            return
        model = job.get("vlm_model") or DEFAULT_VLM_MODEL
        image_path = self.store.job_path(job_id) / PAGE_DIR_NAME / f"page_{page_no:04d}.png"
        try:
            self.set_state(job_id, page_no, {"status": "running", "model": model, "message": "AI 提取中"})
            self.render_page(Path(job.get("_pdf_path")), page_no - 1, image_path, job.get("dpi", 180))
            ocr_text = self.page_ocr_text(job_id, page_no)
            t0 = time.time()
            text, usage = self.call(image_path, api_key, model, self.prompt(job.get("keywords") or [], ocr_text))
            review_required, review_reasons = self.assess_review(text)
            result = {
                "status": "done",
                "model": model,
                "text": text,
                "usage": usage,
                "seconds": round(time.time() - t0, 2),
                "message": "完成",
                "review_required": review_required,
                "review_reasons": review_reasons,
                "ocr_attached": bool(ocr_text),
            }
            (self.store.job_path(job_id) / VLM_DIR_NAME / f"page_{page_no:04d}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            self.set_state(job_id, page_no, result)
            if self.audit:
                self.audit.write(job_id, "vlm_done", page_no, detail={"review_required": review_required, "seconds": result["seconds"]})
            if self.extraction:
                self.extraction.schedule(job_id, page_no)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:1000]
            self.set_state(job_id, page_no, {"status": "failed", "model": model, "message": f"HTTP {exc.code}: {detail}"})
            if self.audit:
                self.audit.write(job_id, "vlm_failed", page_no, detail={"model": model, "message": f"HTTP {exc.code}: {detail}"})
        except Exception as exc:
            self.set_state(job_id, page_no, {"status": "failed", "model": model, "message": str(exc)[:1000]})
            if self.audit:
                self.audit.write(job_id, "vlm_failed", page_no, detail={"model": model, "message": str(exc)[:1000]})

    def run_page_then_dispatch(self, job_id, page_no):
        try:
            self.run_page(job_id, page_no)
        finally:
            self.dispatch(job_id)

    def dispatch(self, job_id):
        job = self.store.load(job_id)
        if not job:
            return
        to_start = []
        with self.store.lock:
            job = self.store.jobs.get(job_id)
            if not job:
                return
            results = job.setdefault("vlm_results", {})
            stale_changed = False
            for page, item in list(results.items()):
                if self.is_stale_running(item):
                    stale = dict(item)
                    stale.update({"status": "failed", "message": "AI 提取超时，可重新提交", "updated_at": time.time()})
                    results[page] = stale
                    stale_changed = True
            limit = self.job_limit(job)
            running = sum(1 for item in results.values() if item.get("status") == "running")
            slots = max(0, limit - running)
            if not slots:
                if stale_changed:
                    job["updated_at"] = time.time()
                    self.store.save(job_id)
                return
            queued = sorted(
                int(page) for page, item in results.items()
                if item.get("status") == "queued" and str(page).isdigit()
            )
            for page_no in queued[:slots]:
                current = dict(results.get(str(page_no)) or {})
                current.update({"status": "running", "message": "AI 提取中", "updated_at": time.time()})
                results[str(page_no)] = current
                to_start.append(page_no)
            if stale_changed or to_start:
                job["updated_at"] = time.time()
                self.store.save(job_id)
        for page_no in to_start:
            self.executor.submit(self.run_page_then_dispatch, job_id, page_no)

    def schedule(self, job_id, page_no):
        job = self.store.load(job_id)
        if not job:
            return False, "任务不存在"
        if not self.get_api_key(job):
            self.set_state(job_id, page_no, {"status": "not_configured", "message": "大模型未配置，仅支持文本识别"})
            return False, "大模型未配置，仅支持文本识别"
        with self.store.lock:
            results = self.store.jobs[job_id].setdefault("vlm_results", {})
            current = results.get(str(page_no), {})
            if self.is_stale_running(current):
                current = dict(current)
                current.update({"status": "failed", "message": "AI 提取超时，可重新提交", "updated_at": time.time()})
                results[str(page_no)] = current
            if current.get("status") in {"queued", "running", "done"}:
                return True, current.get("status")
            results[str(page_no)] = {"status": "queued", "message": "已加入 AI 提取队列", "updated_at": time.time()}
            self.store.save(job_id)
        self.dispatch(job_id)
        return True, "queued"
