import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import config
from address_check import verify_full_address
from llm_client import extract_people
from normalizer import build_block, normalize_address

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

dp = Dispatcher()

HELP = (
    "Привет! Пришли мне любую «кашу» с данными о человеке, я приведу её к формату.\n\n"
    "Можно кидать как одну запись, так и несколько подряд — разделю и обработаю каждую.\n\n"
    "Команды:\n"
    "/id — узнать свой telegram user_id\n"
    "/help — эта справка"
)


def _allowed(m: Message) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return m.from_user.id in config.ALLOWED_USER_IDS


def _chunks(text: str, size: int = 3900):
    while text:
        yield text[:size]
        text = text[size:]


@dp.message(CommandStart())
async def cmd_start(m: Message):
    await m.answer(HELP)


@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP)


@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Ваш user_id: <code>{m.from_user.id}</code>")


@dp.message(F.text)
async def on_text(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return

    text = m.text.strip()
    if not text or text.startswith("/"):
        return

    await m.bot.send_chat_action(m.chat.id, "typing")

    try:
        people = await extract_people(text)
    except Exception as e:
        log.exception("LLM call failed")
        await m.answer(
            "⏳ LLM сейчас недоступна или перегружена. Попробуй ещё раз через минуту.\n\n"
            f"<code>{type(e).__name__}: {str(e)[:200]}</code>"
        )
        return

    if not people:
        await m.answer("🤷 Ничего не нашёл. Попробуй прислать текст ещё раз.")
        return

    blocks = []
    for p in people:
        addr = normalize_address(p.address or "")

        if not addr:
            final_addr = ""
        else:
            ok = await verify_full_address(addr)
            final_addr = "(Ошибка)" if ok is False else addr

        blocks.append(build_block(p, address_override=final_addr))

    out = "\n\n———————————\n\n".join(blocks)

    for chunk in _chunks(out):
        await m.answer(chunk)


async def main():
    bot = Bot(
        config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    log.info("Бот запущен, провайдер: %s, модели: %s",
             config.LLM_BASE_URL, config.LLM_MODELS)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())