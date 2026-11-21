import logging
from google import genai
from google.genai import types

import config
from schemas import Extraction, Person
from prompts import SYSTEM_PROMPT

log = logging.getLogger("gemini")

_client = genai.Client(api_key=config.GEMINI_API_KEY)


async def extract_people(raw_text: str) -> list[Person]:
    """Отдаёт сырой текст в Gemini, получает структурированный список людей."""
    resp = await _client.aio.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=raw_text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=Extraction,
            temperature=0.1,
        ),
    )

    parsed = resp.parsed
    if parsed is None:
        log.warning("Пустой парсинг от Gemini: %s", resp.text)
        return []

    if isinstance(parsed, dict):
        parsed = Extraction(**parsed)

    return parsed.people