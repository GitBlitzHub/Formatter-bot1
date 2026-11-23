import sys
print("[bot] boot", file=sys.stderr, flush=True)

import asyncio
import html
import io
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

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
from image_utils import InvalidImageError, prepare_image
from llm_client import extract_people
from matcher import build_assignment, match_batch
from normalizer import build_block, build_block_details, strip_to_clean_blocks
from photo.crop import crop_uk
from photo.faces import ModelsUnavailableError, NoFace
from photo.phone_look import LookSettings, apply_phone_look, load_rgb
from photo.store import MAX_FILE_BYTES, MAX_QUEUE, PhotoStore, QueuedPhoto
from vision_client import analyze_photo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

dp = Dispatcher()
batch_store = BatchStore()
photo_store = PhotoStore()
media_group_buffer = MediaGroupBuffer(delay=0.7)
_cpu_sem = asyncio.Semaphore(2)

HELP = (
    "Привет! Пришли мне любую «кашу» с данными о человеке, я приведу её к формату.\n\n"
    "Можно кидать как одну запись, так и несколько подряд — разделю и обработаю каждую.\n\n"
    "Команды:\n"
    "/match — режим пакетного сопоставления строк и фото\n"
    "/match_reset — закрыть/сбросить текущую пачку\n"
    "/photo — режим обработки и кадрирования фото 35×45\n"
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


BTN_MATCH = "📦 Match"
BTN_PHOTO = "📸 Photo"
BTN_HELP = "❓ Помощь"

BTN_PHOTO_CROP = "✂️ 35×45 UK DL"
BTN_PHOTO_LOOK = "📱 Под iPhone"
BTN_PHOTO_BOTH = "✨ Оба"
BTN_PHOTO_SETTINGS = "⚙️ Настройки"
BTN_PHOTO_CLEAR = "🧹 Очистить"
BTN_PHOTO_BACK = "⬅️ Назад"

REPLY_BUTTON_TEXTS = {
    BTN_MATCH,
    BTN_PHOTO,
    BTN_HELP,
    BTN_PHOTO_CROP,
    BTN_PHOTO_LOOK,
    BTN_PHOTO_BOTH,
    BTN_PHOTO_SETTINGS,
    BTN_PHOTO_CLEAR,
    BTN_PHOTO_BACK,
}


def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_MATCH),
                KeyboardButton(text=BTN_PHOTO),
                KeyboardButton(text=BTN_HELP),
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder="Пришлите данные или фото...",
    )


def photo_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PHOTO_CROP), KeyboardButton(text=BTN_PHOTO_LOOK)],
            [KeyboardButton(text=BTN_PHOTO_BOTH), KeyboardButton(text=BTN_PHOTO_SETTINGS)],
            [KeyboardButton(text=BTN_PHOTO_CLEAR), KeyboardButton(text=BTN_PHOTO_BACK)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder="Пришлите фото (лучше файлом)...",
    )


CLEAN_CALLBACK_DATA = "clean_blocks"


def _clean_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Только строки", callback_data=CLEAN_CALLBACK_DATA)]
        ]
    )


def _open_match_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Включить Match", callback_data="pm:open"),
                InlineKeyboardButton(text="📸 Режим Photo", callback_data="ph:open"),
            ]
        ]
    )


def _photo_queue_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✂️ 35×45", callback_data="ph:crop"),
                InlineKeyboardButton(text="📱 Под iPhone", callback_data="ph:look"),
                InlineKeyboardButton(text="✨ Оба", callback_data="ph:both"),
            ],
            [InlineKeyboardButton(text="🧹 Очистить", callback_data="ph:clear")],
        ]
    )


def _photo_settings_kb(look: LookSettings) -> InlineKeyboardMarkup:
    def _row(key: str, label: str, val: str) -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(text="−", callback_data=f"phs:{key}:-"),
            InlineKeyboardButton(text=f"{label}: {val}", callback_data="phs:noop"),
            InlineKeyboardButton(text="+", callback_data=f"phs:{key}:+"),
        ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row("soft", "Кожа", f"{look.soft:g}%"),
            _row("paper", "Ватман", f"{look.paper:g}%"),
            _row("noise", "Шум", f"{look.noise:g}"),
            [InlineKeyboardButton(text="↩️ По умолчанию", callback_data="phs:reset")],
        ]
    )


