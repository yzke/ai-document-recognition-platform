import re
from decimal import Decimal

from .validation import KNOWN_FIELDS, normalize_amount, normalize_date


def rule(code, message, severity="warning", field=None):
    return {"code": code, "message": message, "severity": severity, "field": field}


def evaluate_business_rules(document, existing_documents=None):
    fields = document.get("fields") or {}
    rules = []

    total = normalize_amount((fields.get("total_amount") or fields.get("amount") or {}).get("value"))
    amount_excl_tax = normalize_amount((fields.get("amount_excl_tax") or {}).get("value"))
    tax_amount = normalize_amount((fields.get("tax_amount") or {}).get("value"))

    if "total_amount" in fields or "amount" in fields:
        if total is None:
            rules.append(rule("amount_invalid", "金额无法解析", "error", "total_amount"))
    if "date" in fields:
        normalized = normalize_date((fields.get("date") or {}).get("value"))
        if normalized is None:
            rules.append(rule("date_invalid", "日期格式不合法", "error", "date"))
        else:
            fields["date"]["value"] = normalized
    if amount_excl_tax is not None and tax_amount is not None and total is not None:
        if abs((amount_excl_tax + tax_amount) - total) > Decimal("0.02"):
            rules.append(rule("amount_cross_check", "金额交叉校验不一致", "warning", "total_amount"))

    for tax_key in ("seller_tax_id", "buyer_tax_id"):
        value = (fields.get(tax_key) or {}).get("value", "")
        compact = re.sub(r"\s+", "", value)
        if compact and not re.fullmatch(r"[0-9A-Z]{15}|[0-9A-Z]{18}|[0-9A-Z]{20}", compact):
            rules.append(rule("tax_id_suspicious", f"{KNOWN_FIELDS[tax_key]} 格式可疑", "warning", tax_key))

    invoice_number = (fields.get("invoice_number") or {}).get("value", "").strip()
    if invoice_number and existing_documents:
        for item in existing_documents:
            if item.get("page_no") == document.get("page_no"):
                continue
            other = ((item.get("fields") or {}).get("invoice_number") or {}).get("value", "").strip()
            if other and other == invoice_number:
                rules.append(rule("duplicate_invoice", "同任务内发票号重复", "warning", "invoice_number"))
                break

    return rules


def summarize_rules(rules):
    errors = [item["message"] for item in rules if item.get("severity") == "error"]
    warnings = [item["message"] for item in rules if item.get("severity") != "error"]
    hits = {}
    for item in rules:
        hits[item["code"]] = hits.get(item["code"], 0) + 1
    return errors, warnings, hits
