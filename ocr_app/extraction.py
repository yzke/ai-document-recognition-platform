import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .config import EXTRACTION_MAX_TOKENS, EXTRACTION_MODEL, EXTRACTION_TIMEOUT_SECONDS, SILICONFLOW_API_URL
from .validation import normalize_extracted, validate_document

EXTRACT_DIR_NAME = "extracted"


EXTRACTION_PROMPT = """你是企业文档结构化抽取引擎。请只输出合法 JSON，不要输出 Markdown。

根据 VLM 复核报告和 OCR 文本抽取字段。无法确认的字段不要编造；如有冲突或不确定，将 needs_review 设为 true。

JSON 格式：
{
  "doc_type": "文档类型",
  "fields": {
    "invoice_number": {"value": "", "source": "ocr|vlm|unknown", "ocr_value": "", "vlm_value": "", "needs_review": false, "confidence": 0.0},
    "date": {"value": "YYYY-MM-DD 或原文", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0},
    "amount_excl_tax": {"value": "", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0},
    "tax_amount": {"value": "", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0},
    "total_amount": {"value": "", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0},
    "seller_name": {"value": "", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0},
    "seller_tax_id": {"value": "", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0},
    "buyer_name": {"value": "", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0},
    "buyer_tax_id": {"value": "", "source": "ocr|vlm|unknown", "needs_review": false, "confidence": 0.0}
  },
  "extraction_confidence": 0.0
}

字段不适用时可以省略。source 只能是 ocr、vlm 或 unknown。confidence 取 0 到 1。
"""


