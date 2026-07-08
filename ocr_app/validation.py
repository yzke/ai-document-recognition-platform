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


def normalize_extracted(raw, page_no=None, raw_vlm_text=""):
    if not isinstance(raw, dict):
        raw = {}
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    normalized_fields = {}
    for key, value in fields.items():
        safe_key = re.sub(r"[^A-Za-z0-9_]+", "_", str(key)).strip("_")
        if safe_key:
            normalized_fields[safe_key] = normalize_field(value)
    try:
        confidence = max(0, min(1, float(raw.get("extraction_confidence", raw.get("confidence", 0)))))
    except (TypeError, ValueError):
        confidence = 0
    return {
        "status": raw.get("status", "done"),
        "page_no": page_no or raw.get("page_no"),
        "doc_type": str(raw.get("doc_type") or "未知文档").strip(),
        "fields": normalized_fields,
        "extraction_confidence": confidence,
        "retry_count": int(raw.get("retry_count") or 0),
        "raw_vlm_text": raw.get("raw_vlm_text") or raw_vlm_text or "",
    }


def validate_document(document, existing_documents=None):
    from .routing import decide_route
    from .rules import evaluate_business_rules, summarize_rules

    result = empty_result(float(document.get("extraction_confidence") or 0))
    fields = document.get("fields") or {}
    field_review_required = False
    low_confidence_fields = []

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
