import importlib
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("LLM_API_KEY", "test-key")

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from normalizer import build_block, build_block_details  # noqa: E402
from schemas import Person  # noqa: E402


def test_config_vision_model_fallback_to_llm_env(monkeypatch):
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    monkeypatch.delenv("VISION_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1/")
    importlib.reload(config)

    assert config.VISION_MODEL == config.DEFAULT_VISION_MODEL
    assert config.VISION_API_KEY == "llm-secret"
    assert config.VISION_BASE_URL == "https://api.groq.com/openai/v1"

    monkeypatch.setenv("VISION_MODEL", "custom/vision-model")
    monkeypatch.setenv("VISION_API_KEY", "vision-secret")
    monkeypatch.setenv("VISION_BASE_URL", "https://custom.vision/v1/")
    importlib.reload(config)

    assert config.VISION_MODEL == "custom/vision-model"
    assert config.VISION_API_KEY == "vision-secret"
    assert config.VISION_BASE_URL == "https://custom.vision/v1"

    # Восстановим дефолты для остальных тестов
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    monkeypatch.delenv("VISION_BASE_URL", raising=False)
    importlib.reload(config)


def test_build_block_details_compatible_with_build_block():
    p = Person(
        title="Mr",
        gender="M",
        name_tokens=["Anthony", "Buckthought"],
        surname_guess="Buckthought",
        dob="12/03/1975",
    )
    full_block, gender, dob, display_name = asyncio.run(build_block_details(p))
    legacy_block = asyncio.run(build_block(p))

    assert full_block == legacy_block
    assert gender == "M"
    assert dob == "12.03.1975"
    assert "Anthony" in display_name
    assert "Buckthought" in display_name


def test_vision_parse_valid_json():
    from vision_client import _parse_vision_json

    res = _parse_vision_json('{"gender": "M", "age": 34}')
    assert res.gender == "M"
    assert res.age == 34
    assert res.error == ""


def test_vision_normalizes_gender_and_age_variants():
    from vision_client import _parse_vision_json

    r1 = _parse_vision_json('```json\n{"gender": "female", "age": "approx 29 years"}\n```')
    assert r1.gender == "F"
    assert r1.age == 29

    r2 = _parse_vision_json('{"gender": "man", "age": 42.6}')
    assert r2.gender == "M"
    assert r2.age == 43 or r2.age == 42

    r3 = _parse_vision_json('{"gender": "unknown", "age": 250}')
    assert r3.gender == "U"
    assert r3.age is None


def test_vision_handles_invalid_json_and_api_error_gracefully(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import vision_client

    # 1) Невалидный JSON от API
    bad_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not json at all"))]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=bad_resp))
        )
    )
    monkeypatch.setattr(vision_client, "_client", fake_client)

    res1 = asyncio.run(vision_client.analyze_photo(b"fake-jpg-bytes"))
    assert res1.gender == "U"
    assert res1.age is None
    assert res1.error != ""

    # 2) Ошибка API (например, 500 или RuntimeError)
    monkeypatch.setattr(vision_client, "_BASE_DELAY", 0.0)
    monkeypatch.setattr(vision_client, "_MAX_ATTEMPTS", 2)
    err_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("API down 500")))
        )
    )
    monkeypatch.setattr(vision_client, "_client", err_client)

    res2 = asyncio.run(vision_client.analyze_photo(b"fake-jpg-bytes"))
    assert res2.gender == "U"
    assert res2.age is None
    assert "API down" in res2.error


def _make_record(idx: int, gender: str, dob: str = "", age: int | None = None):
    from batch_store import MatchRecord
    from schemas import Person

    return MatchRecord(
        index=idx,
        person=Person(gender=gender, dob=dob),
        gender=gender,
        dob=dob,
        age=age,
        display_name=f"Person {idx}",
        full_block=f"FIRST NAME: Person {idx}\nLAST NAME: TEST\nDOB: {dob}\nNUMBER DL: \nADRESS: ",
        clean_block=f"FIRST NAME: Person {idx}\nLAST NAME: TEST\nDOB: {dob}\nNUMBER DL: \nADRESS: ",
    )


def _make_photo(idx: int, gender: str, age: int | None = None):
    from batch_store import PhotoItem

    return PhotoItem(
        index=idx,
        file_id=f"file_{idx}",
        file_unique_id=f"uniq_{idx}",
        gender=gender,
        age=age,
    )