async def _edit_or_send(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        await message.answer(text, reply_markup=reply_markup)


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


def _match_kb(session: BatchSession, include_auto: bool = True) -> InlineKeyboardMarkup:
    bid = session.batch_id
    rows: list[list[InlineKeyboardButton]] = []
    if session.records and session.photos:
        if include_auto:
            rows.append([
                InlineKeyboardButton(text="🔗 Автоподбор", callback_data=f"pm:{bid}:auto"),
            ])
        rows.append([
            InlineKeyboardButton(text="✋ Вручную", callback_data=f"pm:{bid}:man"),
            InlineKeyboardButton(text="👯 Дубль", callback_data=f"pm:{bid}:dup"),
        ])
    bottom_row: list[InlineKeyboardButton] = []
    if session.photos:
        bottom_row.append(
            InlineKeyboardButton(text=f"🖼 Фото ({len(session.photos)})", callback_data=f"pm:{bid}:showp")
        )
    bottom_row.append(
        InlineKeyboardButton(
            text=f"✂️ 35×45: {'Вкл' if session.crop_35x45 else 'Выкл'}",
            callback_data=f"pm:{bid}:tcrop",
        )
    )
    bottom_row.append(
        InlineKeyboardButton(text="🗑 Сброс", callback_data=f"pm:{bid}:reset")
    )
    rows.append(bottom_row)
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
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"pm:{bid}:back"),
        InlineKeyboardButton(text="🗑 Сброс", callback_data=f"pm:{bid}:reset"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _select_photo_kb(session: BatchSession, rec_idx: int, mode: str) -> InlineKeyboardMarkup:
    bid = session.batch_id
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for p in session.photos:
        age_txt = f"~{p.age}" if p.age is not None else "?"
        label = f"🖼 #{p.index + 1} ({p.gender}, {age_txt})"
        pair.append(
            InlineKeyboardButton(text=label, callback_data=f"pm:{bid}:sp:{rec_idx}:{p.index}:{mode}")
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"pm:{bid}:back:{mode}"),
        InlineKeyboardButton(text="🖼 Показать фото", callback_data=f"pm:{bid}:showp"),
    ])
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


async def _send_photo_item(bot: Bot, chat_id: int, photo: PhotoItem, caption: str) -> None:
    media = (
        BufferedInputFile(photo.photo_bytes, filename="photo.jpg")
        if photo.photo_bytes is not None
        else photo.file_id
    )
    sent = await bot.send_photo(chat_id, photo=media, caption=caption)
    if photo.photo_bytes is not None and sent is not None:
        sent_photos = getattr(sent, "photo", None)
        if isinstance(sent_photos, (list, tuple)) and len(sent_photos) > 0:
            photo.file_id = sent_photos[-1].file_id
            photo.photo_bytes = None


def _crop_uk_bytes(raw_bytes: bytes):
    return crop_uk(load_rgb(raw_bytes))


