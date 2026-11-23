"""Post-processing that makes a generated portrait look like a real iPhone studio shot."""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as nd

MAX_SIDE = 3200


@dataclass
class LookSettings:
    soft: float = 50.0   # % of fine skin detail removed
    paper: float = 5.0   # % paper brightness variation
    noise: float = 2.0   # sensor noise, 8-bit levels

    def describe(self) -> str:
        return f"кожа {self.soft:g}%, ватман {self.paper:g}%, шум {self.noise:g}"


@dataclass
class LookResult:
    jpeg: bytes
    width: int
    height: int
    background_share: float
    resized: bool


def _gauss(a: np.ndarray, sigma: float) -> np.ndarray:
    return nd.gaussian_filter(a, sigma, mode="nearest") if sigma >= 0.35 else a.copy()


def load_rgb(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


def apply_phone_look(im: Image.Image, s: LookSettings, seed: int = 7) -> LookResult:
    W, H = im.size
    resized = False
    if max(W, H) > MAX_SIDE:
        k = MAX_SIDE / max(W, H)
        im = im.resize((round(W * k), round(H * k)), Image.LANCZOS)
        W, H = im.size
        resized = True

    f32 = np.float32
    a = np.asarray(im)
    R, G, B = (a[..., i].astype(f32) for i in range(3))
    del a
    L = (R + G + B) / 3
    sc = max(0.5, W / 1792)

    # background: light, slightly bluish paper connected to the frame border
    cand = (B - R > 6) & (L > 170)
    lab, _ = nd.label(cand)
    del cand
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    bg = np.isin(lab, border[border > 0])
    del lab
    bg_share = float(bg.mean())
    bgm = _gauss(bg.astype(f32), 3 * sc)

    # skin: warm pixels (excludes paper, white brows/hair, dark T-shirt)
    skin = (R > B + 12) & (R >= G - 2) & (R - B < 150) & (L > 45) & (L < 248)
    skin = _gauss(skin.astype(f32), 4 * sc)

    # 1) soften fine micro-detail on skin, protect strong local detail
    soft = s.soft / 100.0
    if soft > 0:
        sig = 2.8 * sc
        f = L - _gauss(L, sig)
        energy = _gauss(np.abs(f), 3 * sc)
        energy /= 12.0
        np.square(energy, out=energy)
        f /= 24.0
        np.square(f, out=f)
        f += energy
        del energy
        np.negative(f, out=f)
        np.exp(f, out=f)
        f *= skin
        f *= (1 - bgm)
        f *= soft
        for C in (R, G, B):
            d = C - _gauss(C, sig)
            d *= f
            C -= d
            del d
        del f
    del skin, L

    # 2) paper unevenness; same shift on all channels so the colour stays put
    v = s.paper / 100.0
    if v > 0 and bg_share > 0.05:
        rows = (~bg).sum(axis=1)
        top_rows = np.nonzero(rows > W * 0.03)[0]
        top = int(top_rows[0]) if len(top_rows) else int(H * 0.1)
        band = ~bg[top:min(H, top + int(H * 0.25))]
        xs = np.nonzero(band)[1]
        hx = float(xs.mean()) if len(xs) else W / 2
        hy = top + H * 0.17
        del band, xs
        rng = np.random.default_rng(seed)
        gw, gh = W // 8 + 2, H // 8 + 2
        grid = nd.gaussian_filter(rng.standard_normal((gh, gw)), 12, mode="nearest").astype(f32)
        grid /= np.abs(grid).max() or 1.0
        blob = np.array(
            Image.fromarray(grid, mode="F").resize((gw * 8, gh * 8), Image.BILINEAR),
            dtype=f32,
        )[:H, :W].copy()
        dx = ((np.arange(W, dtype=f32) - hx) / (W * 0.75)) ** 2
        dy = ((np.arange(H, dtype=f32) - hy) / (H * 0.75)) ** 2
        r = np.sqrt(dy[:, None] + dx[None, :])
        np.clip(r, 0, 1.4, out=r)
        r **= 1.6
        field = (1 + v * 0.5) - v * r
        del r
        blob *= v * 0.5
        blob += 1
        field *= blob
        del blob
        field -= 1
        field *= bgm
        field *= (R + G + B) / 3
        R += field
        G += field
        B += field
        del field
    del bgm, bg

    # 3) sensor noise: luma + a little chroma, stronger in darker areas
    nz = s.noise
    if nz > 0:
        rng = np.random.default_rng(seed + 4)
        sgm = (R + G + B) / 765.0
        np.clip(sgm, 0, 1, out=sgm)
        sgm = nz * (0.7 + 0.9 * (1 - sgm))
        base = rng.standard_normal((H, W), dtype=f32)
        base *= sgm
        for C in (R, G, B):
            ch = _gauss(rng.standard_normal((H, W), dtype=f32), 1.0 * sc)
            ch *= 0.25 / (ch.std() or 1.0)
            ch *= sgm
            C += base
            C += ch
            del ch
        del base, sgm

    out = np.empty((H, W, 3), np.uint8)
    for i, C in enumerate((R, G, B)):
        np.clip(C, 0, 255, out=C)
        np.rint(C, out=C)
        out[..., i] = C
    del R, G, B
    buf = io.BytesIO()
    Image.fromarray(out).save(buf, "JPEG", quality=95, subsampling=0)
    return LookResult(buf.getvalue(), W, H, bg_share, resized)
