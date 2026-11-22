import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return default


# ---------- обязательное ----------
BOT_TOKEN = _env("BOT_TOKEN")
if not BOT_TOKEN:
    print("FATAL: BOT_TOKEN не задан", file=sys.stderr)
    sys.exit(1)


# ---------- LLM ----------
LLM_API_KEY = _env("LLM_API_KEY", "GEMINI_API_KEY")
if not LLM_API_KEY:
    print("FATAL: задай LLM_API_KEY", file=sys.stderr)
    sys.exit(1)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
LLM_BASE_URL = _env("LLM_BASE_URL", default=DEFAULT_BASE_URL).rstrip("/")

DEFAULT_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def _models() -> list[str]:
    raw = _env("LLM_MODELS", "LLM_MODEL")
    items = [m.strip() for m in raw.split(",") if m.strip()]
    return items or DEFAULT_MODELS


LLM_MODELS = _models()
LLM_MODEL = LLM_MODELS[0]


# ---------- валидация адреса (getAddress.io) ----------
GETADDRESS_API_KEY = _env("GETADDRESS_API_KEY")


# ---------- доступ ----------
_raw_ids = _env("ALLOWED_USER_IDS").replace(" ", "").replace("\n", "")
ALLOWED_USER_IDS = {int(x) for x in _raw_ids.split(",") if x}

print(f"[config] llm={LLM_BASE_URL} models={LLM_MODELS} "
      f"getaddress={'on' if GETADDRESS_API_KEY else 'off'} "
      f"allowed={len(ALLOWED_USER_IDS) or 'all'}", file=sys.stderr)