import json
import time


class AuditLog:
    def __init__(self, store):
        self.store = store

    def path(self, job_id):
        return self.store.job_path(job_id) / "audit.jsonl"

    def write(self, job_id, event_type, page_no=None, actor="system", detail=None):
        row = {
            "event_time": time.time(),
            "event_type": event_type,
            "job_id": job_id,
            "page_no": page_no,
            "actor": actor,
            "detail": detail or {},
        }
        path = self.path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def read(self, job_id, limit=500):
        path = self.path(job_id)
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]
