import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("LLM_API_KEY", "test-key")

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, InlineKeyboardMarkup, ReplyKeyboardMarkup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from batch_store import BatchStore, MatchAssignment, MatchRecord, PhotoItem  # noqa: E402
from schemas import Person  # noqa: E402


def _make_record(idx: int, gender: str = "M", age: int = 35, name: str | None = None) -> MatchRecord:
    display = name or f"Person {idx}"
    return MatchRecord(
        index=idx,
        person=Person(first_name="Person", last_name=str(idx), dob="01.01.1990", gender=gender),
        gender=gender,
        dob="01.01.1990",
        age=age,
        display_name=display,
        full_block=f"FIRST NAME: {display}",
        clean_block=f"FIRST NAME: {display}",
    )


def _make_photo(idx: int, gender: str = "M", age: int = 34) -> PhotoItem:
    return PhotoItem(
        index=idx,
        file_id=f"file_{idx}",
        file_unique_id=f"uniq_{idx}",
        gender=gender,
        age=age,
    )


def _fake_message(user_id: int = 101, text: str = ""):
    return SimpleNamespace(
        chat=SimpleNamespace(id=user_id),
        from_user=SimpleNamespace(id=user_id),
        text=text,
        answer=AsyncMock(),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        bot=SimpleNamespace(send_chat_action=AsyncMock(), send_photo=AsyncMock(), send_message=AsyncMock()),
    )


def test_main_reply_kb_properties():
    kb = bot.main_reply_kb()
    assert isinstance(kb, ReplyKeyboardMarkup)
    assert kb.resize_keyboard is True
    assert kb.is_persistent is True
    assert kb.one_time_keyboard is False
    assert kb.input_field_placeholder
    assert len(kb.keyboard) == 1
    texts = [btn.text for btn in kb.keyboard[0]]
    assert texts == [bot.BTN_MATCH, bot.BTN_RESET, bot.BTN_HELP]
    assert set(texts) == bot.REPLY_BUTTON_TEXTS

    # Нигде в bot.py не используется ReplyKeyboardRemove
    bot_src = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")
    assert "ReplyKeyboardRemove" not in bot_src


def test_start_help_id_reset_attach_reply_keyboard(monkeypatch):
    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    for handler in (bot.cmd_start, bot.cmd_help, bot.cmd_id, bot.cmd_match_reset):
        msg = _fake_message(101)
        asyncio.run(handler(msg))
        msg.answer.assert_awaited_once()
        rm = msg.answer.call_args.kwargs.get("reply_markup")
        assert isinstance(rm, ReplyKeyboardMarkup)

    # При сбросе через callback pm:{bid}:reset тоже выдаётся main_reply_kb
    session, _, _ = store.get_or_create(101)
    cb_msg = _fake_message(101)
    cb = SimpleNamespace(
        data=f"pm:{session.batch_id}:reset",
        from_user=SimpleNamespace(id=101),
        message=cb_msg,
        bot=cb_msg.bot,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb))
    cb_msg.answer.assert_awaited_once()
    assert isinstance(cb_msg.answer.call_args.kwargs.get("reply_markup"), ReplyKeyboardMarkup)


def test_reply_button_texts_do_not_call_llm_or_add_match_record(monkeypatch):
    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    llm_mock = AsyncMock(return_value=[Person(first_name="Fake", last_name="Person")])
    monkeypatch.setattr(bot, "extract_people", llm_mock)

    session, _, _ = store.get_or_create(102)
    assert len(session.records) == 0

    for btn_text in bot.REPLY_BUTTON_TEXTS:
        msg = _fake_message(102, text=btn_text)
        asyncio.run(bot.on_text(msg))

    assert llm_mock.call_count == 0
    assert len(session.records) == 0

    # Проверка порядка регистрации хендлеров в Dispatcher: кнопки ДО on_text
    handlers = bot.dp.message.handlers
    callback_names = [h.callback.__name__ for h in handlers]
    on_text_idx = callback_names.index("on_text")
    for btn_handler in ("on_btn_match", "on_btn_reset", "on_btn_help"):
        assert btn_handler in callback_names
        assert callback_names.index(btn_handler) < on_text_idx


