import base64
import io
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from image_utils import InvalidImageError, prepare_image  # noqa: E402


def test_prepare_image_resizes_large_png_and_high_res_to_jpeg():
    # 1) Большой 2400x1800 RGBA PNG -> JPEG <= 1280px, <= 1.5 MB, base64 <= 4 MB
    img = Image.new("RGBA", (2400, 1800), (120, 180, 240, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_png = buf.getvalue()

    out = prepare_image(raw_png)
    assert len(out) <= 1_500_000
    assert len(base64.b64encode(out)) <= 4_000_000

    with Image.open(io.BytesIO(out)) as res_img:
        assert res_img.format == "JPEG"
        assert max(res_img.size) <= 1280
        assert res_img.size == (1280, 960)

    # 2) Очень тяжёлый шумный JPEG > 1.5 MB (не сжимается без ресайза/подбора качества)
    noise = os.urandom(1800 * 1400 * 3)
    noisy_img = Image.frombytes("RGB", (1800, 1400), noise)
    buf2 = io.BytesIO()
    noisy_img.save(buf2, format="JPEG", quality=98)
    raw_big_jpg = buf2.getvalue()
    assert len(raw_big_jpg) > 1_500_000

    out2 = prepare_image(raw_big_jpg)
    assert len(out2) <= 1_500_000
    assert len(base64.b64encode(out2)) <= 4_000_000
    with Image.open(io.BytesIO(out2)) as res_img2:
        assert res_img2.format == "JPEG"
        assert max(res_img2.size) <= 1280


def test_prepare_image_applies_exif_transpose():
    img = Image.new("RGB", (300, 100), (200, 50, 50))
    exif = img.getexif()
    exif[274] = 6  # Orientation = Rotate 90 CW -> после transpose должно стать 100x300
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    raw = buf.getvalue()

    out = prepare_image(raw)
    with Image.open(io.BytesIO(out)) as res_img:
        assert res_img.format == "JPEG"
        assert res_img.size == (100, 300)


def test_prepare_image_fast_path_skips_small_jpeg():
    img = Image.new("RGB", (640, 480), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    raw = buf.getvalue()

    out = prepare_image(raw)
    assert out == raw


def test_prepare_image_raises_on_invalid_bytes():
    try:
        prepare_image(b"not-an-image-at-all")
        assert False, "Expected InvalidImageError"
    except InvalidImageError:
        pass
    except ValueError:
        pass
