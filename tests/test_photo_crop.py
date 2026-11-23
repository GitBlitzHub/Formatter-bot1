import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("LLM_API_KEY", "test-key")

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_jpeg_bytes(w: int = 800, h: int = 1000, bg=(210, 220, 235), face=(220, 175, 145)) -> bytes:
    arr = np.full((h, w, 3), bg, dtype=np.uint8)
    # Тёплая область лица в центре, чтобы apply_phone_look и _crown_y имели реалистичные пиксели
    y0, y1 = int(h * 0.25), int(h * 0.72)
    x0, x1 = int(w * 0.30), int(w * 0.70)
    arr[y0:y1, x0:x1] = face
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _mock_landmarks_for_size(w: int, h: int, tilt_deg: float = 0.0) -> np.ndarray:
    """Генерирует правдоподобные 68 landmarks в пикселях с заданным наклоном глаз."""
    pts = np.zeros((68, 2), dtype=float)
    cx, cy = w * 0.5, h * 0.45
    fw = w * 0.35
    rad = np.radians(tilt_deg)
    rot = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])

    def _p(dx: float, dy: float) -> np.ndarray:
        return np.array([cx, cy]) + (np.array([dx * fw, dy * fw]) @ rot.T)

    # Контур лица 0..16
    pts[0] = _p(-0.5, 0.0)
    pts[16] = _p(0.5, 0.0)
    pts[8] = _p(0.0, 0.75)  # подбородок
    # Брови 17..26
    for i, x in enumerate(np.linspace(-0.38, -0.08, 5)):
        pts[17 + i] = _p(x, -0.22)
    for i, x in enumerate(np.linspace(0.08, 0.38, 5)):
        pts[22 + i] = _p(x, -0.22)
    # Левый глаз 36..41, правый глаз 42..47
    for i in range(6):
        pts[36 + i] = _p(-0.22 + (i - 2.5) * 0.02, -0.08)
        pts[42 + i] = _p(0.22 + (i - 2.5) * 0.02, -0.08)
    return pts


def test_faces_import_and_models_unavailable_without_dat(monkeypatch, tmp_path):
    from photo import faces

    monkeypatch.setattr(faces, "MODELS_DIR", str(tmp_path))
    faces._models.cache_clear()
    try:
        im = Image.new("RGB", (200, 200), (200, 200, 200))
        with pytest.raises(faces.ModelsUnavailableError):
            faces.landmarks(im)
    finally:
        faces._models.cache_clear()


def test_crop_uk_geometry_with_mocked_landmarks(monkeypatch):
    from photo import crop, faces

    raw = _make_jpeg_bytes(1000, 1200)
    im = Image.open(io.BytesIO(raw))
    mock_pts = _mock_landmarks_for_size(1000, 1200, tilt_deg=4.5)
    monkeypatch.setattr(faces, "landmarks", lambda _im: mock_pts.copy())

    res = crop.crop_uk(im)
    assert isinstance(res, crop.CropResult)
    assert abs(res.tilt_deg - 4.5) < 0.2
    assert res.head_mm == pytest.approx(0.733 * 45.0)

    out_im = Image.open(io.BytesIO(res.jpeg))
    assert out_im.size == (827, 1063)
    assert out_im.info.get("dpi") == (600, 600)


def test_phone_look_pure_numpy_scipy():
    from photo.phone_look import LookResult, LookSettings, apply_phone_look, load_rgb

    raw = _make_jpeg_bytes(600, 800)
    im = load_rgb(raw)
    settings = LookSettings(soft=50.0, paper=5.0, noise=2.0)
    res = apply_phone_look(im, settings)

    assert isinstance(res, LookResult)
    assert res.width == 600
    assert res.height == 800
    assert res.resized is False
    assert res.background_share > 0.1
    out_im = Image.open(io.BytesIO(res.jpeg))
    assert out_im.size == (600, 800)
    assert out_im.format == "JPEG"


def test_photo_store_queue_and_settings():
    from photo.store import MAX_QUEUE, PhotoStore, QueuedPhoto

    store = PhotoStore(ttl_seconds=3600)
    assert store.is_photo_mode(1) is False

    st = store.enter_photo_mode(1)
    assert st.in_photo_mode is True
    assert store.is_photo_mode(1) is True

    for i in range(MAX_QUEUE):
        ok = store.add_photo(1, QueuedPhoto(filename=f"f{i}.jpg", data=b"x", compressed=False))
        assert ok is True
    assert store.add_photo(1, QueuedPhoto(filename="overflow.jpg", data=b"x", compressed=False)) is False
    assert len(store.get_queue(1)) == MAX_QUEUE

    s = store.change_setting(1, "soft", "+")
    assert s.soft == 60.0
    s = store.reset_settings(1)
    assert s.soft == 50.0

    store.clear_queue(1)
    assert len(store.get_queue(1)) == 0

    store.exit_photo_mode(1)
    assert store.is_photo_mode(1) is False


