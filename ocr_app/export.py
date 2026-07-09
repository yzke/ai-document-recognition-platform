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
        final_fields = {}
        has_template_result = False
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
            if item.get("template_fields"):
                has_template_result = True
                fields = item.get("final_fields") if isinstance(item.get("final_fields"), dict) else {}
                if not fields:
                    fields = {
                        key: field.get("final_value", "")
                        for key, field in (item.get("template_fields") or {}).items()
                        if isinstance(field, dict)
                    }
                for key, value in fields.items():
                    if value and not final_fields.get(key):
                        final_fields[key] = value
                if not include_review:
                    continue
            else:
                fields = {
                    key: field.get("value", "")
                    for key, field in (item.get("fields") or {}).items()
                    if isinstance(field, dict)
                }
            pages.append({
                "page_no": item.get("page_no"),
                "doc_type": item.get("doc_type"),
                "routing": routing,
                "manual_status": manual_status,
                "fields": fields,
                "confidence": item.get("extraction_confidence", 0),
                "needs_human_review": routing == "human_review",
            })
        if has_template_result:
            self.audit.write(job_id, "export", detail={"pages": len(pages), "include_review": include_review, "template_final": True})
            if not include_review:
                return final_fields
            return {
                "export_time": datetime.now().isoformat(timespec="seconds"),
                "job_id": job_id,
                "source_file": job.get("filename", ""),
                "final_fields": final_fields,
                "pages": pages,
            }
        payload = {
            "export_time": datetime.now().isoformat(timespec="seconds"),
            "job_id": job_id,
            "source_file": job.get("filename", ""),
            "pages": pages,
        }
        self.audit.write(job_id, "export", detail={"pages": len(pages), "include_review": include_review})
        return payload
