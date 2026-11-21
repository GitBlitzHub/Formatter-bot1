import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_raw_ids = os.getenv("ALLOWED_USER_IDS", "").replace(" ", "")
ALLOWED_USER_IDS = {int(x) for x in _raw_ids.split(",") if x}