def _fake_message(user_id: int = 201, text: str = ""):
    from unittest.mock import AsyncMock

    return SimpleNamespace(
        chat=SimpleNamespace(id=user_id),
        from_user=SimpleNamespace(id=user_id),
        text=text,
        photo=None,
        document=None,
        media_group_id=None,
        message_id=10,
        answer=AsyncMock(),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        bot=SimpleNamespace(
            send_chat_action=AsyncMock(),
            send_photo=AsyncMock(),
            send_document=AsyncMock(),
            send_media_group=AsyncMock(),
            send_message=AsyncMock(),
            delete_message=AsyncMock(),
            download=AsyncMock(),
        ),
    )


def test_main_reply_kb_has_photo_button_instead_of_reset():
    import bot

    kb = bot.main_reply_kb()
    texts = [b.text for b in kb.keyboard[0]]
    assert texts == [bot.BTN_MATCH, bot.BTN_PHOTO, bot.BTN_HELP]
    assert not hasattr(bot, "BTN_RESET")


def test_btn_photo_switches_to_photo_menu_and_back(monkeypatch):
    import asyncio
    from aiogram.types import ReplyKeyboardMarkup
    import bot
    from photo.store import PhotoStore

    pstore = PhotoStore()
    monkeypatch.setattr(bot, "photo_store", pstore)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    msg1 = _fake_message(201, text=bot.BTN_PHOTO)
    asyncio.run(bot.on_btn_photo(msg1))
    assert pstore.is_photo_mode(201) is True
    rm1 = msg1.answer.call_args.kwargs.get("reply_markup")
    assert isinstance(rm1, ReplyKeyboardMarkup)
    flat1 = [b.text for row in rm1.keyboard for b in row]
    assert flat1 == [
        bot.BTN_PHOTO_CROP,
        bot.BTN_PHOTO_LOOK,
        bot.BTN_PHOTO_BOTH,
        bot.BTN_PHOTO_SETTINGS,
        bot.BTN_PHOTO_CLEAR,
        bot.BTN_PHOTO_BACK,
    ]

    msg2 = _fake_message(201, text=bot.BTN_PHOTO_BACK)
    asyncio.run(bot.on_btn_photo_back(msg2))
    assert pstore.is_photo_mode(201) is False
    rm2 = msg2.answer.call_args.kwargs.get("reply_markup")
    assert isinstance(rm2, ReplyKeyboardMarkup)
    assert [b.text for b in rm2.keyboard[0]] == [bot.BTN_MATCH, bot.BTN_PHOTO, bot.BTN_HELP]


def test_match_reset_still_works_via_command_and_inline(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore

    bstore = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", bstore)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    bstore.get_or_create(202)
    asyncio.run(bot.cmd_match_reset(_fake_message(202, text="/match_reset")))
    assert bstore.get_active(202) is None

    s2, _, _ = bstore.get_or_create(202)
    cb_msg = _fake_message(202)
    cb = SimpleNamespace(
        data=f"pm:{s2.batch_id}:reset",
        from_user=SimpleNamespace(id=202),
        message=cb_msg,
        bot=cb_msg.bot,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb))
    assert bstore.get_active(202) is None