def test_date_change_auto_clears_stale_batch():
    from batch_store import BatchStore

    current_date = ["2026-11-23"]
    store = BatchStore(today_fn=lambda: current_date[0])
    session, created, cleared = store.get_or_create(user_id=101)
    assert created is True
    assert cleared is False
    assert session.date_str == "2026-11-23"
    assert len(session.batch_id) == 6
    session.photos.append(_make_photo(0, "M", 30))

    # Та же дата — пачка сохраняется
    same_session = store.get_active(101)
    assert same_session is session
    assert len(same_session.photos) == 1

    # Смена даты — устаревшая пачка автоматически блокируется/очищается
    current_date[0] = "2026-11-24"
    assert store.get_active(101) is None

    new_session, created2, cleared2 = store.get_or_create(user_id=101)
    assert created2 is True
    assert cleared2 is True
    assert new_session.batch_id != session.batch_id
    assert new_session.date_str == "2026-11-24"
    assert new_session.photos == []


def test_manual_reset_closes_batch_and_rejects_old_callbacks():
    from batch_store import BatchStore

    store = BatchStore(today_fn=lambda: "2026-11-23")
    session, _, _ = store.get_or_create(user_id=202)
    old_id = session.batch_id

    removed = store.reset(202)
    assert removed is session
    assert removed.is_closed is True
    assert store.get_active(202) is None
    assert store.is_valid_batch(202, old_id) is False


def test_matcher_strict_gender_separation():
    from matcher import match_batch

    records = [_make_record(0, "M", age=30), _make_record(1, "F", age=25)]
    photos = [_make_photo(0, "F", age=30), _make_photo(1, "M", age=32)]
    res = match_batch(records, photos)
    assert res[0].photo_index == 1  # M -> M
    assert res[1].photo_index == 0  # F -> F


def test_matcher_optimal_non_greedy_assignment():
    from matcher import match_batch

    # Записи: 20 и 30. Фото: 22 и 23.
    # Жадный для 20 взял бы 22 (diff=2), оставив 30 с 23 (diff=7) => сумма 9, квадраты 4+49=53.
    # Оптимальный: 20->22 (diff=2), 30->23 (diff=7) vs 20->23 (diff=3), 30->22 (diff=8) -> тут оба 9,
    # но с 20 и 35, фото 25 и 26:
    # Жадный: 20->25 (5), 35->26 (9) -> 25+81=106.
    # Другой кейс: Записи 25, 40; Фото 26, 20.
    # Жадный отсортирует (25,26)=1 -> останется (40,20)=20, сумма 21!
    # Оптимальный: (25,20)=5 и (40,26)=14, сумма 19!
    records = [_make_record(0, "M", age=25), _make_record(1, "M", age=40)]
    photos = [_make_photo(0, "M", age=26), _make_photo(1, "M", age=20)]
    res = match_batch(records, photos)
    assert res[0].photo_index == 1  # 25 -> 20 (diff=5)
    assert res[1].photo_index == 0  # 40 -> 26 (diff=14)


def test_matcher_no_duplicate_photo_in_auto():
    from matcher import match_batch

    records = [_make_record(0, "M", age=30), _make_record(1, "M", age=31)]
    photos = [_make_photo(0, "M", age=30)]
    res = match_batch(records, photos)
    assert len(res) == 1
    used_photos = [a.photo_index for a in res.values()]
    assert len(used_photos) == len(set(used_photos))


def test_matcher_large_age_diff_adds_warning():
    from matcher import match_batch

    records = [_make_record(0, "F", age=22)]
    photos = [_make_photo(0, "F", age=45)]
    res = match_batch(records, photos)
    assert res[0].photo_index == 0
    assert res[0].age_diff == 23
    assert "⚠️" in res[0].warning


def test_matcher_no_matching_gender_leaves_unassigned():
    from matcher import match_batch

    records = [_make_record(0, "M", age=30), _make_record(1, "U", age=30)]
    photos = [_make_photo(0, "F", age=30)]
    res = match_batch(records, photos)
    assert res == {}


