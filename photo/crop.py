"""Crop like PhotoAID with the 'UK driving licence' preset.

Rules measured on PhotoAID's own results from the studio:
  output ............ 827 x 1063 px = 35 x 45 mm at 600 dpi
  head (crown->chin)  73.3 % of the height  (about 33 mm; UK rule 29-34 mm)
  crown ............. 11.2 % from the top    (about 5 mm)
  horizontal ........ centre of the face contour in the middle
  tilt .............. eyes levelled
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import faces

OUT_W, OUT_H, DPI = 827, 1063, 600
HEAD_SHARE = 0.733
CROWN_TOP = 0.112
MM_PER_PX = 45.0 / OUT_H
FALLBACK_CROWN = 0.62


@dataclass
class CropResult:
    jpeg: bytes
    head_mm: float
    tilt_deg: float
    crown_found: bool
    padded: bool


def _crown_y(a: np.ndarray, p: np.ndarray, ymin: int = 0) -> tuple[float, bool]:
    """Top of the head (including hair): first row above the brows that differs from the background
    next to the head in the same row."""
    h, w = a.shape[:2]
    x0, x1 = int(p[17, 0]), int(p[26, 0])
    fw = float(p[16, 0] - p[0, 0])
    top = int(p[17:27, 1].min())
    if x1 - x0 < 10 or top < 5:
        return 0.0, False
    lo = (max(0, int(p[0, 0] - 0.45 * fw)), max(0, int(p[0, 0] - 0.2 * fw)))
    hi = (min(w, int(p[16, 0] + 0.2 * fw)), min(w, int(p[16, 0] + 0.45 * fw)))
    band = a[:top, x0:x1].astype(np.int16)
    refs = [a[:top, lo[0]:lo[1]], a[:top, hi[0]:hi[1]]]
    refs = [r for r in refs if r.shape[1] > 2]
    if refs:
        ref = np.median(np.concatenate(refs, axis=1), axis=1).astype(np.int16)[:, None, :]
    else:
        ref = np.median(band.reshape(-1, 3)[: band.shape[1] * 8], axis=0).astype(np.int16)[None, None, :]
    diff = np.abs(band - ref).max(axis=2)
    not_bg = (diff > 22).mean(axis=1) > 0.3
    not_bg[:ymin] = False
    for r in np.nonzero(not_bg)[0]:
        if r + 5 <= top and not_bg[r:r + 5].all():
            return float(r), r > ymin + 2
    return 0.0, False


def crop_uk(im: Image.Image) -> CropResult:
    im = im.convert("RGB")
    p = faces.landmarks(im)
    le, re = p[36:42].mean(0), p[42:48].mean(0)
    tilt = float(np.degrees(np.arctan2(re[1] - le[1], re[0] - le[0])))

    W, H = im.size
    m = int(0.35 * max(W, H))
    padded_img = Image.fromarray(np.pad(np.asarray(im), ((m, m), (m, m), (0, 0)), mode="edge"))
    del im
    p = p + m
    le, re = le + m, re + m

    c = (le + re) / 2
    rot = padded_img.rotate(tilt, center=tuple(c), resample=Image.BICUBIC)
    del padded_img
    t = np.radians(-tilt)
    R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    p = (p - c) @ R.T + c
    a = np.asarray(rot)

    chin = float(p[8, 1])
    ymin = int(m + abs(np.tan(np.radians(tilt))) * W) + 2
    crown, found = _crown_y(a, p, ymin)
    if not found:
        brow = float(p[17:27, 1].mean())
        crown = brow - FALLBACK_CROWN * (chin - brow)
    scale = HEAD_SHARE * OUT_H / (chin - crown)
    cx = float((p[0, 0] + p[16, 0]) / 2)
    left = cx - OUT_W / 2 / scale
    top = crown - CROWN_TOP * OUT_H / scale
    box = (
        int(round(left)),
        int(round(top)),
        int(round(left + OUT_W / scale)),
        int(round(top + OUT_H / scale)),
    )
    corners = np.array([[box[0], box[1]], [box[2], box[1]], [box[0], box[3]], [box[2], box[3]]], float)
    orig = (corners - c) @ np.linalg.inv(R).T + c - m
    padded = bool(
        (orig[:, 0] < -1).any()
        or (orig[:, 1] < -1).any()
        or (orig[:, 0] > W + 1).any()
        or (orig[:, 1] > H + 1).any()
    )
    del a
    out = rot.crop(box).resize((OUT_W, OUT_H), Image.LANCZOS)
    del rot

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=95, subsampling=0, dpi=(DPI, DPI))
    return CropResult(buf.getvalue(), HEAD_SHARE * 45.0, tilt, found, padded)
