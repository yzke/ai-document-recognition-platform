import json

from .config import DEFAULT_VLM_MODEL, EXTRACTION_MODEL, EXTRACTION_MODELS, LOCAL_SETTINGS_PATH, VLM_MODELS


def load_local_settings():
    if not LOCAL_SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(LOCAL_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_local_settings(api_key=None, vlm_model=None, extraction_model=None):
    current = load_local_settings()
    if api_key is not None:
        api_key = str(api_key).strip()
        if api_key:
            current["api_key"] = api_key
    if vlm_model is not None:
        current["vlm_model"] = vlm_model if vlm_model in VLM_MODELS else DEFAULT_VLM_MODEL
    if extraction_model is not None:
        current["extraction_model"] = extraction_model if extraction_model in EXTRACTION_MODELS else str(extraction_model or EXTRACTION_MODEL).strip()
    LOCAL_SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def get_local_api_key():
    return str(load_local_settings().get("api_key") or "").strip()


def get_local_vlm_model():
    model = load_local_settings().get("vlm_model") or DEFAULT_VLM_MODEL
    return model if model in VLM_MODELS else DEFAULT_VLM_MODEL


def get_local_extraction_model():
    model = str(load_local_settings().get("extraction_model") or EXTRACTION_MODEL).strip()
    return model or EXTRACTION_MODEL
