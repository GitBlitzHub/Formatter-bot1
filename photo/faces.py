"""Face detection and 68-point facial landmarks (lazy-loaded dlib)."""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
from PIL import Image

def _resolve_models_dir() -> str:
    return (
        os.environ.get("MODELS_DIR")
        or os.environ.get("FACE_MODELS_DIR")
        or os.path.join(os.path.dirname(__file__), "..", "models")
    )


MODELS_DIR = _resolve_models_dir()
PREDICTOR_68_FILENAME = "shape_predictor_68_face_landmarks.dat"


class ModelsUnavailableError(RuntimeError):
    """Raised when dlib-bin or the 68-landmark .dat model is missing."""


class NoFace(Exception):
    """Raised when no face is detected in the image."""


@lru_cache(maxsize=1)
def _models():
    try:
        import dlib  # imported lazily so the module works in tests without dlib/models
    except ImportError as exc:
        raise ModelsUnavailableError("dlib-bin is not installed") from exc

    model_path = os.path.join(MODELS_DIR, PREDICTOR_68_FILENAME)
    if not os.path.isfile(model_path):
        raise ModelsUnavailableError(f"Model file not found: {model_path}")

    return (
        dlib,
        dlib.get_frontal_face_detector(),
        dlib.shape_predictor(model_path),
    )


def _detect(img: np.ndarray):
    dlib, det, _ = _models()
    h, w = img.shape[:2]
    s = max(1.0, w / 900)
    small = np.asarray(Image.fromarray(img).resize((int(w / s), int(h / s))))
    rects = det(small, 1)
    if not rects:
        rects = det(small, 2)
    if not rects:
        raise NoFace
    r = max(rects, key=lambda rect: rect.width() * rect.height())
    return (
        dlib.rectangle(
            int(r.left() * s),
            int(r.top() * s),
            int(r.right() * s),
            int(r.bottom() * s),
        ),
        len(rects),
    )


def landmarks(im: Image.Image) -> np.ndarray:
    """Averaged 68 landmarks in image pixels (faster, used for cropping)."""
    dlib, _, sp68 = _models()
    a = np.asarray(im.convert("RGB"))
    rect, _ = _detect(a)
    w = rect.width()
    pts = []
    shifts = [
        (0, 0, 0),
        (-0.02, 0, 0),
        (0.02, 0, 0),
        (0, -0.02, 0),
        (0, 0.02, 0),
        (0, 0, -0.03),
        (0, 0, 0.03),
    ]
    for dx, dy, ds in shifts:
        g = ds * w
        rr = dlib.rectangle(
            int(rect.left() + dx * w - g),
            int(rect.top() + dy * w - g),
            int(rect.right() + dx * w + g),
            int(rect.bottom() + dy * w + g),
        )
        pts.append([[q.x, q.y] for q in sp68(a, rr).parts()])
    return np.mean(np.array(pts, float), axis=0)
