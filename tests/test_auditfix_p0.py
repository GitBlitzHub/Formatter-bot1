import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("LLM_API_KEY", "test-key")

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
from normalizer import build_block  # noqa: E402
from schemas import Person  # noqa: E402

DL1 = "DEAKI661187JA9GY 39"
MULTI_TEXT = (
    "Judith Ann Deakin " + DL1 + " 18/11/1967\n"
    "John Smith 01/02/1980"
)


def _p1():
    return Person(gender="F", name_tokens=["Judith", "Ann", "Deakin"],
                  surname_guess="Deakin", dob="18.11.1967", dl_number=DL1)


def _p2():
    return Person(gender="M", name_tokens=["John", "Smith"],
                  surname_guess="Smith", dob="01.02.1980")


def _run_on_text(text, people, monkeypatch):
    monkeypatch.setattr(bot, "extract_people", AsyncMock(return_value=people))
    monkeypatch.setattr(bot, "_allowed", lambda m: True)
    m = SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1),
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_text(m))
    return "\n".join(c.args[0] for c in m.answer.call_args_list)


# ---------------- I-01 ----------------

def test_i01_multi_person_via_on_text_no_leak(monkeypatch):
    out = _run_on_text(MULTI_TEXT, [_p1(), _p2()], monkeypatch)
    first, second = out.split("———")[0], out.split("———")[-1]
    assert "NUMBER DL: " + DL1 in first
    assert "Ms Judith Ann" in first
    assert "DEAKI" not in second
    assert "FIRST NAME: Mr John" in second
    assert "Ms John" not in second
    assert "NUMBER DL: SMITH80201" in second


def test_i01_build_block_empty_raw_gives_own_dl_and_gender():
    b = asyncio.run(build_block(_p2(), ""))
    assert "FIRST NAME: Mr John" in b
    assert "DEAKI" not in b
    assert "NUMBER DL: SMITH80201" in b


def test_i01_single_person_dl_only_in_raw_text_preserved(monkeypatch):
    p = Person(gender="F", name_tokens=["Judith", "Ann", "Deakin"],
               surname_guess="Deakin", dob="18.11.1967")
    out = _run_on_text("Judith Ann Deakin 18/11/1967 " + DL1, [p], monkeypatch)
    assert "NUMBER DL: " + DL1 in out


def test_i01_single_person_dob_typo_dl_in_raw_text_preserved_with_warning(monkeypatch):
    p = Person(gender="F", name_tokens=["Judith", "Ann", "Deakin"],
               surname_guess="Deakin", dob="19.11.1967")
    out = _run_on_text("Judith Ann Deakin 19/11/1967 " + DL1, [p], monkeypatch)
    assert "NUMBER DL: " + DL1 in out
    assert "DOB" in out and "не совпадает" in out
