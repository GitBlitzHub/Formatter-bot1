import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("LLM_API_KEY", "test-key")

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardMarkup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from dlgen import generate_dl  # noqa: E402
from normalizer import build_block, strip_to_clean_blocks  # noqa: E402
from schemas import Person  # noqa: E402


FOUR_RECORDS_DIRTY_PACK = """FIRST NAME: Mr Anthony J
LAST NAME: BUCKTHOUGHT
DOB: 12.03.1975
NUMBER DL: BUCKT703125AJ9XX
ADRESS: 10 High Street, London, SW1A 1AA

<i>🟡 Посткод и улица реальны, дом подтвердить не удалось
• Посткод подтверждён: SW1A 1AA (Westminster)
⚠️ DL не был указан — номер сгенерирован автоматически</i>

———————————

FIRST NAME: Mr Michaek J
LAST NAME: MALINOWSKI
DOB: 05.07.1982
NUMBER DL: MALIN807052MJ9YY
ADRESS: 22 Baker Street, London, NW1 6XE

<i>✅ Адрес подтверждён по базе Royal Mail (PAF)
• Найдено точное совпадение дома</i>

———————————

FIRST NAME: Mr Gheorghe
LAST NAME: HACHE
DOB: 21.11.1990
NUMBER DL: HACHE911210G99ZZ
ADRESS: 5 King Road, Manchester, M1 1AA

<i>🟡 Посткод и улица реальны, дом подтвердить не удалось
• Улица найдена в OpenStreetMap
⚠️ Проверьте написание имени</i>

———————————

FIRST NAME: Mr Kyle M
LAST NAME: BRACE
DOB: 30.01.1988
NUMBER DL: BRACE801308KM9WW
ADRESS: 

<i>⚠️ Адрес не указан</i>"""


FOUR_RECORDS_EXPECTED_CLEAN = """FIRST NAME: Mr Anthony J
LAST NAME: BUCKTHOUGHT
DOB: 12.03.1975
NUMBER DL: BUCKT703125AJ9XX
ADRESS: 10 High Street, London, SW1A 1AA

FIRST NAME: Mr Michaek J
LAST NAME: MALINOWSKI
DOB: 05.07.1982
NUMBER DL: MALIN807052MJ9YY
ADRESS: 22 Baker Street, London, NW1 6XE

FIRST NAME: Mr Gheorghe
LAST NAME: HACHE
DOB: 21.11.1990
NUMBER DL: HACHE911210G99ZZ
ADRESS: 5 King Road, Manchester, M1 1AA

FIRST NAME: Mr Kyle M
LAST NAME: BRACE
DOB: 30.01.1988
NUMBER DL: BRACE801308KM9WW
ADRESS: """


def test_always_generates_fresh_dl_ignoring_input_dl():
    # Даже если в p.dl_number и raw_text передан валидный или чужой DL с суффиксом 39,
    # build_block обязан сгенерировать 16-символьный номер через generate_dl(...)
    p = Person(
        title="Ms",
        gender="F",
        name_tokens=["Judith", "Ann", "Deakin"],
        surname_guess="Deakin",
        dob="18.11.1967",
        dl_number="DEAKI661187JA9GY 39",
    )
    out = asyncio.run(build_block(p, "Judith Ann Deakin DEAKI661187JA9GY 39"))
    expected_dl = generate_dl("Deakin", "Judith", "Ann", "18.11.1967", "F")
    assert expected_dl == "DEAKI661187JA9MN"
    assert f"NUMBER DL: {expected_dl}\n" in out
    assert "DEAKI661187JA9GY" not in out
    assert " 39" not in out
    assert "DL не был указан" not in out
    assert "не совпадает с датой из номера прав" not in out


def test_foreign_dl_in_input_does_not_corrupt_gender_or_surname():
    # Чужой женский DL (SMITH551187...) у мужчины Mr John Brown не должен менять пол или добавлять warning
    p = Person(
        title="Mr",
        gender="M",
        name_tokens=["John", "Brown"],
        surname_guess="Brown",
        dob="01.02.1980",
        dl_number="DEAKI661187JA9GY 39",
    )
    out = asyncio.run(build_block(p, "Mr John Brown DEAKI661187JA9GY 39"))
    expected_dl = generate_dl("Brown", "John", "", "01.02.1980", "M")
    assert "FIRST NAME: Mr John\n" in out
    assert "LAST NAME: BROWN\n" in out
    assert f"NUMBER DL: {expected_dl}\n" in out
    assert "DEAKI" not in out
    assert "не совпадает с номером прав" not in out


def test_no_gender_warning_has_updated_text():
    p = Person(
        title="",
        gender="U",
        name_tokens=["Lee", "BARNARD"],
        surname_guess="BARNARD",
        dob="10.05.1983",
    )
    out = asyncio.run(build_block(p, ""))
    assert "DL не сгенерирован: не удалось определить пол (нужен титул Mr/Ms)" in out
    assert "или DL" not in out