def test_reply_btn_match_starts_and_shows_session(monkeypatch):
    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    msg1 = _fake_message(103, text=bot.BTN_MATCH)
    asyncio.run(bot.on_btn_match(msg1))
    session = store.get_active(103)
    assert session is not None
    assert isinstance(msg1.answer.call_args.kwargs.get("reply_markup"), InlineKeyboardMarkup)

    # Повторное нажатие показывает актуальный статус существующей пачки
    session.records.append(_make_record(0))
    msg2 = _fake_message(103, text=bot.BTN_MATCH)
    asyncio.run(bot.on_btn_match(msg2))
    assert "Строк: <b>1</b>" in msg2.answer.call_args.args[0]
    assert isinstance(msg2.answer.call_args.kwargs.get("reply_markup"), InlineKeyboardMarkup)


def test_reply_btn_reset_closes_active_session(monkeypatch):
    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    cancel_calls = []
    monkeypatch.setattr(bot.media_group_buffer, "cancel_batch", lambda uid, bid: cancel_calls.append((uid, bid)))

    session, _, _ = store.get_or_create(104)
    msg = _fake_message(104, text=bot.BTN_RESET)
    asyncio.run(bot.on_btn_reset(msg))

    assert store.get_active(104) is None
    assert cancel_calls == [(104, session.batch_id)]
    assert isinstance(msg.answer.call_args.kwargs.get("reply_markup"), ReplyKeyboardMarkup)


def test_setup_bot_commands_registers_expected_commands():
    ok_bot = SimpleNamespace(set_my_commands=AsyncMock())
    asyncio.run(bot._setup_bot_commands(ok_bot))
    ok_bot.set_my_commands.assert_awaited_once()
    cmds = ok_bot.set_my_commands.call_args.args[0]
    assert all(isinstance(c, BotCommand) for c in cmds)
    assert [c.command for c in cmds] == ["match", "match_reset", "help", "id"]

    # При ошибке API не падает
    failing_bot = SimpleNamespace(set_my_commands=AsyncMock(side_effect=RuntimeError("network down")))
    asyncio.run(bot._setup_bot_commands(failing_bot))


def test_match_inline_keyboards_compact_and_callback_under_64_bytes():
    store = BatchStore(today_fn=lambda: "2026-11-23")
    session, _, _ = store.get_or_create(105)
    for i in range(4):
        session.records.append(_make_record(i))
    for i in range(5):
        session.photos.append(_make_photo(i))

    clean = bot._clean_kb()
    assert len(clean.inline_keyboard) == 1
    assert clean.inline_keyboard[0][0].text == "🧹 Только строки"

    mkb = bot._match_kb(session)
    assert len(mkb.inline_keyboard) <= 3
    assert [b.text for b in mkb.inline_keyboard[0]] == ["🔗 Автоподбор"]
    assert [b.text for b in mkb.inline_keyboard[1]] == ["✋ Вручную", "👯 Дубль"]
    assert [b.text for b in mkb.inline_keyboard[2]] == ["🖼 Фото (5)", "🗑 Сброс"]

    # При include_auto=False (нехватка фото) порядок Вручную -> Дубль сохраняется без инверсии
    mkb_no_auto = bot._match_kb(session, include_auto=False)
    assert [b.text for b in mkb_no_auto.inline_keyboard[0]] == ["✋ Вручную", "👯 Дубль"]

    rkb = bot._select_record_kb(session, "m")
    assert [b.text for b in rkb.inline_keyboard[-1]] == ["⬅️ Назад", "🗑 Сброс"]
    assert rkb.inline_keyboard[-1][0].callback_data == f"pm:{session.batch_id}:back"

    pkb = bot._select_photo_kb(session, 0, "m")
    # 5 фото по 2 в ряд -> 3 ряда с фото + 1 нижний ряд = 4 ряда
    assert len(pkb.inline_keyboard) == 4
    assert len(pkb.inline_keyboard[0]) == 2
    assert len(pkb.inline_keyboard[1]) == 2
    assert len(pkb.inline_keyboard[2]) == 1
    assert [b.text for b in pkb.inline_keyboard[-1]] == ["⬅️ Назад", "🖼 Показать фото"]
    assert pkb.inline_keyboard[-1][0].callback_data == f"pm:{session.batch_id}:back:m"

    for kb in (clean, mkb, mkb_no_auto, rkb, pkb, bot._open_match_kb()):
        for row in kb.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode("utf-8")) <= 64


