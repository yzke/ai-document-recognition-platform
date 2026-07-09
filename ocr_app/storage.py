import json
import re
import threading
import time
from pathlib import Path

from .config import DATA_DIR, JOB_DIR, KEYWORD_HISTORY_PATH
from .local_settings import get_local_api_key


def ensure_dirs():
    JOB_DIR.mkdir(parents=True, exist_ok=True)


def safe_name(name):
    base = Path(name).name
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", base)[:120] or "upload.pdf"


def parse_keywords(raw):
    return [p.strip() for p in re.split(r"[,，、;；\n\r\t ]+", raw or "") if p.strip()]


def normalize_keyword_string(keywords):
    if isinstance(keywords, list):
        raw = ",".join(keywords)
    else:
        raw = keywords or ""
    return ",".join(parse_keywords(raw))


def compute_elapsed(job):
    started = job.get("started_at") or job.get("created_at")
    if not started:
        return 0
    end = job.get("finished_at") or time.time()
    return max(0, round(end - started, 1))


class JobStore:
    def __init__(self):
        ensure_dirs()
        self.jobs = {}
        self.lock = threading.Lock()
        self.cancel_events = {}

    def job_path(self, job_id):
        return JOB_DIR / job_id

    def state_path(self, job_id):
        return self.job_path(job_id) / "state.json"

    def public_job(self, job):
        public = {k: v for k, v in job.items() if not k.startswith("_")}
        public["elapsed_seconds"] = compute_elapsed(job)
        public["vlm_configured"] = bool(job.get("_api_key") or get_local_api_key())
        return public

    def save(self, job_id):
        self.state_path(job_id).write_text(
            json.dumps(self.public_job(self.jobs[job_id]), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def create(self, job_id, job):
        with self.lock:
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            self.save(job_id)

    def load(self, job_id):
        if job_id not in self.jobs and self.state_path(job_id).exists():
            with self.lock:
                job = json.loads(self.state_path(job_id).read_text(encoding="utf-8"))
                # Private fields are not persisted. Restore PDF path by filename.
                job["_pdf_path"] = str(self.job_path(job_id) / job.get("filename", ""))
                job["_api_key"] = get_local_api_key()
                self.jobs[job_id] = job
                self.cancel_events.setdefault(job_id, threading.Event())
        return self.jobs.get(job_id)

    def update(self, job_id, **fields):
        with self.lock:
            self.jobs[job_id].update(fields)
            self.jobs[job_id]["updated_at"] = time.time()
            if fields.get("status") in {"done", "failed", "stopped"} and not self.jobs[job_id].get("finished_at"):
                self.jobs[job_id]["finished_at"] = time.time()
            self.save(job_id)

    def update_extracted(self, job_id, page_no, summary):
        with self.lock:
            job = self.jobs[job_id]
            results = dict(job.get("extracted_results") or {})
            current = dict(results.get(str(page_no)) or {})
            current.update(summary)
            current["page"] = page_no
            results[str(page_no)] = current
            summary_counts = {"auto_approve": 0, "human_review": 0, "rejected": 0, "failed": 0}
            low_confidence_fields = {}
            rule_hits = {}
            for item in results.values():
                routing = item.get("routing")
                status = item.get("status")
                if status == "failed":
                    summary_counts["failed"] += 1
                elif routing in summary_counts:
                    summary_counts[routing] += 1
                for field in item.get("low_confidence_fields") or []:
                    low_confidence_fields[field] = low_confidence_fields.get(field, 0) + 1
                for code, count in (item.get("rule_hits") or {}).items():
                    rule_hits[code] = rule_hits.get(code, 0) + int(count or 0)
            job["extracted_results"] = results
            job["extraction_summary"] = summary_counts
            job["quality_summary"] = {
                "routing": summary_counts,
                "low_confidence_fields": low_confidence_fields,
                "rule_hits": rule_hits,
            }
            job["updated_at"] = time.time()
            self.save(job_id)

    def set_cancelled(self, job_id):
        self.cancel_events.setdefault(job_id, threading.Event()).set()

    def list_jobs(self, limit=30):
        ensure_dirs()
        rows = []
        for state_file in JOB_DIR.glob("*/state.json"):
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows.append({
                "id": state.get("id", state_file.parent.name),
                "filename": state.get("filename", ""),
                "status": state.get("status", ""),
                "created_at": state.get("created_at", 0),
                "updated_at": state.get("updated_at", 0),
                "total_pages": state.get("total_pages", 0),
                "done_pages": state.get("done_pages", 0),
                "candidate_count": len(state.get("candidate_pages") or []),
                "elapsed_seconds": state.get("elapsed_seconds", 0),
            })
        rows.sort(key=lambda r: r["updated_at"] or r["created_at"], reverse=True)
        return rows[:limit]


def record_keyword_history(keywords):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    text = normalize_keyword_string(keywords)
    if not text:
        return
    history = []
    if KEYWORD_HISTORY_PATH.exists():
        try:
            history = json.loads(KEYWORD_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history = [item for item in history if item.get("keywords") != text]
    history.insert(0, {"keywords": text, "updated_at": time.time()})
    KEYWORD_HISTORY_PATH.write_text(json.dumps(history[:30], ensure_ascii=False, indent=2), encoding="utf-8")


def get_keyword_history():
    if not KEYWORD_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(KEYWORD_HISTORY_PATH.read_text(encoding="utf-8"))[:30]
    except Exception:
        return []