def test_strip_to_clean_blocks_four_records_pack():
    cleaned = strip_to_clean_blocks(FOUR_RECORDS_DIRTY_PACK)
    assert cleaned == FOUR_RECORDS_EXPECTED_CLEAN

    blocks = cleaned.split("\n\n")
    assert len(blocks) == 4
    for b in blocks:
        lines = b.splitlines()
        assert len(lines) == 5
        assert lines[0].startswith("FIRST NAME: ")
        assert lines[1].startswith("LAST NAME: ")
        assert lines[2].startswith("DOB: ")
        assert lines[3].startswith("NUMBER DL: ")
        assert lines[4].startswith("ADRESS: ")

    # Проверка, что у 4-й записи (Kyle M BRACE) сохранился пустой ADRESS:
    assert blocks[3].splitlines()[4] == "ADRESS: "

    # Никаких диагностических маркеров и разделителей
    for forbidden in ("🟡", "✅", "❌", "•", "⚠️", "———", "<i>", "</i>"):
        assert forbidden not in cleaned


def test_strip_to_clean_blocks_trimmed_trailing_space_and_idempotency():
    # Если Telegram убрал пробел после "ADRESS:" у 4-й записи
    trimmed_input = FOUR_RECORDS_DIRTY_PACK.replace("ADRESS: \n", "ADRESS:\n")
    cleaned = strip_to_clean_blocks(trimmed_input)
    assert cleaned == FOUR_RECORDS_EXPECTED_CLEAN
    # Повторный вызов идемпотентен
    assert strip_to_clean_blocks(cleaned) == FOUR_RECORDS_EXPECTED_CLEAN


def test_strip_to_clean_blocks_mixed_with_error_and_empty():
    mixed = FOUR_RECORDS_DIRTY_PACK + "\n\n———————————\n\n⚠️ Ошибка обработки записи: <code>ValueError: boom</code>"
    assert strip_to_clean_blocks(mixed) == FOUR_RECORDS_EXPECTED_CLEAN
    assert strip_to_clean_blocks("⚠️ Ошибка обработки записи: <code>ValueError</code>") == ""
    assert strip_to_clean_blocks("") == ""


def test_on_text_attaches_clean_inline_keyboard(monkeypatch):
    p = Person(
        title="Mr",
        gender="M",
        name_tokens=["John", "Smith"],
        surname_guess="Smith",
        dob="01.02.1980",
    )
    monkeypatch.setattr(bot, "extract_people", AsyncMock(return_value=[p]))
    monkeypatch.setattr(bot, "_allowed", lambda m: True)
    m = SimpleNamespace(
        text="Mr John Smith 01.02.1980",
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1),
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_text(m))
    assert m.answer.call_count == 1
    call = m.answer.call_args
    assert "FIRST NAME: Mr John" in call.args[0]
    kb = call.kwargs.get("reply_markup")
    assert isinstance(kb, InlineKeyboardMarkup)
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data == "clean_blocks"


def test_on_clean_blocks_callback_edits_message(monkeypatch):
    monkeypatch.setattr(bot, "_allowed", lambda c: True)
    msg = SimpleNamespace(
        text=FOUR_RECORDS_DIRTY_PACK,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )
    cb = SimpleNamespace(
        data="clean_blocks",
        from_user=SimpleNamespace(id=1),
        message=msg,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_clean_blocks(cb))
    msg.edit_text.assert_awaited_once_with(FOUR_RECORDS_EXPECTED_CLEAN, reply_markup=None)
    cb.answer.assert_awaited_once_with()


def test_on_clean_blocks_callback_handles_empty_blocks_and_html_entities(monkeypatch):
    monkeypatch.setattr(bot, "_allowed", lambda c: True)
    # 1) Сообщение без валидных блоков (только ошибка) -> не вызываем edit_text("")
    msg_err = SimpleNamespace(
        text="⚠️ Ошибка обработки записи: ValueError",
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )
    cb_err = SimpleNamespace(
        data="clean_blocks",
        from_user=SimpleNamespace(id=1),
        message=msg_err,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_clean_blocks(cb_err))
    msg_err.edit_text.assert_not_called()
    msg_err.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    cb_err.answer.assert_awaited_once()

    # 2) Адрес с амперсандом '&' и апострофом "'" при ParseMode.HTML
    dirty_amp = (
        "FIRST NAME: Mr John O'Brien\n"
        "LAST NAME: O'BRIEN\n"
        "DOB: 01.02.1980\n"
        "NUMBER DL: OBRIE802010J99XX\n"
        "ADRESS: Flat 1, B&Q House, London, SW1A 1AA\n\n"
        "🟡 Посткод и улица реальны"
    )
    msg_amp = SimpleNamespace(
        text=dirty_amp,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )
    cb_amp = SimpleNamespace(
        data="clean_blocks",
        from_user=SimpleNamespace(id=1),
        message=msg_amp,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_clean_blocks(cb_amp))
    edited_arg = msg_amp.edit_text.call_args.args[0]
    assert "O'Brien" in edited_arg
    assert "B&amp;Q House" in edited_arg
    assert "🟡" not in edited_arg