def test_photo_reply_buttons_ignored_by_on_text(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import bot

    monkeypatch.setattr(bot, "_allowed", lambda m: True)
    llm_mock = AsyncMock()
    monkeypatch.setattr(bot, "extract_people", llm_mock)

    for btn in (
        bot.BTN_PHOTO,
        bot.BTN_PHOTO_CROP,
        bot.BTN_PHOTO_LOOK,
        bot.BTN_PHOTO_BOTH,
        bot.BTN_PHOTO_SETTINGS,
        bot.BTN_PHOTO_CLEAR,
        bot.BTN_PHOTO_BACK,
    ):
        asyncio.run(bot.on_text(_fake_message(203, text=btn)))
    assert llm_mock.call_count == 0


def test_photo_mode_routes_photos_to_queue_not_match(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore
    from photo.store import PhotoStore

    pstore = PhotoStore()
    bstore = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "photo_store", pstore)
    monkeypatch.setattr(bot, "batch_store", bstore)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    raw = _make_jpeg_bytes(600, 800)

    async def fake_download(obj, destination):
        destination.write(raw)

    # 1) Вне обоих режимов -> подсказка с pm:open и ph:open
    m_idle = _fake_message(204)
    m_idle.photo = [SimpleNamespace(file_id="p0", file_unique_id="u0")]
    asyncio.run(bot.on_photo(m_idle))
    kb_idle = m_idle.answer.call_args.kwargs.get("reply_markup")
    cbs_idle = [b.callback_data for row in kb_idle.inline_keyboard for b in row]
    assert "pm:open" in cbs_idle
    assert "ph:open" in cbs_idle

    # 2) В photo_mode -> идёт в photo_store, не в batch_store (даже если есть пачка /match)
    bstore.get_or_create(204)
    pstore.enter_photo_mode(204)
    monkeypatch.setattr(bot, "_schedule_photo_queue_msg", lambda uid, chat_id, b: None)

    m_ph = _fake_message(204)
    m_ph.photo = [SimpleNamespace(file_id="p1", file_unique_id="u1", file_size=len(raw))]
    m_ph.bot.download = AsyncMock(side_effect=fake_download)
    asyncio.run(bot.on_photo(m_ph))

    m_doc = _fake_message(204)
    m_doc.document = SimpleNamespace(
        file_id="d1",
        file_unique_id="ud1",
        mime_type="image/jpeg",
        file_name="portrait.jpg",
        file_size=len(raw),
    )
    m_doc.bot.download = AsyncMock(side_effect=fake_download)
    asyncio.run(bot.on_document(m_doc))

    q = pstore.get_queue(204)
    assert len(q) == 2
    assert q[0].compressed is True
    assert q[1].compressed is False
    assert len(bstore.get_active(204).photos) == 0


def test_photo_queue_actions_crop_look_both_clear(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import bot
    from photo import faces
    from photo.store import PhotoStore, QueuedPhoto

    pstore = PhotoStore()
    monkeypatch.setattr(bot, "photo_store", pstore)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    raw = _make_jpeg_bytes(1000, 1200)
    mock_pts = _mock_landmarks_for_size(1000, 1200, tilt_deg=0.0)
    monkeypatch.setattr(faces, "landmarks", lambda _im: mock_pts.copy())

    async def _call_ph(action: str):
        cb_msg = _fake_message(205)
        cb = SimpleNamespace(
            data=f"ph:{action}",
            from_user=SimpleNamespace(id=205),
            message=cb_msg,
            bot=cb_msg.bot,
            answer=AsyncMock(),
        )
        await bot.on_ph_callback(cb)
        return cb_msg

    # ph:crop -> отправляет документ 827x1063 600 dpi
    pstore.enter_photo_mode(205)
    pstore.add_photo(205, QueuedPhoto(filename="a.jpg", data=raw, compressed=False))
    m_crop = asyncio.run(_call_ph("crop"))
    m_crop.bot.send_document.assert_awaited_once()
    sent_doc = m_crop.bot.send_document.call_args.kwargs["document"]
    im_crop = Image.open(io.BytesIO(sent_doc.data))
    assert im_crop.size == (827, 1063)
    assert im_crop.info.get("dpi") == (600, 600)

    # ph:look -> отправляет обработанный документ 1000x1200
    pstore.add_photo(205, QueuedPhoto(filename="b.jpg", data=raw, compressed=False))
    m_look = asyncio.run(_call_ph("look"))
    m_look.bot.send_document.assert_awaited_once()
    sent_look = m_look.bot.send_document.call_args.kwargs["document"]
    im_look = Image.open(io.BytesIO(sent_look.data))
    assert im_look.size == (1000, 1200)

    # ph:both -> сначала look, затем crop (итого 827x1063)
    pstore.add_photo(205, QueuedPhoto(filename="c.jpg", data=raw, compressed=False))
    m_both = asyncio.run(_call_ph("both"))
    m_both.bot.send_document.assert_awaited_once()
    sent_both = m_both.bot.send_document.call_args.kwargs["document"]
    im_both = Image.open(io.BytesIO(sent_both.data))
    assert im_both.size == (827, 1063)

    # ph:clear -> очищает очередь
    pstore.add_photo(205, QueuedPhoto(filename="d.jpg", data=raw, compressed=False))
    asyncio.run(_call_ph("clear"))
    assert len(pstore.get_queue(205)) == 0


def test_photo_settings_callbacks(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import bot
    from photo.store import PhotoStore

    pstore = PhotoStore()
    monkeypatch.setattr(bot, "photo_store", pstore)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    async def _call_phs(data: str):
        cb_msg = _fake_message(206)
        cb = SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=206),
            message=cb_msg,
            bot=cb_msg.bot,
            answer=AsyncMock(),
        )
        await bot.on_phs_callback(cb)
        return cb_msg

    asyncio.run(_call_phs("phs:soft:+"))
    assert pstore.get_settings(206).soft == 60.0
    asyncio.run(_call_phs("phs:paper:-"))
    assert pstore.get_settings(206).paper == 4.0
    asyncio.run(_call_phs("phs:noise:+"))
    assert pstore.get_settings(206).noise == 2.5
    asyncio.run(_call_phs("phs:reset"))
    s = pstore.get_settings(206)
    assert (s.soft, s.paper, s.noise) == (50.0, 5.0, 2.0)


def test_match_crop_toggle_and_cache_and_fallback(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import bot
    from batch_store import BatchStore, MatchAssignment, MatchRecord, PhotoItem
    from photo import faces
    from photo.faces import ModelsUnavailableError, NoFace
    from schemas import Person

    bstore = BatchStore(today_fn=lambda: "2026-11-23")
    monkeypatch.setattr(bot, "batch_store", bstore)
    monkeypatch.setattr(bot, "_allowed", lambda m: True)

    session, _, _ = bstore.get_or_create(207)
    assert session.crop_35x45 is True

    # Переключение тумблера pm:<bid>:tcrop
    cb_msg = _fake_message(207)
    cb_toggle = SimpleNamespace(
        data=f"pm:{session.batch_id}:tcrop",
        from_user=SimpleNamespace(id=207),
        message=cb_msg,
        bot=cb_msg.bot,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb_toggle))
    assert session.crop_35x45 is False
    # Переживает сброс и новую пачку
    bstore.reset(207)
    session2, _, _ = bstore.get_or_create(207)
    assert session2.crop_35x45 is False

    # Включаем обратно
    cb_toggle2 = SimpleNamespace(
        data=f"pm:{session2.batch_id}:tcrop",
        from_user=SimpleNamespace(id=207),
        message=cb_msg,
        bot=cb_msg.bot,
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_pm_callback(cb_toggle2))
    assert session2.crop_35x45 is True

    # Проверка send_match_card: успешный кроп -> кэш cropped_file_id -> повторная отправка без crop_uk
    raw = _make_jpeg_bytes(1000, 1200)
    mock_pts = _mock_landmarks_for_size(1000, 1200, tilt_deg=0.0)
    crop_calls = []

    def fake_landmarks(im):
        crop_calls.append(1)
        return mock_pts.copy()

    monkeypatch.setattr(faces, "landmarks", fake_landmarks)

    rec = MatchRecord(
        index=0,
        person=Person(first_name="John", last_name="Smith"),
        gender="M",
        dob="01.01.1990",
        age=36,
        display_name="John Smith",
        full_block="FIRST NAME: John",
        clean_block="FIRST NAME: John",
    )
    photo = PhotoItem(
        index=0,
        file_id="orig_fid",
        file_unique_id="u_orig",
        gender="M",
        age=35,
        raw_bytes=raw,
    )
    assign = MatchAssignment(record_index=0, photo_index=0, mode="auto")

    fake_bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=SimpleNamespace(photo=[SimpleNamespace(file_id="cropped_fid_1")])),
        send_message=AsyncMock(),
    )
    asyncio.run(bot.send_match_card(fake_bot, 207, rec, photo, assign, crop_35x45=True))
    assert len(crop_calls) == 1
    assert photo.cropped_file_id == "cropped_fid_1"
    sent_media = fake_bot.send_photo.call_args.kwargs["photo"]
    im_sent = Image.open(io.BytesIO(sent_media.data))
    assert im_sent.size == (827, 1063)

    # Повторный вызов (дубль) использует cropped_file_id без повторного crop_uk
    asyncio.run(bot.send_match_card(fake_bot, 207, rec, photo, assign, crop_35x45=True))
    assert len(crop_calls) == 1
    assert fake_bot.send_photo.call_args.kwargs["photo"] == "cropped_fid_1"

    # Мягкий фоллбэк при NoFace
    photo_nf = PhotoItem(index=1, file_id="orig_nf", file_unique_id="u_nf", gender="M", age=30, raw_bytes=raw)
    monkeypatch.setattr(faces, "landmarks", lambda _im: (_ for _ in ()).throw(NoFace()))
    asyncio.run(bot.send_match_card(fake_bot, 207, rec, photo_nf, assign, crop_35x45=True))
    assert fake_bot.send_photo.call_args.kwargs["photo"] == "orig_nf"
    assert "35×45" in fake_bot.send_photo.call_args.kwargs["caption"]

    # Мягкий фоллбэк при ModelsUnavailableError
    photo_mu = PhotoItem(index=2, file_id="orig_mu", file_unique_id="u_mu", gender="M", age=30, raw_bytes=raw)
    monkeypatch.setattr(faces, "landmarks", lambda _im: (_ for _ in ()).throw(ModelsUnavailableError("no dat")))
    asyncio.run(bot.send_match_card(fake_bot, 207, rec, photo_mu, assign, crop_35x45=True))
    assert fake_bot.send_photo.call_args.kwargs["photo"] == "orig_mu"
    assert "35×45" in fake_bot.send_photo.call_args.kwargs["caption"]