async def send_match_card(
    bot: Bot,
    chat_id: int,
    record: MatchRecord,
    photo: PhotoItem,
    assignment: MatchAssignment,
    crop_35x45: bool = False,
) -> None:
    effective_assign = assignment
    overflow_text: str | None = None

    if crop_35x45:
        if photo.cropped_file_id:
            caption, overflow_text = format_match_caption(record, effective_assign)
            await bot.send_photo(chat_id, photo=photo.cropped_file_id, caption=caption)
        elif photo.raw_bytes is not None:
            try:
                async with _cpu_sem:
                    cr = await asyncio.to_thread(_crop_uk_bytes, photo.raw_bytes)
                caption, overflow_text = format_match_caption(record, effective_assign)
                sent = await bot.send_photo(
                    chat_id,
                    photo=BufferedInputFile(cr.jpeg, filename="crop_35x45.jpg"),
                    caption=caption,
                )
                if sent is not None:
                    sent_photos = getattr(sent, "photo", None)
                    if isinstance(sent_photos, (list, tuple)) and len(sent_photos) > 0:
                        photo.cropped_file_id = sent_photos[-1].file_id
            except NoFace:
                note = "35×45: лицо не найдено, отправлено исходное фото."
                warn = f"{assignment.warning}; {note}" if assignment.warning else note
                effective_assign = MatchAssignment(
                    record_index=assignment.record_index,
                    photo_index=assignment.photo_index,
                    mode=assignment.mode,
                    age_diff=assignment.age_diff,
                    warning=warn,
                )
                caption, overflow_text = format_match_caption(record, effective_assign)
                await _send_photo_item(bot, chat_id, photo, caption)
            except ModelsUnavailableError:
                note = "35×45: модели недоступны, отправлено исходное фото."
                warn = f"{assignment.warning}; {note}" if assignment.warning else note
                effective_assign = MatchAssignment(
                    record_index=assignment.record_index,
                    photo_index=assignment.photo_index,
                    mode=assignment.mode,
                    age_diff=assignment.age_diff,
                    warning=warn,
                )
                caption, overflow_text = format_match_caption(record, effective_assign)
                await _send_photo_item(bot, chat_id, photo, caption)
        else:
            caption, overflow_text = format_match_caption(record, effective_assign)
            await _send_photo_item(bot, chat_id, photo, caption)
    else:
        caption, overflow_text = format_match_caption(record, effective_assign)
        await _send_photo_item(bot, chat_id, photo, caption)

    if overflow_text:
        for chunk in _chunks(overflow_text):
            await bot.send_message(chat_id, chunk)


@dp.message(CommandStart())
async def cmd_start(m: Message):
    await m.answer(HELP, reply_markup=main_reply_kb())


@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP, reply_markup=main_reply_kb())


@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Ваш user_id: <code>{m.from_user.id}</code>", reply_markup=main_reply_kb())