def _make_fake_message(user_id: int = 1, text: str = "", photo_id: str | None = None, media_group_id: str | None = None):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    photos = None
    if photo_id is not None:
        photos = [
            SimpleNamespace(file_id=f"{photo_id}_small", file_unique_id=f"u_{photo_id}_s"),
            SimpleNamespace(file_id=photo_id, file_unique_id=f"u_{photo_id}"),
        ]

    async def _download(file_obj, destination):
        destination.write(b"fake-jpeg-bytes")

    return SimpleNamespace(
        text=text,
        photo=photos,
        media_group_id=media_group_id,
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=user_id),
        bot=SimpleNamespace(
            send_chat_action=AsyncMock(),
            download=AsyncMock(side_effect=_download),
            send_photo=AsyncMock(),
            send_message=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def test_on_text_without_match_keeps_default_behavior(monkeypatch):
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore
    from schemas import Person

    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)
    p = Person(title="Mr", gender="M", name_tokens=["John", "Smith"], surname_guess="Smith", dob="01.02.1980")
    monkeypatch.setattr(bot, "extract_people", AsyncMock(return_value=[p]))

    m = _make_fake_message(user_id=500, text="John Smith 01/02/1980")
    asyncio.run(bot.on_text(m))

    assert store.get_active(500) is None
    assert m.answer.called
    out = m.answer.call_args_list[0].args[0]
    assert "FIRST NAME: Mr John" in out


def test_match_command_creates_session_with_date_and_batch_id(monkeypatch):
    import bot
    from batch_store import BatchStore

    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    m = _make_fake_message(user_id=501, text="/match")
    asyncio.run(bot.cmd_match(m))

    session = store.get_active(501)
    assert session is not None
    assert session.date_str == "2026-11-23"
    assert len(session.batch_id) == 6
    assert m.answer.called
    assert session.batch_id in m.answer.call_args_list[0].args[0]


def test_batch_accumulates_people_and_single_photo(monkeypatch):
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore
    from schemas import Person
    from vision_client import VisionResult

    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)
    p = Person(title="Mr", gender="M", name_tokens=["Anthony", "Buckthought"], surname_guess="Buckthought", dob="12.03.1975")
    monkeypatch.setattr(bot, "extract_people", AsyncMock(return_value=[p]))
    monkeypatch.setattr(bot, "analyze_photo", AsyncMock(return_value=VisionResult(gender="M", age=50)))

    store.get_or_create(502)

    m_text = _make_fake_message(user_id=502, text="Anthony Buckthought 12/03/1975")
    asyncio.run(bot.on_text(m_text))

    m_photo = _make_fake_message(user_id=502, photo_id="photo_file_1")
    asyncio.run(bot.on_photo(m_photo))

    session = store.get_active(502)
    assert len(session.records) == 1
    assert session.records[0].gender == "M"
    assert session.records[0].age == 51
    assert len(session.photos) == 1
    assert session.photos[0].file_id == "photo_file_1"
    assert session.photos[0].gender == "M"
    assert session.photos[0].age == 50
    assert "Строк: <b>1</b> | Фото: <b>1</b>" in m_photo.answer.call_args_list[-1].args[0]


def test_media_group_album_batched_once(monkeypatch):
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore, MediaGroupBuffer
    from vision_client import VisionResult

    store = BatchStore(today_fn=lambda: "2026-11-23")
    buf = MediaGroupBuffer(delay=0.01)
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "media_group_buffer", buf)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)
    monkeypatch.setattr(bot, "analyze_photo", AsyncMock(return_value=VisionResult(gender="F", age=28)))

    store.get_or_create(503)

    async def _run():
        m1 = _make_fake_message(user_id=503, photo_id="alb_1", media_group_id="grp_99")
        m2 = _make_fake_message(user_id=503, photo_id="alb_2", media_group_id="grp_99")
        m3 = _make_fake_message(user_id=503, photo_id="alb_3", media_group_id="grp_99")
        await bot.on_photo(m1)
        await bot.on_photo(m2)
        await bot.on_photo(m3)
        await asyncio.sleep(0.05)
        return m1, m2, m3

    m1, m2, m3 = asyncio.run(_run())
    session = store.get_active(503)
    assert len(session.photos) == 3
    total_answers = m1.answer.call_count + m2.answer.call_count + m3.answer.call_count
    assert total_answers == 1