class ExtractionService:
    def __init__(self, store, audit, samples=None):
        self.store = store
        self.audit = audit
        self.samples = samples
        self.executor = ThreadPoolExecutor(max_workers=2)

    def page_ocr_text(self, job_id, page_no):
        path = self.store.job_path(job_id) / "texts" / f"page_{page_no:04d}.txt"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")

    def page_ocr_records(self, job_id, page_no):
        path = self.store.job_path(job_id) / "texts" / f"page_{page_no:04d}.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def attach_field_evidence(self, job_id, document):
        page_no = document.get("page_no")
        records = self.page_ocr_records(job_id, page_no)
        fields = document.get("fields") or {}
        for name, field in fields.items():
            value = str(field.get("value") or "").strip()
            best = None
            if value:
                compact_value = re.sub(r"\s+", "", value)
                for record in records:
                    text = str(record.get("text") or "").strip()
                    compact_text = re.sub(r"\s+", "", text)
                    if compact_value and (compact_value in compact_text or compact_text in compact_value):
                        best = record
                        break
            if best:
                field["evidence"] = {
                    "page_no": page_no,
                    "ocr_text": best.get("text", ""),
                    "bbox": best.get("box") or [],
                    "ocr_score": best.get("score", 0),
                }
                if field.get("confidence") is None:
                    try:
                        field["confidence"] = max(0, min(1, float(best.get("score", 0))))
                    except (TypeError, ValueError):
                        field["confidence"] = 0.75
            else:
                field.setdefault("evidence", {"page_no": page_no, "ocr_text": "", "bbox": [], "ocr_score": 0})
                if field.get("confidence") is None:
                    field["confidence"] = 0.7 if field.get("source") == "vlm" else 0.55
            if field.get("confidence", 0) < 0.75:
                field["needs_review"] = True
                field.setdefault("review_reason", "缺少可靠 OCR 证据或字段置信度低")
        return document

    def extracted_dir(self, job_id):
        path = self.store.job_path(job_id) / EXTRACT_DIR_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    def extracted_path(self, job_id, page_no):
        return self.extracted_dir(job_id) / f"page_{page_no:04d}.json"

    def list_existing(self, job_id):
        rows = []
        for path in self.extracted_dir(job_id).glob("page_*.json"):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return rows

    def extract_json_fragment(self, text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError("LLM 未返回合法 JSON")

    def call_llm(self, api_key, model, vlm_text, ocr_text, error_hint=""):
        prompt = EXTRACTION_PROMPT
        if error_hint:
            prompt += "\n上一次输出解析失败，请修复为严格 JSON。错误：" + error_hint[:500]
        prompt += "\n\nVLM 复核报告：\n" + (vlm_text or "")[:12000]
        prompt += "\n\nOCR 文本：\n" + (ocr_text or "")[:12000]
        payload = {
            "model": model or EXTRACTION_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": EXTRACTION_MAX_TOKENS,
        }
        req = urllib.request.Request(
            SILICONFLOW_API_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=EXTRACTION_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    def fallback(self, page_no, vlm_text, reason, retry_count=0):
        return {
            "status": "failed",
            "page_no": page_no,
            "doc_type": "未知文档",
            "fields": {},
            "extraction_confidence": 0,
            "retry_count": retry_count,
            "raw_vlm_text": vlm_text or "",
            "validation": {
                "valid": False,
                "errors": [reason],
                "warnings": [],
                "routing": "rejected",
                "final_confidence": 0,
            },
            "routing": "rejected",
            "updated_at": time.time(),
        }

    def run_page(self, job_id, page_no):
        job = self.store.load(job_id)
        if not job:
            return None
        vlm_result = (job.get("vlm_results") or {}).get(str(page_no)) or {}
        vlm_text = vlm_result.get("text", "")
        api_key = job.get("_api_key", "")
        if not vlm_text:
            result = self.fallback(page_no, "", "没有可用 VLM 报告")
            self.save_result(job_id, page_no, result)
            return result
        if not api_key:
            result = self.fallback(page_no, vlm_text, "大模型未配置，无法结构化提取")
            self.save_result(job_id, page_no, result)
            return result

        ocr_text = self.page_ocr_text(job_id, page_no)
        model = EXTRACTION_MODEL
        last_error = ""
        for attempt in range(3):
            try:
                raw = self.call_llm(api_key, model, vlm_text, ocr_text, last_error)
                parsed = self.extract_json_fragment(raw)
                document = normalize_extracted(parsed, page_no=page_no, raw_vlm_text=vlm_text)
                self.attach_field_evidence(job_id, document)
                document["retry_count"] = attempt
                validation = validate_document(document, self.list_existing(job_id))
                document["validation"] = validation
                document["routing"] = validation["routing"]
                document["status"] = "done"
                document["updated_at"] = time.time()
                self.save_result(job_id, page_no, document)
                self.audit.write(job_id, "extraction_done", page_no, detail={"routing": document["routing"], "confidence": document["extraction_confidence"]})
                self.audit.write(job_id, "validation_done", page_no, detail=validation)
                return document
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")[:1000]
                last_error = f"HTTP {exc.code}: {detail}"
                break
            except Exception as exc:
                last_error = str(exc)
                break
        result = self.fallback(page_no, vlm_text, last_error or "结构化提取失败", retry_count=2)
        self.save_result(job_id, page_no, result)
        self.audit.write(job_id, "extraction_failed", page_no, detail={"routing": result["routing"], "error": last_error})
        return result

    def save_result(self, job_id, page_no, result):
        self.extracted_path(job_id, page_no).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        validation = result.get("validation") or {}
        self.store.update_extracted(job_id, page_no, {
            "status": result.get("status"),
            "routing": result.get("routing"),
            "doc_type": result.get("doc_type"),
            "confidence": result.get("extraction_confidence", 0),
            "low_confidence_fields": validation.get("low_confidence_fields") or [],
            "rule_hits": validation.get("rule_hits") or {},
            "errors": validation.get("errors") or [],
            "warnings": validation.get("warnings") or [],
            "updated_at": result.get("updated_at", time.time()),
        })

    def load_result(self, job_id, page_no):
        path = self.extracted_path(job_id, page_no)
        if not path.exists():
            return {"status": "none", "message": "尚未结构化提取"}
        return json.loads(path.read_text(encoding="utf-8"))

    def update_fields(self, job_id, page_no, fields, reason=""):
        result = self.load_result(job_id, page_no)
        if result.get("status") == "none":
            raise ValueError("结构化结果不存在")
        before = result.get("fields") or {}
        merged = dict(before)
        for key, value in (fields or {}).items():
            current = dict(merged.get(key) or {})
            previous = dict(current)
            if isinstance(value, dict):
                current.update(value)
            else:
                current["value"] = value
            current["source"] = "manual"
            current["needs_review"] = False
            current["status"] = "manual"
            current["review_reason"] = reason
            merged[key] = current
            if self.samples and previous.get("value") != current.get("value"):
                self.samples.write_field_change(
                    job_id,
                    page_no,
                    key,
                    previous,
                    current,
                    reason=reason,
                    ocr_text=self.page_ocr_text(job_id, page_no),
                    vlm_text=result.get("raw_vlm_text", ""),
                    doc_type=result.get("doc_type", ""),
                )
        result["fields"] = merged
        result["validation"] = validate_document(result, self.list_existing(job_id))
        result["routing"] = result["validation"]["routing"]
        result["updated_at"] = time.time()
        self.save_result(job_id, page_no, result)
        self.audit.write(job_id, "human_edit", page_no, actor="user", detail={"before": before, "after": merged})
        return result

    def set_manual_status(self, job_id, page_no, status, reason=""):
        result = self.load_result(job_id, page_no)
        if result.get("status") == "none":
            raise ValueError("结构化结果不存在")
        result["manual_status"] = status
        result["manual_reason"] = reason
        result["routing"] = "auto_approve" if status == "approved" else "rejected"
        result["updated_at"] = time.time()
        self.save_result(job_id, page_no, result)
        self.audit.write(job_id, "human_approve" if status == "approved" else "human_reject", page_no, actor="user", detail={"routing": result["routing"], "reason": reason})
        return result
    def schedule(self, job_id, page_no):
        self.executor.submit(self.run_page, job_id, page_no)
