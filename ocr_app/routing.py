def decide_route(confidence, errors=None, warnings=None, low_confidence_fields=None, field_review_required=False):
    errors = errors or []
    warnings = warnings or []
    low_confidence_fields = low_confidence_fields or []
    if errors or confidence < 0.7:
        return "rejected"
    if warnings or low_confidence_fields or field_review_required or confidence < 0.9:
        return "human_review"
    return "auto_approve"
