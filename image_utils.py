import io
from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidImageError(ValueError):
    """Некорректное или повреждённое изображение."""


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def prepare_image(
    raw_bytes: bytes,
    max_dim: int = 1280,
    target_bytes: int = 1_500_000,
    hard_max_bytes: int = 2_800_000,
) -> bytes:
    """
    Приводит изображение к безопасному для Vision API и Telegram send_photo виду:
    - применяет EXIF-поворот;
    - для подходящих небольших JPEG пропускает пережатие;
    - конвертирует в RGB, уменьшает до max_dim по длинной стороне;
    - сохраняет в JPEG с адаптивным качеством до <= target_bytes (и строго <= hard_max_bytes).
    """
    if not raw_bytes:
        raise InvalidImageError("Empty image bytes")

    try:
        with Image.open(io.BytesIO(raw_bytes)) as opened:
            opened.load()
            fmt = (opened.format or "").upper()
            orientation = opened.getexif().get(274, 1)

            if (
                fmt == "JPEG"
                and len(raw_bytes) <= target_bytes
                and max(opened.size) <= max_dim
                and orientation in (1, None)
            ):
                return raw_bytes

            transposed = ImageOps.exif_transpose(opened)
            rgb = _to_rgb(transposed)
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise InvalidImageError(f"Invalid image: {e}") from e

    rgb.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    best_out: bytes = b""
    for q in (85, 75, 65, 50):
        out = _encode_jpeg(rgb, q)
        best_out = out
        if len(out) <= target_bytes:
            return out

    for dim in (960, 800, 640):
        if max(rgb.size) > dim:
            scaled = rgb.copy()
            scaled.thumbnail((dim, dim), Image.Resampling.LANCZOS)
        else:
            scaled = rgb
        for q in (65, 50, 40):
            out = _encode_jpeg(scaled, q)
            if not best_out or len(out) < len(best_out):
                best_out = out
            if len(out) <= target_bytes:
                return out

    if len(best_out) <= hard_max_bytes:
        return best_out

    return best_out
