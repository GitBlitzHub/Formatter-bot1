import sys
print("[bot] boot", file=sys.stderr, flush=True)

import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from llm_client import extract_people
from normalizer import build_block, strip_to_clean_blocks

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


CLEAN_CALLBACK_DATA = "clean_blocks"


def _clean_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Только строки (без лишнего)", callback_data=CLEAN_CALLBACK_DATA)]
        ]
    )


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
    # I-01: при нескольких записях не искать DL в общем тексте (он может быть чужим)
    raw_fallback = text if len(people) == 1 else ""
    for p in people:
        try:
            blocks.append(await build_block(p, raw_fallback))
        except Exception as e:
            log.exception("build_block failed")
            blocks.append(f"⚠️ Ошибка обработки записи: <code>{type(e).__name__}: {str(e)[:200]}</code>")
    out = "\n\n———————————\n\n".join(blocks)

    kb = _clean_kb() if "FIRST NAME:" in out else None
    for chunk in _chunks(out):
        await m.answer(chunk, reply_markup=kb)


@dp.callback_query(F.data == CLEAN_CALLBACK_DATA)
async def on_clean_blocks(callback: CallbackQuery):
    if not _allowed(callback):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    if not callback.message or not callback.message.text:
        await callback.answer()
        return
    cleaned = strip_to_clean_blocks(callback.message.text)
    if not cleaned:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Нет блоков для очистки")
        return
    await callback.message.edit_text(html.escape(cleaned, quote=False), reply_markup=None)
    await callback.answer()


async def main():
    bot = Bot(
        config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    log.info("Бот запущен как @%s (id=%s), провайдер: %s, модели: %s",
             me.username, me.id, config.LLM_BASE_URL, config.LLM_MODELS)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())