import sys
print("[bot] boot", file=sys.stderr, flush=True)

import asyncio
import html
import io
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from batch_store import (
    BatchSession,
    BatchStore,
    MatchAssignment,
    MatchRecord,
    MediaGroupBuffer,
    PhotoItem,
    calc_age,
)
from llm_client import extract_people
from matcher import build_assignment, match_batch
from normalizer import build_block, build_block_details, strip_to_clean_blocks
from vision_client import analyze_photo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

dp = Dispatcher()
batch_store = BatchStore()
media_group_buffer = MediaGroupBuffer(delay=0.7)

HELP = (
    "Привет! Пришли мне любую «кашу» с данными о человеке, я приведу её к формату.\n\n"
    "Можно кидать как одну запись, так и несколько подряд — разделю и обработаю каждую.\n\n"
    "Команды:\n"
    "/match — режим пакетного сопоставления строк и фото\n"
    "/match_reset — закрыть/сбросить текущую пачку\n"
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


def _batch_status_text(session: BatchSession) -> str:
    lines = [
        f"📦 <b>Режим /match</b> (пачка <code>{session.batch_id}</code>, дата <code>{session.date_str}</code>)",
        f"Строк: <b>{len(session.records)}</b> | Фото: <b>{len(session.photos)}</b>",
    ]
    if session.records:
        lines.append("\n<b>Строки:</b>")
        for r in session.records:
            age_txt = f"{r.age} л." if r.age is not None else "? л."
            mark = "✅" if r.index in session.assignments else "⏳"
            lines.append(f"{mark} #{r.index + 1} {html.escape(r.display_name)} ({r.gender}, {age_txt})")
    if session.photos:
        lines.append("\n<b>Фото:</b>")
        for p in session.photos:
            age_txt = f"~{p.age} л." if p.age is not None else "? л."
            err_txt = " ⚠️" if p.vision_error else ""
            lines.append(f"🖼 Фото #{p.index + 1}: {p.gender}, {age_txt}{err_txt}")
    lines.append("\nПрисылайте ещё строки или фото, либо выберите действие:")
    return "\n".join(lines)


def _match_kb(session: BatchSession) -> InlineKeyboardMarkup:
    bid = session.batch_id
    rows: list[list[InlineKeyboardButton]] = []
    if session.records and session.photos:
        rows.append([
            InlineKeyboardButton(text="🔗 Связать автоматически", callback_data=f"pm:{bid}:auto"),
        ])
        rows.append([
            InlineKeyboardButton(text="✋ Выбрать вручную", callback_data=f"pm:{bid}:man"),
            InlineKeyboardButton(text="👯 Выбрать дубль", callback_data=f"pm:{bid}:dup"),
        ])
    if session.photos:
        rows.append([
            InlineKeyboardButton(text="🖼 Показать фото с номерами", callback_data=f"pm:{bid}:showp"),
        ])
    rows.append([
        InlineKeyboardButton(text="🗑 Закрыть / Сбросить пачку", callback_data=f"pm:{bid}:reset"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _select_record_kb(session: BatchSession, mode: str) -> InlineKeyboardMarkup:
    bid = session.batch_id
    rows: list[list[InlineKeyboardButton]] = []
    ordered = sorted(
        session.records,
        key=lambda r: (r.index in session.assignments, r.index) if mode == "d" else (False, r.index),
    )
    for r in ordered:
        age_txt = str(r.age) if r.age is not None else "?"
        if r.index in session.assignments:
            status = f"✅#{session.assignments[r.index].photo_index + 1}"
        else:
            status = "⏳"
        label = f"{status} #{r.index + 1} {r.display_name[:28]} ({age_txt}, {r.gender})"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"pm:{bid}:sr:{r.index}:{mode}")
        ])
    rows.append([InlineKeyboardButton(text="🗑 Закрыть пачку", callback_data=f"pm:{bid}:reset")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _select_photo_kb(session: BatchSession, rec_idx: int, mode: str) -> InlineKeyboardMarkup:
    bid = session.batch_id
    rows: list[list[InlineKeyboardButton]] = []
    for p in session.photos:
        age_txt = f"~{p.age} г." if p.age is not None else "? г."
        label = f"Фото #{p.index + 1} ({p.gender}, {age_txt})"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"pm:{bid}:sp:{rec_idx}:{p.index}:{mode}")
        ])
    rows.append([InlineKeyboardButton(text="🖼 Показать фото с номерами", callback_data=f"pm:{bid}:showp")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_match_caption(record: MatchRecord, assignment: MatchAssignment) -> tuple[str, str | None]:
    """
    Формирует (caption, overflow_text) с жёстким лимитом Telegram 1024 символа.
    При обрезке режет сырой clean_block ДО html.escape, чтобы не оставлять незакрытых HTML-тегов.
    """
    warn_raw = assignment.warning or ""
    warn_html = f"\n\n<i>{html.escape(warn_raw, quote=False)}</i>" if warn_raw else ""

    base = record.full_block + warn_html
    if len(base) <= 1024:
        return base, None

    compact = html.escape(record.clean_block, quote=False) + warn_html
    if len(compact) <= 1024:
        return compact, record.full_block

    # Обрезаем сырой текст ДО html.escape, чтобы не сломать HTML-разметку
    budget = 1024 - len(warn_html) - 3
    if budget <= 0:
        # Даже предупреждение с тегом не влезает — оставляем чистый безопасный бюджет без тега
        warn_html = ""
        budget = 1024 - 3

    raw = record.clean_block
    # Быстрая верхняя граница по длине сырого текста
    lo, hi = 0, min(len(raw), budget)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(html.escape(raw[:mid], quote=False)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    truncated = html.escape(raw[:lo], quote=False) + "..." + warn_html
    return truncated, record.full_block


async def send_match_card(
    bot: Bot,
    chat_id: int,
    record: MatchRecord,
    photo: PhotoItem,
    assignment: MatchAssignment,
) -> None:
    caption, overflow_text = format_match_caption(record, assignment)
    await bot.send_photo(chat_id, photo=photo.file_id, caption=caption)
    if overflow_text:
        for chunk in _chunks(overflow_text):
            await bot.send_message(chat_id, chunk)


@dp.message(CommandStart())
async def cmd_start(m: Message):
    await m.answer(HELP)


@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP)


@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Ваш user_id: <code>{m.from_user.id}</code>")


@dp.message(Command("match"))
async def cmd_match(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    session, created, cleared_stale = batch_store.get_or_create(m.from_user.id)
    prefix = "🧹 Устаревшая пачка за прошлый день была автоматически очищена.\n\n" if cleared_stale else ""
    if created:
        msg = (
            f"{prefix}📦 Включён режим <b>/match</b> (пачка <code>{session.batch_id}</code>, "
            f"дата <code>{session.date_str}</code>).\n"
            "Присылайте строки (текстом) и фотографии (по одной или альбомом)."
        )
        await m.answer(msg, reply_markup=_match_kb(session))
    else:
        await m.answer(prefix + _batch_status_text(session), reply_markup=_match_kb(session))


@dp.message(Command("match_reset", "cancel"))
async def cmd_match_reset(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    removed = batch_store.reset(m.from_user.id)
    if removed is not None:
        media_group_buffer.cancel_batch(m.from_user.id, removed.batch_id)
        await m.answer(f"🗑 Пачка <code>{removed.batch_id}</code> закрыта и очищена.")
    else:
        await m.answer("Активной пачки /match нет.")


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

    session = batch_store.get_active(m.from_user.id)
    if session is not None:
        raw_fallback = text if len(people) == 1 else ""
        added = 0
        for p in people:
            try:
                full_block, gender, dob, display_name = await build_block_details(p, raw_fallback)
                clean_block = strip_to_clean_blocks(full_block)
                age = calc_age(dob, session.date_str)
                session.records.append(
                    MatchRecord(
                        index=len(session.records),
                        person=p,
                        gender=gender,
                        dob=dob,
                        age=age,
                        display_name=display_name,
                        full_block=full_block,
                        clean_block=clean_block,
                    )
                )
                added += 1
            except Exception as e:
                log.exception("build_block_details failed")
                await m.answer(f"⚠️ Ошибка обработки записи: <code>{type(e).__name__}: {str(e)[:200]}</code>")
        if added:
            await m.answer(_batch_status_text(session), reply_markup=_match_kb(session))
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


async def _analyze_telegram_photo(bot: Bot, message: Message) -> tuple[str, str, str, int | None, str]:
    photo_obj = message.photo[-1]
    buf = io.BytesIO()
    await bot.download(photo_obj, destination=buf)
    res = await analyze_photo(buf.getvalue())
    return photo_obj.file_id, photo_obj.file_unique_id, res.gender, res.age, res.error


async def _flush_media_group(user_id: int, media_group_id: str, batch_id: str, messages: list[Message]) -> None:
    session = batch_store.get_active(user_id)
    if session is None or session.batch_id != batch_id or not messages:
        return

    sem = asyncio.Semaphore(3)
    bot = messages[0].bot

    async def _worker(msg: Message):
        async with sem:
            return await _analyze_telegram_photo(bot, msg)

    results = await asyncio.gather(*(_worker(msg) for msg in messages), return_exceptions=True)

    session = batch_store.get_active(user_id)
    if session is None or session.batch_id != batch_id:
        return

    for item in results:
        if isinstance(item, Exception):
            log.warning("media_group photo failed: %s", item)
            continue
        file_id, file_unique_id, gender, age, err = item
        session.photos.append(
            PhotoItem(
                index=len(session.photos),
                file_id=file_id,
                file_unique_id=file_unique_id,
                gender=gender,
                age=age,
                vision_error=err,
            )
        )

    await messages[-1].answer(_batch_status_text(session), reply_markup=_match_kb(session))


@dp.message(F.photo)
async def on_photo(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return

    session = batch_store.get_active(m.from_user.id)
    if session is None:
        await m.answer("Чтобы подбирать фото к строкам, включите режим /match")
        return

    if m.media_group_id:
        media_group_buffer.add(
            m.from_user.id,
            m.media_group_id,
            session.batch_id,
            m,
            _flush_media_group,
        )
        return

    await m.bot.send_chat_action(m.chat.id, "typing")
    file_id, file_unique_id, gender, age, err = await _analyze_telegram_photo(m.bot, m)
    session.photos.append(
        PhotoItem(
            index=len(session.photos),
            file_id=file_id,
            file_unique_id=file_unique_id,
            gender=gender,
            age=age,
            vision_error=err,
        )
    )
    await m.answer(_batch_status_text(session), reply_markup=_match_kb(session))


@dp.callback_query(F.data.startswith("pm:"))
async def on_pm_callback(callback: CallbackQuery):
    if not _allowed(callback):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    _, batch_id, action, *rest = parts
    user_id = callback.from_user.id
    if not batch_store.is_valid_batch(user_id, batch_id):
        await callback.answer("Пачка устарела или закрыта. Включите /match заново.", show_alert=True)
        return

    session = batch_store.get_active(user_id)
    if session is None:
        await callback.answer("Пачка не найдена.", show_alert=True)
        return

    chat_id = callback.message.chat.id

    if action == "reset":
        batch_store.reset(user_id)
        media_group_buffer.cancel_batch(user_id, batch_id)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(f"🗑 Пачка <code>{batch_id}</code> закрыта и очищена.")
        await callback.answer("Пачка закрыта")
        return

    if action == "showp":
        if not session.photos:
            await callback.answer("В пачке нет фото.", show_alert=True)
            return
        for p in session.photos:
            age_txt = f"~{p.age} г." if p.age is not None else "? г."
            await callback.bot.send_photo(chat_id, photo=p.file_id, caption=f"Фото #{p.index + 1} ({p.gender}, {age_txt})")
        await callback.answer()
        return

    if action == "auto":
        if not session.records or not session.photos:
            await callback.answer("Нужны и строки, и фото для автоподбора.", show_alert=True)
            return
        matched = match_batch(session.records, session.photos)
        session.assignments = matched
        for rec_idx in sorted(matched):
            assign = matched[rec_idx]
            await send_match_card(
                callback.bot,
                chat_id,
                session.records[rec_idx],
                session.photos[assign.photo_index],
                assign,
            )

        unmatched = [r for r in session.records if r.index not in session.assignments]
        if unmatched:
            lines = ["⚠️ <b>Остались строки без фото:</b>"]
            for r in unmatched:
                age_txt = f"{r.age} л." if r.age is not None else "? л."
                lines.append(f"• #{r.index + 1} {html.escape(r.display_name)} ({r.gender}, {age_txt})")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="👯 Выбрать дубль", callback_data=f"pm:{batch_id}:dup"),
                    InlineKeyboardButton(text="✋ Выбрать вручную", callback_data=f"pm:{batch_id}:man"),
                ],
                [InlineKeyboardButton(text="🗑 Закрыть / Сбросить пачку", callback_data=f"pm:{batch_id}:reset")],
            ])
            await callback.message.answer("\n".join(lines), reply_markup=kb)
        else:
            await callback.message.answer("✅ Все строки сматчены и выданы!", reply_markup=_match_kb(session))
        await callback.answer()
        return

    if action in ("man", "dup"):
        if not session.records or not session.photos:
            await callback.answer("Нужны и строки, и фото.", show_alert=True)
            return
        mode = "d" if action == "dup" else "m"
        title = "Выберите строку для дубля фото:" if mode == "d" else "Выберите строку для ручного назначения фото:"
        await callback.message.answer(title, reply_markup=_select_record_kb(session, mode))
        await callback.answer()
        return

    if action == "sr" and len(rest) >= 2:
        rec_idx = int(rest[0])
        mode = rest[1]
        if rec_idx < 0 or rec_idx >= len(session.records):
            await callback.answer("Строка не найдена.", show_alert=True)
            return
        rec = session.records[rec_idx]
        await callback.message.answer(
            f"Выберите фото для <b>#{rec.index + 1} {html.escape(rec.display_name)}</b>:",
            reply_markup=_select_photo_kb(session, rec_idx, mode),
        )
        await callback.answer()
        return

    if action == "sp" and len(rest) >= 3:
        rec_idx = int(rest[0])
        photo_idx = int(rest[1])
        mode = rest[2]
        if (
            rec_idx < 0 or rec_idx >= len(session.records)
            or photo_idx < 0 or photo_idx >= len(session.photos)
        ):
            await callback.answer("Строка или фото не найдены.", show_alert=True)
            return
        mode_full = "duplicate" if mode == "d" else "manual"
        assign = build_assignment(session.records[rec_idx], session.photos[photo_idx], mode=mode_full)
        session.assignments[rec_idx] = assign
        await send_match_card(
            callback.bot,
            chat_id,
            session.records[rec_idx],
            session.photos[photo_idx],
            assign,
        )
        await callback.answer("Карточка выдана")
        return

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