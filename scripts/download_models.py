"""Скачивает shape_predictor_68_face_landmarks.dat из PyPI-пакета face_recognition_models."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

WHEEL_NAME = "face_recognition_models==0.3.0"
TARGET_FILES = (
    "shape_predictor_68_face_landmarks.dat",
)


def _is_ready(dest: Path) -> bool:
    return all(
        (dest / name).is_file() and (dest / name).stat().st_size > 0
        for name in TARGET_FILES
    )


def _extract_from_archive(archive: Path, dest: Path) -> None:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = Path(info.filename).name
                if base in TARGET_FILES:
                    out = dest / base
                    with zf.open(info) as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"extracted {out} ({out.stat().st_size // 1024} KB)")
        return

    if archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                base = Path(member.name).name
                if base in TARGET_FILES:
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    out = dest / base
                    with src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"extracted {out} ({out.stat().st_size // 1024} KB)")
        return

    raise RuntimeError(f"unsupported archive format: {archive.name}")


def download(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if _is_ready(dest):
        print(f"models already present in {dest}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "download", "--no-deps", "-d", tmp, WHEEL_NAME]
        )
        tmp_path = Path(tmp)
        archives = sorted(
            p for p in tmp_path.iterdir()
            if p.is_file() and p.name.endswith((".whl", ".tar.gz", ".tgz"))
        )
        if not archives:
            raise RuntimeError(f"archive for {WHEEL_NAME} not downloaded")

        _extract_from_archive(archives[0], dest)

    missing_or_empty = [
        name for name in TARGET_FILES
        if not ((dest / name).is_file() and (dest / name).stat().st_size > 0)
    ]
    if missing_or_empty:
        raise RuntimeError(
            f"failed to extract non-empty model files {missing_or_empty} from {WHEEL_NAME}"
        )


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "models"
    download(target)
