import asyncio
import json
import logging
import random
import re

from openai import AsyncOpenAI

import config
from schemas import Extraction, Person
from prompts import SYSTEM_PROMPT, JSON_REQUEST

log = logging.getLogger("llm")

_client = AsyncOpenAI(
    base_url=config.LLM_BASE_URL,
    api_key=config.LLM_API_KEY,
    timeout=60.0,
)

_SYSTEM = SYSTEM_PROMPT + "\n\n" + JSON_REQUEST

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BASE_DELAY = 1.5


def _status_of(exc: Exception) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    m = re.search(r"\b(408|409|429|500|502|503|504)\b", str(exc))
    return int(m.group(1)) if m else None


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def _call_once(model: str, raw_text: str) -> str:
    resp = await _client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": raw_text},
        ],
    )
    return resp.choices[0].message.content or ""


async def extract_people(raw_text: str) -> list[Person]:
    last_exc: Exception | None = None

    for model in config.LLM_MODELS:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                raw = await _call_once(model, raw_text)
                data = _extract_json(raw)

                if not data:
                    log.warning("[%s] не распарсил: %s", model, raw[:400])
                    return []

                extraction = Extraction(**data)
                if model != config.LLM_MODELS[0]:
                    log.info("Ответ получен с фолбэк-модели: %s", model)
                return extraction.people

            except Exception as e:
                last_exc = e
                code = _status_of(e)

                if code not in _RETRYABLE or attempt == _MAX_ATTEMPTS:
                    log.warning("[%s] сдаюсь (code=%s): %s", model, code, e)
                    break

                delay = _BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.7)
                log.info("[%s] %s, повтор через %.1fs (%d/%d)",
                         model, code, delay, attempt, _MAX_ATTEMPTS)
                await asyncio.sleep(delay)

    raise RuntimeError(f"Все модели недоступны: {last_exc}") from last_exc