def test_duplicate_photo_flow_assigns_used_photo_to_unmatched_record(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore

    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    session, _, _ = store.get_or_create(504)
    session.records.append(_make_record(0, "M", dob="01.01.1990", age=36))
    session.records.append(_make_record(1, "M", dob="01.01.1991", age=35))
    session.photos.append(_make_photo(0, "M", age=36))

    # Автоподбор назначит фото 0 только на одну строку
    cb_auto = SimpleNamespace(
        data=f"pm:{session.batch_id}:auto",
        from_user=SimpleNamespace(id=504),
        message=SimpleNamespace(chat=SimpleNamespace(id=504), answer=AsyncMock(), edit_reply_markup=AsyncMock()),
        bot=SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock()),
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb_auto))
    assert len(session.assignments) == 1
    unmatched_idx = 1 if 0 in session.assignments else 0

    # Дубль фото 0 на оставшуюся строку
    cb_dup = SimpleNamespace(
        data=f"pm:{session.batch_id}:sp:{unmatched_idx}:0:d",
        from_user=SimpleNamespace(id=504),
        message=SimpleNamespace(chat=SimpleNamespace(id=504), answer=AsyncMock(), edit_reply_markup=AsyncMock()),
        bot=SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock()),
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb_dup))
    assert session.assignments[unmatched_idx].mode == "duplicate"
    assert session.assignments[unmatched_idx].photo_index == 0
    assert cb_dup.bot.send_photo.called


def test_manual_selection_without_auto_match(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore

    store = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", store)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    session, _, _ = store.get_or_create(505)
    session.records.append(_make_record(0, "M", dob="01.01.1990", age=36))
    session.photos.append(_make_photo(0, "F", age=25))

    # Ручной выбор без запуска match_batch (можно даже любое фото пачки)
    cb_man = SimpleNamespace(
        data=f"pm:{session.batch_id}:sp:0:0:m",
        from_user=SimpleNamespace(id=505),
        message=SimpleNamespace(chat=SimpleNamespace(id=505), answer=AsyncMock(), edit_reply_markup=AsyncMock()),
        bot=SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock()),
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb_man))
    assert session.assignments[0].mode == "manual"
    assert session.assignments[0].photo_index == 0
    assert cb_man.bot.send_photo.called


def test_send_match_card_photo_with_caption():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import bot
    from matcher import build_assignment

    rec = _make_record(0, "M", dob="12.03.1975", age=51)
    ph = _make_photo(0, "M", age=50)
    assign = build_assignment(rec, ph, mode="auto")

    fake_bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())
    asyncio.run(bot.send_match_card(fake_bot, 777, rec, ph, assign))

    fake_bot.send_photo.assert_called_once()
    kwargs = fake_bot.send_photo.call_args.kwargs
    assert kwargs["photo"] == "file_0"
    assert "FIRST NAME: Person 0" in kwargs["caption"]
    assert not fake_bot.send_message.called


def test_caption_respects_1024_char_limit_with_overflow():
    import bot
    from batch_store import MatchAssignment

    rec = _make_record(0, "M", dob="12.03.1975", age=51)
    # Делаем очень длинный full_block с HTML-тегами и длинный clean_block со спецсимволами
    rec.clean_block = "FIRST NAME: " + ("A&B<" * 300)
    rec.full_block = "FIRST NAME: Test\n<i>" + ("NOTE " * 300) + "</i>"
    assign = MatchAssignment(
        record_index=0,
        photo_index=0,
        mode="auto",
        age_diff=15,
        warning="⚠️ Большая разница возраста: в строке 51 л., на фото ~36 л. (Δ15)",
    )

    caption, overflow = bot.format_match_caption(rec, assign)
    assert len(caption) <= 1024
    assert overflow == rec.full_block
    # Проверка на отсутствие незакрытых HTML-тегов
    assert caption.count("<i>") == caption.count("</i>")


def test_all_callback_data_under_64_bytes():
    import bot
    from batch_store import BatchStore

    store = BatchStore(today_fn=lambda: "2026-11-23")
    session, _, _ = store.get_or_create(506)
    for i in range(15):
        session.records.append(_make_record(i, "M", age=30))
        session.photos.append(_make_photo(i, "M", age=30))

    for kb in (
        bot._match_kb(session),
        bot._select_record_kb(session, "m"),
        bot._select_photo_kb(session, 14, "m"),
    ):
        for row in kb.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode("utf-8")) <= 64



