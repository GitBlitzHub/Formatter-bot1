import asyncio
import base64
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Any, Literal

from openai import AsyncOpenAI

import config

log = logging.getLogger("vision")

_client = AsyncOpenAI(
    base_url=config.VISION_BASE_URL,
    api_key=config.VISION_API_KEY,
    timeout=60.0,
)

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BASE_DELAY = 1.5

_SYSTEM_PROMPT = (
    "You are an assistant that estimates the gender and approximate age of the main person in a photo. "
    "Return strictly a JSON object with keys: "
    '{"gender": "M" | "F" | "U", "age": <integer 1..100 or null>}. '
    "Use 'M' for male, 'F' for female, 'U' if unclear. Do not include any extra text."
)


@dataclass
class VisionResult:
    gender: Literal["M", "F", "U"] = "U"
    age: int | None = None
    error: str = ""


def _status_of(exc: Exception) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    m = re.search(r"\b(400|408|409|429|500|502|503|504)\b", str(exc))
    return int(m.group(1)) if m else None


def _extract_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _normalize_gender(val: Any) -> Literal["M", "F", "U"]:
    if not isinstance(val, str):
        return "U"
    s = val.strip().lower()
    if s in {"m", "male", "man", "boy", "м", "муж", "мужчина"}:
        return "M"
    if s in {"f", "female", "woman", "girl", "ж", "жен", "женщина"}:
        return "F"
    return "U"


def _normalize_age(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        age = int(round(val))
    elif isinstance(val, str):
        m = re.search(r"\d+", val)
        if not m:
            return None
        age = int(m.group(0))
    else:
        return None
    return age if 1 <= age <= 110 else None


def _parse_vision_json(raw: str) -> VisionResult:
    data = _extract_json(raw)
    if not data:
        return VisionResult(gender="U", age=None, error="invalid json")
    gender = _normalize_gender(data.get("gender"))
    age = _normalize_age(data.get("age"))
    return VisionResult(gender=gender, age=age, error="")


async def _call_once(data_url: str, use_json_format: bool = True) -> str:
    kwargs: dict[str, Any] = {
        "model": config.VISION_MODEL,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Определи пол и примерный возраст человека на фото."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    if use_json_format:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await _client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def analyze_photo(image_bytes: bytes, mime_type: str = "image/jpeg") -> VisionResult:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    use_json_format = True
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            raw = await _call_once(data_url, use_json_format=use_json_format)
            res = _parse_vision_json(raw)
            if res.error:
                log.warning("[vision] invalid JSON response: %s", raw[:200])
            return res
        except Exception as e:
            last_exc = e
            code = _status_of(e)
            if code == 400 and use_json_format:
                use_json_format = False
                continue
            if code not in _RETRYABLE or attempt == _MAX_ATTEMPTS:
                log.warning("[vision] failed (code=%s): %s", code, e)
                break
            delay = _BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.7)
            log.info("[vision] %s, retry in %.1fs (%d/%d)", code, delay, attempt, _MAX_ATTEMPTS)
            await asyncio.sleep(delay)

    return VisionResult(gender="U", age=None, error=str(last_exc)[:120] if last_exc else "unknown error")