def test_edit_text_navigation_and_back_callback(monkeypatch):
    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    session, _, _ = store.get_or_create(106)
    session.records.append(_make_record(0, name="Alice"))
    session.photos.append(_make_photo(0))
    bid = session.batch_id

    # 1) man -> edit_text со списком строк
    msg = _fake_message(106)
    asyncio.run(bot.on_pm_callback(SimpleNamespace(
        data=f"pm:{bid}:man", from_user=SimpleNamespace(id=106), message=msg, bot=msg.bot, answer=AsyncMock(),
    )))
    msg.edit_text.assert_awaited_once()
    assert not msg.answer.called
    assert "Выберите строку" in msg.edit_text.call_args.args[0]

    # 2) sr -> edit_text со списком фото
    msg.edit_text.reset_mock()
    asyncio.run(bot.on_pm_callback(SimpleNamespace(
        data=f"pm:{bid}:sr:0:m", from_user=SimpleNamespace(id=106), message=msg, bot=msg.bot, answer=AsyncMock(),
    )))
    msg.edit_text.assert_awaited_once()
    assert not msg.answer.called
    assert "Выберите фото" in msg.edit_text.call_args.args[0]

    # 3) back:m -> возврат к выбору строк через edit_text
    msg.edit_text.reset_mock()
    asyncio.run(bot.on_pm_callback(SimpleNamespace(
        data=f"pm:{bid}:back:m", from_user=SimpleNamespace(id=106), message=msg, bot=msg.bot, answer=AsyncMock(),
    )))
    msg.edit_text.assert_awaited_once()
    assert "Выберите строку" in msg.edit_text.call_args.args[0]

    # 4) back -> возврат к главной панели пачки через edit_text
    msg.edit_text.reset_mock()
    asyncio.run(bot.on_pm_callback(SimpleNamespace(
        data=f"pm:{bid}:back", from_user=SimpleNamespace(id=106), message=msg, bot=msg.bot, answer=AsyncMock(),
    )))
    msg.edit_text.assert_awaited_once()
    assert "Режим /match" in msg.edit_text.call_args.args[0]


def test_edit_text_fallback_to_answer_on_telegram_bad_request(monkeypatch):
    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    session, _, _ = store.get_or_create(107)
    session.records.append(_make_record(0))
    session.photos.append(_make_photo(0))
    bid = session.batch_id

    msg = _fake_message(107)
    msg.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(method="editMessageText", message="Bad Request: message can't be edited")
    )
    asyncio.run(bot.on_pm_callback(SimpleNamespace(
        data=f"pm:{bid}:dup", from_user=SimpleNamespace(id=107), message=msg, bot=msg.bot, answer=AsyncMock(),
    )))
    msg.edit_text.assert_awaited_once()
    msg.answer.assert_awaited_once()
    assert "дубля" in msg.answer.call_args.args[0]


def test_on_photo_and_doc_without_match_offers_quick_start_and_pm_open(monkeypatch):
    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    # Фото вне /match
    m_photo = _fake_message(108)
    m_photo.photo = [SimpleNamespace(file_id="p1", file_unique_id="u1")]
    asyncio.run(bot.on_photo(m_photo))
    rm = m_photo.answer.call_args.kwargs.get("reply_markup")
    assert isinstance(rm, InlineKeyboardMarkup)
    assert rm.inline_keyboard[0][0].callback_data == "pm:open"

    # Документ вне /match
    m_doc = _fake_message(108)
    m_doc.document = SimpleNamespace(file_id="d1", file_unique_id="u2", mime_type="image/jpeg", file_name="a.jpg")
    asyncio.run(bot.on_document(m_doc))
    rm_doc = m_doc.answer.call_args.kwargs.get("reply_markup")
    assert isinstance(rm_doc, InlineKeyboardMarkup)
    assert rm_doc.inline_keyboard[0][0].callback_data == "pm:open"

    # Нажатие pm:open создаёт пачку
    cb_msg = _fake_message(108)
    cb = SimpleNamespace(
        data="pm:open",
        from_user=SimpleNamespace(id=108),
        message=cb_msg,
        bot=cb_msg.bot,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb))
    assert store.get_active(108) is not None
    assert isinstance(cb_msg.answer.call_args.kwargs.get("reply_markup"), InlineKeyboardMarkup)
