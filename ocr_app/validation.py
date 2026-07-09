import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


KNOWN_FIELDS = {
    "invoice_number": "发票号",
    "date": "日期",
    "amount_excl_tax": "不含税金额",
    "tax_amount": "税额",
    "total_amount": "价税合计",
    "seller_name": "销售方",
    "seller_tax_id": "销售方税号",
    "buyer_name": "购买方",
    "buyer_tax_id": "购买方税号",
    "contract_number": "合同编号",
    "project_name": "项目名称",
    "party_a": "甲方",
    "party_b": "乙方",
    "amount": "金额",
    "signature_status": "签字盖章情况",
}

SOURCE_VALUES = {"ocr", "vlm", "manual", "unknown"}


def empty_result(confidence=0):
    return {
        "valid": False,
        "errors": [],
        "warnings": [],
        "routing": "human_review",
        "final_confidence": confidence,
    }


def normalize_amount(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("￥", "").replace("¥", "").replace("元", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def normalize_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = text.translate(str.maketrans({"年": "-", "月": "-", "日": "", ".": "-", "/": "-"})).strip("-")
    text = re.sub(r"-+", "-", text)
    candidates = [text]
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        candidates.append(f"{digits[:4]}-{digits[4:6]}-{digits[6:]}")
    for candidate in candidates:
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
    return None


def normalize_field(raw):
    if isinstance(raw, dict):
        value = raw.get("value", "")
        source = raw.get("source", "unknown")
        needs_review = bool(raw.get("needs_review", False))
        confidence = raw.get("confidence", None)
        ocr_value = raw.get("ocr_value", "")
        vlm_value = raw.get("vlm_value", "")
    else:
        value = raw
        source = "unknown"
        needs_review = False
        confidence = None
        ocr_value = ""
        vlm_value = ""
    source = source if source in SOURCE_VALUES else "unknown"
    try:
        confidence = None if confidence is None else max(0, min(1, float(confidence)))
    except (TypeError, ValueError):
        confidence = None
    review_reason = raw.get("review_reason", "") if isinstance(raw, dict) else ""
    status = raw.get("status", "") if isinstance(raw, dict) else ""
    evidence = raw.get("evidence", {}) if isinstance(raw, dict) else {}
    return {
        "value": "" if value is None else str(value).strip(),
        "source": source,
        "needs_review": needs_review,
        "confidence": confidence,
        "ocr_value": "" if ocr_value is None else str(ocr_value).strip(),
        "vlm_value": "" if vlm_value is None else str(vlm_value).strip(),
        "evidence": evidence if isinstance(evidence, dict) else {},
        "status": status,
        "review_reason": "" if review_reason is None else str(review_reason).strip(),
    }


def normalize_candidate(raw):
    if not isinstance(raw, dict):
        raw = {"value": raw}
    try:
        confidence = max(0, min(1, float(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0
    source = raw.get("source", "unknown")
    candidate = {
        "value": "" if raw.get("value") is None else str(raw.get("value")).strip(),
        "recognized_as": "" if raw.get("recognized_as") is None else str(raw.get("recognized_as")).strip(),
        "source": source if source in SOURCE_VALUES else "unknown",
        "confidence": confidence,
        "evidence": "" if raw.get("evidence") is None else str(raw.get("evidence")).strip(),
        "ocr_value": "" if raw.get("ocr_value") is None else str(raw.get("ocr_value")).strip(),
        "vlm_value": "" if raw.get("vlm_value") is None else str(raw.get("vlm_value")).strip(),
        "selected": bool(raw.get("selected", False)),
        "review_reason": "" if raw.get("review_reason") is None else str(raw.get("review_reason")).strip(),
    }
    if isinstance(raw.get("bbox"), list):
        candidate["bbox"] = raw.get("bbox")
    try:
        candidate["ocr_score"] = max(0, min(1, float(raw.get("ocr_score", 0))))
    except (TypeError, ValueError):
        candidate["ocr_score"] = 0
    return candidate


def candidate_rank(candidate, label):
    recognized = (candidate.get("recognized_as") or "").replace(" ", "")
    compact_label = str(label or "").replace(" ", "")
    semantic = 0
    if recognized and compact_label and (compact_label in recognized or recognized in compact_label):
        semantic = 0.18
    ocr_support = 0.08 if candidate.get("ocr_value") or candidate.get("bbox") else 0
    ocr_score = float(candidate.get("ocr_score") or 0) * 0.06
    return float(candidate.get("confidence") or 0) + semantic + ocr_support + ocr_score


def normalize_template_field(raw, label):
    raw = raw if isinstance(raw, dict) else {}
    candidates = [normalize_candidate(item) for item in (raw.get("candidates") or []) if isinstance(item, (dict, str, int, float))]
    candidates = [item for item in candidates if item.get("value")]
    candidates.sort(key=lambda item: candidate_rank(item, label), reverse=True)
    selected_index = raw.get("selected_candidate_index", None)
    try:
        selected_index = None if selected_index is None else int(selected_index)
    except (TypeError, ValueError):
        selected_index = None
    final_value = "" if raw.get("final_value") is None else str(raw.get("final_value")).strip()
    review_status = str(raw.get("review_status") or "pending").strip()
    return {
        "label": str(raw.get("label") or label).strip(),
        "keyword": str(raw.get("keyword") or label).strip(),
        "final_value": final_value,
        "review_status": review_status,
        "selected_candidate_index": selected_index,
        "candidates": candidates,
        "manual_value": "" if raw.get("manual_value") is None else str(raw.get("manual_value")).strip(),
        "review_reason": "" if raw.get("review_reason") is None else str(raw.get("review_reason")).strip(),
    }


def normalize_extracted(raw, page_no=None, raw_vlm_text=""):
    if not isinstance(raw, dict):
        raw = {}
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    template_fields = raw.get("template_fields") if isinstance(raw.get("template_fields"), dict) else {}
    normalized_fields = {}
    for key, value in fields.items():
        safe_key = re.sub(r"[^A-Za-z0-9_]+", "_", str(key)).strip("_")
        if safe_key:
            normalized_fields[safe_key] = normalize_field(value)
    normalized_template_fields = {}
    for key, value in template_fields.items():
        label = str(key).strip()
        if label:
            normalized_template_fields[label] = normalize_template_field(value, label)
    try:
        confidence = max(0, min(1, float(raw.get("extraction_confidence", raw.get("confidence", 0)))))
    except (TypeError, ValueError):
        confidence = 0
    return {
        "status": raw.get("status", "done"),
        "page_no": page_no or raw.get("page_no"),
        "doc_type": str(raw.get("doc_type") or "未知文档").strip(),
        "fields": normalized_fields,
        "template_fields": normalized_template_fields,
        "final_fields": raw.get("final_fields") if isinstance(raw.get("final_fields"), dict) else {},
        "extraction_confidence": confidence,
        "retry_count": int(raw.get("retry_count") or 0),
        "raw_vlm_text": raw.get("raw_vlm_text") or raw_vlm_text or "",
    }


def validate_document(document, existing_documents=None):
    from .routing import decide_route
    from .rules import evaluate_business_rules, rule, summarize_rules

    result = empty_result(float(document.get("extraction_confidence") or 0))
    fields = document.get("fields") or {}
    template_fields = document.get("template_fields") or {}
    field_review_required = False
    low_confidence_fields = []

    if template_fields:
        rule_results = []
        final_fields = {}
        confidences = []
        for name, field in template_fields.items():
            candidates = field.get("candidates") or []
            best = candidates[0] if candidates else None
            selected_index = field.get("selected_candidate_index")
            selected = candidates[selected_index] if isinstance(selected_index, int) and 0 <= selected_index < len(candidates) else None
            if not field.get("final_value") and selected:
                field["final_value"] = selected.get("value", "")
            if not field.get("final_value") and len(candidates) == 1 and best and best.get("confidence", 0) >= 0.9:
                recognized = (best.get("recognized_as") or "").replace(" ", "")
                compact_name = str(name).replace(" ", "")
                if compact_name and (compact_name in recognized or recognized in compact_name):
                    field["final_value"] = best.get("value", "")
                    field["selected_candidate_index"] = 0
                    field["review_status"] = "auto_selected"
                    best["selected"] = True
            if not candidates:
                rule_results.append(rule("template_missing_candidate", f"{name} 未找到候选值", "warning", name))
                field_review_required = True
            if len(candidates) > 1:
                rule_results.append(rule("template_multiple_candidates", f"{name} 有多个候选值，需要人工选择", "warning", name))
                field_review_required = True
            if best and best.get("confidence", 0) < 0.75:
                low_confidence_fields.append(name)
                rule_results.append(rule("template_low_confidence", f"{name} 最高候选置信度低", "warning", name))
                field_review_required = True
            if best and best.get("recognized_as"):
                recognized = best.get("recognized_as", "").replace(" ", "")
                compact_name = str(name).replace(" ", "")
                if compact_name and recognized and compact_name not in recognized and recognized not in compact_name:
                    rule_results.append(rule("template_semantic_mismatch", f"{name} 的候选被识别为 {best.get('recognized_as')}", "warning", name))
                    field_review_required = True
            if not field.get("final_value"):
                field["review_status"] = "pending"
                field_review_required = True
            elif field.get("review_status") not in {"accepted", "manual", "auto_selected"}:
                field["review_status"] = "accepted"
            if field.get("final_value"):
                final_fields[name] = field.get("final_value", "")
            if best:
                confidences.append(float(best.get("confidence") or 0))
        errors, warnings, rule_hits = summarize_rules(rule_results)
        confidence = min(confidences) if confidences else 0
        document["extraction_confidence"] = confidence
        document["final_fields"] = final_fields
        if errors:
            routing = "rejected"
        elif warnings or low_confidence_fields or field_review_required:
            routing = "human_review"
        else:
            routing = "auto_approve"
        result.update({
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "rules": rule_results,
            "rule_hits": rule_hits,
            "low_confidence_fields": low_confidence_fields,
            "routing": routing,
            "final_confidence": confidence,
        })
        return result

    for name, field in fields.items():
        confidence = field.get("confidence")
        if confidence is not None and confidence < 0.75:
            low_confidence_fields.append(name)
            field["needs_review"] = True
            if not field.get("review_reason"):
                field["review_reason"] = "字段置信度低"
        if field.get("needs_review"):
            field_review_required = True
        if not field.get("value"):
            field["status"] = "missing"
            field["needs_review"] = True
            field_review_required = True
            if not field.get("review_reason"):
                field["review_reason"] = "字段缺失"
        elif field.get("needs_review"):
            field["status"] = "review"
        else:
            field["status"] = "auto"

    rule_results = evaluate_business_rules(document, existing_documents)
    errors, warnings, rule_hits = summarize_rules(rule_results)
    confidence = float(document.get("extraction_confidence") or 0)
    routing = decide_route(confidence, errors, warnings, low_confidence_fields, field_review_required)
    result.update({
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "rules": rule_results,
        "rule_hits": rule_hits,
        "low_confidence_fields": low_confidence_fields,
        "routing": routing,
        "final_confidence": confidence,
    })
    return result
