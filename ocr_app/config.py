import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
JOB_DIR = DATA_DIR / "jobs"
KEYWORD_HISTORY_PATH = DATA_DIR / "keyword_history.json"
LOCAL_SETTINGS_PATH = BASE_DIR / "local_settings.json"

PAGE_DIR_NAME = "pages"
TEXT_DIR_NAME = "texts"
VLM_DIR_NAME = "vlm"

DEFAULT_KEYWORDS = "合同,协议,金额,价款,甲方,乙方,发票,清单,项目,编号,日期,盖章,签字"
CPU_COUNT = os.cpu_count() or 2
MAX_WORKERS = max(1, min(12, CPU_COUNT))
OCR_INTRA_THREADS = max(1, int(os.environ.get("OCR_INTRA_THREADS", "2")))
OCR_INTER_THREADS = max(1, int(os.environ.get("OCR_INTER_THREADS", "1")))

SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
VLM_TIMEOUT_SECONDS = max(60, int(os.environ.get("VLM_TIMEOUT_SECONDS", "420")))
VLM_MAX_TOKENS = max(512, int(os.environ.get("VLM_MAX_TOKENS", "2048")))
EXTRACTION_TIMEOUT_SECONDS = max(30, int(os.environ.get("EXTRACTION_TIMEOUT_SECONDS", "120")))
EXTRACTION_MAX_TOKENS = max(512, int(os.environ.get("EXTRACTION_MAX_TOKENS", "2048")))
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
DEFAULT_VLM_MODEL = os.environ.get("DEFAULT_VLM_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
VLM_MODELS = [
    "Qwen/Qwen3-VL-32B-Thinking",
    "Qwen/Qwen3-VL-32B-Instruct",
    "Qwen/Qwen3-VL-30B-A3B-Thinking",
    "Qwen/Qwen3-VL-30B-A3B-Instruct",
    "PaddlePaddle/PaddleOCR-VL-1.5",
    "deepseek-ai/DeepSeek-OCR",
]
