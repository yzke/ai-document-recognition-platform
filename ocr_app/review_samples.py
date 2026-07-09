import json
import time

from .config import DATA_DIR


class ReviewSamples:
    def __init__(self, store):
        self.store = store
        self.path = DATA_DIR / "review_samples.jsonl"

    def write_field_change(self, job_id, page_no, field, before, after, reason="", ocr_text="", vlm_text="", doc_type=""):
        model_value = (before or {}).get("value", "")
        manual_value = (after or {}).get("value", "")
        candidates = []
        if isinstance(before, dict) and isinstance(before.get("candidates"), list):
            candidates = before.get("candidates")[:10]
            selected_index = after.get("selected_candidate_index") if isinstance(after, dict) else None
            if isinstance(selected_index, int) and 0 <= selected_index < len(candidates):
                model_value = candidates[selected_index].get("value", model_value)
        if isinstance(after, dict) and after.get("final_value") is not None:
            manual_value = after.get("final_value", "")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": time.time(),
            "job_id": job_id,
            "page_no": page_no,
            "field": field,
            "model_value": model_value,
            "manual_value": manual_value,
            "candidates": candidates,
            "reason": reason,
            "doc_type": doc_type,
            "ocr_text": (ocr_text or "")[:12000],
            "vlm_text": (vlm_text or "")[:12000],
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def read(self, limit=500):
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]
