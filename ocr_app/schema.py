from jsonschema import Draft202012Validator


def build_template_schema(template_fields):
    field_names = list((template_fields or {}).keys())
    field_schema = {
        "type": "object",
        "required": ["label", "keyword", "final_value", "review_status", "selected_candidate_index", "candidates"],
        "properties": {
            "label": {"type": "string"},
            "keyword": {"type": "string"},
            "final_value": {"type": "string"},
            "review_status": {"type": "string"},
            "selected_candidate_index": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["value", "recognized_as", "source", "confidence", "evidence"],
                    "properties": {
                        "value": {"type": "string"},
                        "recognized_as": {"type": "string"},
                        "source": {"enum": ["ocr", "vlm", "manual", "unknown"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence": {"type": "string"},
                        "ocr_value": {"type": "string"},
                        "vlm_value": {"type": "string"},
                        "ocr_score": {"type": "number", "minimum": 0, "maximum": 1},
                        "bbox": {"type": "array"},
                        "selected": {"type": "boolean"},
                        "review_reason": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "manual_value": {"type": "string"},
            "review_reason": {"type": "string"},
        },
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "required": ["doc_type", "template_fields"],
        "properties": {
            "doc_type": {"type": "string"},
            "template_fields": {
                "type": "object",
                "required": field_names,
                "properties": {name: field_schema for name in field_names},
                "additionalProperties": False,
            },
            "extraction_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "additionalProperties": True,
    }


def validate_template_payload(payload, template_fields):
    schema = build_template_schema(template_fields)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    if not errors:
        return []
    rows = []
    for error in errors:
        path = ".".join(str(part) for part in error.path) or "$"
        rows.append(f"{path}: {error.message}")
    return rows
