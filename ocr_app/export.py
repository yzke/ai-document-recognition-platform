import json
from datetime import datetime

from .extraction import EXTRACT_DIR_NAME


class ExportService:
    def __init__(self, store, audit):
        self.store = store
        self.audit = audit

    def export_job(self, job_id, include_review=False):
        job = self.store.load(job_id)
        if not job:
            raise ValueError("任务不存在")
        pages = []
        extracted_dir = self.store.job_path(job_id) / EXTRACT_DIR_NAME
        for path in sorted(extracted_dir.glob("page_*.json")) if extracted_dir.exists() else []:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            routing = item.get("routing")
            manual_status = item.get("manual_status")
            approved = routing == "auto_approve" or manual_status == "approved"
            if not approved and not include_review:
                continue
            fields = {
                key: field.get("value", "")
                for key, field in (item.get("fields") or {}).items()
                if isinstance(field, dict)
            }
            pages.append({
                "page_no": item.get("page_no"),
                "doc_type": item.get("doc_type"),
                "routing": routing,
                "fields": fields,
                "confidence": item.get("extraction_confidence", 0),
                "needs_human_review": routing == "human_review",
            })
        payload = {
            "export_time": datetime.now().isoformat(timespec="seconds"),
            "job_id": job_id,
            "source_file": job.get("filename", ""),
            "pages": pages,
        }
        self.audit.write(job_id, "export", detail={"pages": len(pages), "include_review": include_review})
        return payload