def _match_open_text_and_kb(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    session, created, cleared_stale = batch_store.get_or_create(user_id)
    prefix = "🧹 Устаревшая пачка за прошлый день была автоматически очищена.\n\n" if cleared_stale else ""
    if created:
        msg = (
            f"{prefix}📦 Включён режим <b>/match</b> (пачка <code>{session.batch_id}</code>, "
            f"дата <code>{session.date_str}</code>).\n"
            "Присылайте строки (текстом) и фотографии (по одной или альбомом)."
        )
    else:
        msg = prefix + _batch_status_text(session)
    return msg, _match_kb(session)


@dp.message(Command("match"))
async def cmd_match(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    photo_store.exit_photo_mode(m.from_user.id)
    msg, kb = _match_open_text_and_kb(m.from_user.id)
    await m.answer(msg, reply_markup=kb)


@dp.message(Command("photo"))
async def cmd_photo(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    photo_store.enter_photo_mode(m.from_user.id)
    await m.answer(
        "📸 <b>Режим Photo</b>\n\n"
        "Присылайте фото (лучше <b>файлами</b>, можно альбомом) — они попадут в очередь.\n"
        "Затем выберите действие кнопками ниже:\n"
        "• <b>✂️ 35×45 UK DL</b> — кадрирование 827×1063 px (600 dpi)\n"
        "• <b>📱 Под iPhone</b> — обработка кожи, ватмана и шума\n"
        "• <b>✨ Оба</b> — сначала обработка под iPhone, затем кадрирование 35×45",
        reply_markup=photo_reply_kb(),
    )


@dp.message(Command("match_reset", "cancel"))
async def cmd_match_reset(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    removed = batch_store.reset(m.from_user.id)
    if removed is not None:
        media_group_buffer.cancel_batch(m.from_user.id, removed.batch_id)
        await m.answer(
            f"🗑 Пачка <code>{removed.batch_id}</code> закрыта и очищена.",
            reply_markup=main_reply_kb(),
        )
    else:
        await m.answer("Активной пачки /match нет.", reply_markup=main_reply_kb())


@dp.message(F.text == BTN_MATCH)
async def on_btn_match(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    await cmd_match(m)


@dp.message(F.text == BTN_PHOTO)
async def on_btn_photo(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    await cmd_photo(m)


@dp.message(F.text == BTN_PHOTO_BACK)
async def on_btn_photo_back(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    photo_store.exit_photo_mode(m.from_user.id)
    await m.answer("Возвращаю главное меню.", reply_markup=main_reply_kb())


@dp.message(F.text == BTN_PHOTO_CROP)
async def on_btn_photo_crop(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    await _run_photo_queue(m.bot, m.chat.id, m.from_user.id, "crop")


@dp.message(F.text == BTN_PHOTO_LOOK)
async def on_btn_photo_look(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    await _run_photo_queue(m.bot, m.chat.id, m.from_user.id, "look")


@dp.message(F.text == BTN_PHOTO_BOTH)
async def on_btn_photo_both(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    await _run_photo_queue(m.bot, m.chat.id, m.from_user.id, "both")


@dp.message(F.text == BTN_PHOTO_SETTINGS)
async def on_btn_photo_settings(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    await _show_photo_settings(m.bot, m.chat.id, m.from_user.id)


@dp.message(F.text == BTN_PHOTO_CLEAR)
async def on_btn_photo_clear(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    photo_store.clear_queue(m.from_user.id)
    await m.answer("🧹 Очередь фото очищена.", reply_markup=photo_reply_kb())


@dp.message(F.text == BTN_HELP)
async def on_btn_help(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return
    await cmd_help(m)


@dp.message(F.text)
async def on_text(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return

    text = (m.text or "").strip()
    if not text or text.startswith("/") or text in REPLY_BUTTON_TEXTS:
        return

    await m.bot.send_chat_action(m.chat.id, "typing")

    try:
        people = await extract_people(text)
    except Exception as e:
        log.exception("LLM call failed")
        await m.answer(
            "⏳ LLM сейчас недоступна или перегружена. Попробуй ещё раз через минуту.\n\n"
            f"<code>{type(e).__name__}: {str(e)[:200]}</code>",
            reply_markup=main_reply_kb(),
        )
        return

    if not people:
        await m.answer(
            "🤷 Ничего не нашёл. Попробуй прислать текст ещё раз.",
            reply_markup=main_reply_kb(),
        )
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

    kb = _clean_kb() if "FIRST NAME:" in out else main_reply_kb()
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


ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _is_image_document(doc: Document | None) -> bool:
    if doc is None:
        return False
    mime = (getattr(doc, "mime_type", None) or "").lower().strip()
    if mime.startswith("image/"):
        return mime != "image/svg+xml"
    ext = Path(getattr(doc, "file_name", None) or "").suffix.lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS


async def _analyze_telegram_media(
    bot: Bot,
    message: Message,
) -> tuple[str, str, str, int | None, str, bool, bytes | None, bytes | None]:
    if message.photo:
        photo_obj = message.photo[-1]
        buf = io.BytesIO()
        await bot.download(photo_obj, destination=buf)
        raw_bytes = buf.getvalue()
        res = await analyze_photo(raw_bytes)
        return photo_obj.file_id, photo_obj.file_unique_id, res.gender, res.age, res.error, False, None, raw_bytes

    doc_obj = message.document
    buf = io.BytesIO()
    await bot.download(doc_obj, destination=buf)
    raw_bytes = buf.getvalue()
    try:
        prepared_bytes = await asyncio.to_thread(prepare_image, raw_bytes)
    except InvalidImageError as e:
        log.warning("Invalid document image %s: %s", getattr(doc_obj, "file_name", None), e)
        return doc_obj.file_id, doc_obj.file_unique_id, "U", None, "invalid image", True, None, None

    res = await analyze_photo(prepared_bytes, mime_type="image/jpeg")
    return doc_obj.file_id, doc_obj.file_unique_id, res.gender, res.age, res.error, True, prepared_bytes, raw_bytes


async def _flush_media_group(user_id: int, media_group_id: str, batch_id: str, messages: list[Message]) -> None:
    session = batch_store.get_active(user_id)
    if session is None or session.batch_id != batch_id or not messages:
        return

    sem = asyncio.Semaphore(3)
    bot = messages[0].bot
    await bot.send_chat_action(messages[0].chat.id, "typing")

    async def _worker(msg: Message):
        async with sem:
            return await _analyze_telegram_media(bot, msg)

    results = await asyncio.gather(*(_worker(msg) for msg in messages), return_exceptions=True)

    session = batch_store.get_active(user_id)
    if session is None or session.batch_id != batch_id:
        return

    for item in results:
        if isinstance(item, Exception):
            log.warning("media_group photo failed: %s", item)
            continue
        file_id, file_unique_id, gender, age, err, is_doc, photo_bytes, raw_bytes = item
        session.photos.append(
            PhotoItem(
                index=len(session.photos),
                file_id=file_id,
                file_unique_id=file_unique_id,
                gender=gender,
                age=age,
                vision_error=err,
                is_document=is_doc,
                photo_bytes=photo_bytes,
                raw_bytes=raw_bytes,
            )
        )

    await messages[-1].answer(_batch_status_text(session), reply_markup=_match_kb(session))


def _photo_stem(filename: str) -> str:
    base = Path(filename).stem or "photo"
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "-_")[:60]
    return cleaned or "photo"


def _photo_queue_text(queue: list[QueuedPhoto]) -> str:
    text = f"📥 В очереди: <b>{len(queue)}</b> фото. Что сделать?"
    compressed = sum(1 for item in queue if item.compressed)
    if compressed:
        text += f"\n⚠️ {compressed} из них пришли сжатыми (как «фото», а не «файл») — качество будет ниже."
    return text


def _photo_settings_text(look: LookSettings) -> str:
    return (
        "⚙️ <b>Настройки обработки «Под iPhone»</b>\n\n"
        f"Кожа (мягкость пор): <b>{look.soft:g}%</b>\n"
        f"Неровность ватмана: <b>{look.paper:g}%</b>\n"
        f"Шум матрицы: <b>{look.noise:g}</b>\n\n"
        "Кадрирование 35×45 эти настройки не использует."
    )


async def _show_photo_settings(bot: Bot, chat_id: int, user_id: int) -> None:
    look = photo_store.get_settings(user_id)
    await bot.send_message(
        chat_id,
        _photo_settings_text(look),
        reply_markup=_photo_settings_kb(look),
    )


def _schedule_photo_queue_msg(user_id: int, chat_id: int, bot: Bot) -> None:
    state = photo_store.get_state(user_id)
    if state.queue_task and not state.queue_task.done():
        state.queue_task.cancel()

    async def _show_later() -> None:
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return
        q = photo_store.get_queue(user_id)
        if not q:
            return
        old_id = state.queue_msg_id
        state.queue_msg_id = None
        if old_id:
            try:
                await bot.delete_message(chat_id, old_id)
            except Exception:
                pass
        sent = await bot.send_message(chat_id, _photo_queue_text(q), reply_markup=_photo_queue_kb())
        if sent is not None:
            state.queue_msg_id = getattr(sent, "message_id", None)

    state.queue_task = asyncio.create_task(_show_later())


def _process_photo_sync(data: bytes, action: str, look: LookSettings):
    im = load_rgb(data)
    res = apply_phone_look(im, look) if action in ("look", "both") else None
    cr = crop_uk(load_rgb(res.jpeg) if res else im) if action in ("crop", "both") else None
    return res, cr


async def _run_photo_queue(bot: Bot, chat_id: int, user_id: int, action: str) -> None:
    items = photo_store.pop_queue(user_id)
    state = photo_store.get_state(user_id)
    if state.queue_task and not state.queue_task.done():
        state.queue_task.cancel()
    state.queue_task = None
    old_id = state.queue_msg_id
    state.queue_msg_id = None
    if old_id:
        try:
            await bot.delete_message(chat_id, old_id)
        except Exception:
            pass

    if not items:
        await bot.send_message(
            chat_id,
            "Очередь пуста. Пришлите фото (лучше файлами), затем выберите действие.",
            reply_markup=photo_reply_kb(),
        )
        return

    action_names = {
        "crop": "Кадрирование 35×45",
        "look": "Обработка под iPhone",
        "both": "Обработка и кадрирование 35×45",
    }
    action_name = action_names.get(action, action)
    look = photo_store.get_settings(user_id)
    status = await bot.send_message(chat_id, f"⏳ {action_name}: {len(items)} фото…")

    results: list[tuple[bytes, str]] = []
    notes: list[str] = []

    for i, item in enumerate(items, 1):
        await bot.send_chat_action(chat_id, "upload_document")
        try:
            async with _cpu_sem:
                res, cr = await asyncio.to_thread(_process_photo_sync, item.data, action, look)
        except NoFace:
            notes.append(f"№{i} ({item.filename}): не нашёл лицо на фото (нужен портрет анфас).")
            continue
        except ModelsUnavailableError:
            notes.append(f"№{i} ({item.filename}): модели распознавания лиц недоступны на сервере.")
            continue
        except Exception:
            log.exception("photo queue item failed")
            notes.append(f"№{i} ({item.filename}): не удалось обработать изображение.")
            continue

        item_notes: list[str] = []
        if cr is not None:
            out_bytes = cr.jpeg
            out_name = f"{_photo_stem(item.filename)}_35x45.jpg"
            if abs(cr.tilt_deg) >= 0.5:
                item_notes.append(f"голова выровнена на {abs(cr.tilt_deg):.1f}°")
            if not cr.crown_found:
                item_notes.append("верх головы найден приблизительно")
            if cr.padded:
                item_notes.append("края кадра достроены фоном")
        else:
            out_bytes = res.jpeg
            out_name = f"{_photo_stem(item.filename)}_studio.jpg"
            if res.background_share < 0.05:
                item_notes.append("светлый фон не распознан, неровность ватмана не добавлена")

        if item.compressed:
            item_notes.append("пришло сжатым")
        if item_notes:
            notes.append(f"№{i} ({item.filename}): " + "; ".join(item_notes))
        results.append((out_bytes, out_name))

    from aiogram.types import InputMediaDocument

    for start in range(0, len(results), 10):
        chunk = results[start:start + 10]
        if len(chunk) == 1:
            await bot.send_document(
                chat_id,
                document=BufferedInputFile(chunk[0][0], filename=chunk[0][1]),
            )
        else:
            await bot.send_media_group(
                chat_id,
                media=[
                    InputMediaDocument(media=BufferedInputFile(data, filename=fname))
                    for data, fname in chunk
                ],
            )

    summary = f"✅ {action_name}: готово {len(results)} из {len(items)}."
    if action in ("crop", "both") and results:
        summary += "\n35×45 мм, 827×1063 px, 600 dpi. Голова ≈33.0 мм (норма UK 29–34 мм)."
    if action in ("look", "both"):
        summary += f"\nНастройки обработки: {look.describe()}."
    if notes:
        summary += "\n\nОбратите внимание:\n• " + "\n• ".join(notes)

    if status is not None:
        try:
            await status.delete()
        except Exception:
            pass
    await bot.send_message(chat_id, summary, reply_markup=photo_reply_kb())


async def _enqueue_photo_for_user(m: Message, is_doc: bool) -> None:
    user_id = m.from_user.id
    if len(photo_store.get_queue(user_id)) >= MAX_QUEUE:
        await m.answer(
            f"В очереди уже {MAX_QUEUE} фото — это максимум. Сначала обработайте или очистите их.",
            reply_markup=_photo_queue_kb(),
        )
        return

    if is_doc:
        doc = m.document
        if getattr(doc, "file_size", None) and doc.file_size > MAX_FILE_BYTES:
            await m.answer("Файл слишком большой для Telegram-бота (больше 20 МБ).")
            return
        buf = io.BytesIO()
        await m.bot.download(doc, destination=buf)
        filename = getattr(doc, "file_name", None) or f"photo_{m.message_id}.jpg"
        compressed = False
    else:
        photo_obj = m.photo[-1]
        buf = io.BytesIO()
        await m.bot.download(photo_obj, destination=buf)
        filename = f"photo_{m.message_id}.jpg"
        compressed = True

    data = buf.getvalue()
    added = photo_store.add_photo(
        user_id,
        QueuedPhoto(filename=filename, data=data, compressed=compressed),
    )
    if not added:
        await m.answer(
            f"В очереди уже {MAX_QUEUE} фото — это максимум.",
            reply_markup=_photo_queue_kb(),
        )
        return
    _schedule_photo_queue_msg(user_id, m.chat.id, m.bot)


@dp.message(F.photo)
async def on_photo(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return

    if photo_store.is_photo_mode(m.from_user.id):
        await _enqueue_photo_for_user(m, is_doc=False)
        return

    session = batch_store.get_active(m.from_user.id)
    if session is None:
        await m.answer(
            "Чтобы подбирать фото к строкам, включите режим /match (или 📸 Photo для кадрирования/обработки).",
            reply_markup=_open_match_kb(),
        )
        return

    if m.media_group_id:
        is_first = media_group_buffer.add(
            m.from_user.id,
            m.media_group_id,
            session.batch_id,
            m,
            _flush_media_group,
        )
        if is_first:
            await m.bot.send_chat_action(m.chat.id, "typing")
        return

    await m.bot.send_chat_action(m.chat.id, "typing")
    file_id, file_unique_id, gender, age, err, is_doc, photo_bytes, raw_bytes = await _analyze_telegram_media(m.bot, m)
    session.photos.append(
        PhotoItem(
            index=len(session.photos),
            file_id=file_id,
            file_unique_id=file_unique_id,
            gender=gender,
            age=age,
            vision_error=err,
            is_document=is_doc,
            photo_bytes=photo_bytes,
            raw_bytes=raw_bytes,
        )
    )
    await m.answer(_batch_status_text(session), reply_markup=_match_kb(session))


@dp.message(F.document)
async def on_document(m: Message):
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш user_id: {m.from_user.id}")
        return

    if photo_store.is_photo_mode(m.from_user.id):
        if not _is_image_document(m.document):
            fname = html.escape(getattr(m.document, "file_name", None) or "document")
            await m.answer(
                f"⚠️ Файл «{fname}» не является изображением. Пришлите фото (JPG, PNG, WEBP, BMP)."
            )
            return
        await _enqueue_photo_for_user(m, is_doc=True)
        return

    session = batch_store.get_active(m.from_user.id)
    if session is None:
        await m.answer(
            "Чтобы подбирать фото к строкам, включите режим /match (или 📸 Photo для кадрирования/обработки).",
            reply_markup=_open_match_kb(),
        )
        return

    if not _is_image_document(m.document):
        fname = html.escape(getattr(m.document, "file_name", None) or "document")
        await m.answer(
            f"⚠️ Файл «{fname}» не является изображением. Пришлите фото (JPG, PNG, WEBP, BMP)."
        )
        return

    if m.media_group_id:
        is_first = media_group_buffer.add(
            m.from_user.id,
            m.media_group_id,
            session.batch_id,
            m,
            _flush_media_group,
        )
        if is_first:
            await m.bot.send_chat_action(m.chat.id, "typing")
        return

    await m.bot.send_chat_action(m.chat.id, "typing")
    file_id, file_unique_id, gender, age, err, is_doc, photo_bytes, raw_bytes = await _analyze_telegram_media(m.bot, m)
    session.photos.append(
        PhotoItem(
            index=len(session.photos),
            file_id=file_id,
            file_unique_id=file_unique_id,
            gender=gender,
            age=age,
            vision_error=err,
            is_document=is_doc,
            photo_bytes=photo_bytes,
            raw_bytes=raw_bytes,
        )
    )
    await m.answer(_batch_status_text(session), reply_markup=_match_kb(session))


@dp.callback_query(F.data.startswith("ph:"))
async def on_ph_callback(callback: CallbackQuery):
    if not _allowed(callback):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    action = (callback.data or "").partition(":")[2]

    if action == "open":
        photo_store.enter_photo_mode(user_id)
        await callback.message.answer(
            "📸 Включён режим <b>Photo</b>. Присылайте фото (лучше файлами).",
            reply_markup=photo_reply_kb(),
        )
        await callback.answer()
        return

    if action == "clear":
        photo_store.clear_queue(user_id)
        try:
            await callback.message.edit_text("🧹 Очередь фото очищена.")
        except Exception:
            pass
        await callback.answer("Очищено")
        return

    if action in ("crop", "look", "both"):
        await callback.answer()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _run_photo_queue(callback.bot, chat_id, user_id, action)
        return

    await callback.answer()


@dp.callback_query(F.data.startswith("phs:"))
async def on_phs_callback(callback: CallbackQuery):
    if not _allowed(callback):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    user_id = callback.from_user.id
    rest = (callback.data or "").partition(":")[2]
    if rest == "noop":
        await callback.answer()
        return

    if rest == "reset":
        look = photo_store.reset_settings(user_id)
    else:
        key, _, direction = rest.partition(":")
        if key not in ("soft", "paper", "noise") or direction not in ("+", "-"):
            await callback.answer()
            return
        look = photo_store.change_setting(user_id, key, direction)

    await callback.answer("Сохранено")
    try:
        await callback.message.edit_text(
            _photo_settings_text(look),
            reply_markup=_photo_settings_kb(look),
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("pm:"))
async def on_pm_callback(callback: CallbackQuery):
    if not _allowed(callback):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    user_id = callback.from_user.id
    if callback.data == "pm:open":
        msg, kb = _match_open_text_and_kb(user_id)
        await callback.message.answer(msg, reply_markup=kb)
        await callback.answer()
        return

    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    _, batch_id, action, *rest = parts
    if not batch_store.is_valid_batch(user_id, batch_id):
        await callback.answer("Пачка устарела или закрыта. Включите /match заново.", show_alert=True)
        return

    session = batch_store.get_active(user_id)
    if session is None:
        await callback.answer("Пачка не найдена.", show_alert=True)
        return

    chat_id = callback.message.chat.id

    if action == "tcrop":
        new_val = not session.crop_35x45
        batch_store.set_crop_35x45(user_id, new_val)
        try:
            await callback.message.edit_reply_markup(reply_markup=_match_kb(session))
        except Exception:
            pass
        await callback.answer(f"Кадрирование 35×45: {'Вкл' if new_val else 'Выкл'}")
        return

    if action == "reset":
        batch_store.reset(user_id)
        media_group_buffer.cancel_batch(user_id, batch_id)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(
            f"🗑 Пачка <code>{batch_id}</code> закрыта и очищена.",
            reply_markup=main_reply_kb(),
        )
        await callback.answer("Пачка закрыта")
        return

    if action == "showp":
        if not session.photos:
            await callback.answer("В пачке нет фото.", show_alert=True)
            return
        for p in session.photos:
            age_txt = f"~{p.age} г." if p.age is not None else "? г."
            await _send_photo_item(
                callback.bot,
                chat_id,
                p,
                f"Фото #{p.index + 1} ({p.gender}, {age_txt})",
            )
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
                crop_35x45=session.crop_35x45,
            )

        unmatched = [r for r in session.records if r.index not in session.assignments]
        if unmatched:
            lines = ["⚠️ <b>Остались строки без фото:</b>"]
            for r in unmatched:
                age_txt = f"{r.age} л." if r.age is not None else "? л."
                lines.append(f"• #{r.index + 1} {html.escape(r.display_name)} ({r.gender}, {age_txt})")
            await callback.message.answer(
                "\n".join(lines),
                reply_markup=_match_kb(session, include_auto=False),
            )
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
        await _edit_or_send(callback.message, title, reply_markup=_select_record_kb(session, mode))
        await callback.answer()
        return

    if action == "back":
        if rest:
            mode = rest[0]
            if not session.records or not session.photos:
                await _edit_or_send(
                    callback.message,
                    _batch_status_text(session),
                    reply_markup=_match_kb(session),
                )
            else:
                title = "Выберите строку для дубля фото:" if mode == "d" else "Выберите строку для ручного назначения фото:"
                await _edit_or_send(
                    callback.message,
                    title,
                    reply_markup=_select_record_kb(session, mode),
                )
        else:
            await _edit_or_send(
                callback.message,
                _batch_status_text(session),
                reply_markup=_match_kb(session),
            )
        await callback.answer()
        return

    if action == "sr" and len(rest) >= 2:
        rec_idx = int(rest[0])
        mode = rest[1]
        if rec_idx < 0 or rec_idx >= len(session.records):
            await callback.answer("Строка не найдена.", show_alert=True)
            return
        rec = session.records[rec_idx]
        await _edit_or_send(
            callback.message,
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


async def _setup_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="match", description="Режим сопоставления фото и строк"),
        BotCommand(command="match_reset", description="Закрыть текущую пачку"),
        BotCommand(command="photo", description="Режим обработки и кадрирования фото"),
        BotCommand(command="help", description="Справка по работе бота"),
        BotCommand(command="id", description="Узнать свой Telegram user_id"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as e:
        log.warning("Не удалось зарегистрировать BotCommand: %s", e)


async def main():
    bot = Bot(
        config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    await _setup_bot_commands(bot)
    log.info("Бот запущен как @%s (id=%s), провайдер: %s, модели: %s",
             me.username, me.id, config.LLM_BASE_URL, config.LLM_MODELS)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())