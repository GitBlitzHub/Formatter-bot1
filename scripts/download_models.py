"""Скачивает shape_predictor_68_face_landmarks.dat из PyPI-пакета face_recognition_models."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

WHEEL_NAME = "face_recognition_models==0.3.0"
TARGET_FILES = (
    "shape_predictor_68_face_landmarks.dat",
)


def download(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if all((dest / name).exists() for name in TARGET_FILES):
        print(f"models already present in {dest}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "download", "--no-deps", "-d", tmp, WHEEL_NAME]
        )
        wheels = list(Path(tmp).glob("*.whl"))
        if not wheels:
            raise RuntimeError(f"wheel {WHEEL_NAME} not downloaded")
        with zipfile.ZipFile(wheels[0]) as zf:
            for member in zf.namelist():
                base = Path(member).name
                if base in TARGET_FILES:
                    out = dest / base
                    with zf.open(member) as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"extracted {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "models"
    download(target)
