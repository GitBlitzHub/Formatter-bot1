import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _env(*names: str, default: str = "") -> str:
    """Первое непустое значение из перечисленных переменных."""
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return default


# ---------- обязательное ----------
BOT_TOKEN = _env("BOT_TOKEN")
if not BOT_TOKEN:
    print("FATAL: BOT_TOKEN не задан в переменных окружения", file=sys.stderr)
    sys.exit(1)


# ---------- LLM ----------
# Ключ: сначала LLM_API_KEY, потом GEMINI_API_KEY (обратная совместимость)
LLM_API_KEY = _env("LLM_API_KEY", "GEMINI_API_KEY")
if not LLM_API_KEY:
    print("FATAL: задай LLM_API_KEY (или GEMINI_API_KEY) в переменных окружения",
          file=sys.stderr)
    sys.exit(1)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
LLM_BASE_URL = _env("LLM_BASE_URL", default=DEFAULT_BASE_URL).rstrip("/")

DEFAULT_MODELS = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]


def _models() -> list[str]:
    raw = _env("LLM_MODELS", "LLM_MODEL", "GEMINI_MODEL", "GEMINI_MODELS")
    items = [m.strip() for m in raw.split(",") if m.strip()]
    return items or DEFAULT_MODELS


LLM_MODELS = _models()
LLM_MODEL = LLM_MODELS[0]


# ---------- Vision ----------
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"
VISION_MODEL = _env("VISION_MODEL", default=DEFAULT_VISION_MODEL)
VISION_API_KEY = _env("VISION_API_KEY") or LLM_API_KEY
VISION_BASE_URL = (_env("VISION_BASE_URL") or LLM_BASE_URL).rstrip("/")


# ---------- доступ ----------
_raw_ids = _env("ALLOWED_USER_IDS").replace(" ", "").replace("\n", "")
ALLOWED_USER_IDS = {int(x) for x in _raw_ids.split(",") if x}


print(f"[config] base_url={LLM_BASE_URL} models={LLM_MODELS} "
      f"allowed={len(ALLOWED_USER_IDS) or 'all'}", file=sys.stderr)