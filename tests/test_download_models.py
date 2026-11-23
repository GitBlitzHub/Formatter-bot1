"""Offline tests for scripts/download_models.py, Dockerfile, and photo/faces.py."""
from __future__ import annotations

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from photo import faces
from scripts import download_models


def _make_tar_gz(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _make_whl(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _mock_pip_download(monkeypatch, creator):
    def fake_check_call(cmd, *args, **kwargs):
        d_idx = cmd.index("-d")
        tmp_dir = Path(cmd[d_idx + 1])
        creator(tmp_dir)

    monkeypatch.setattr(subprocess, "check_call", fake_check_call)


def test_download_extracts_from_tar_gz(monkeypatch, tmp_path):
    payload = b"fake-dat-payload-tar-gz"
    _mock_pip_download(
        monkeypatch,
        lambda d: _make_tar_gz(
            d / "face_recognition_models-0.3.0.tar.gz",
            {
                "face_recognition_models-0.3.0/face_recognition_models/models/shape_predictor_68_face_landmarks.dat": payload,
                "face_recognition_models-0.3.0/README.md": b"readme",
            },
        ),
    )

    dest = tmp_path / "models"
    download_models.download(dest)

    out = dest / "shape_predictor_68_face_landmarks.dat"
    assert out.is_file()
    assert out.read_bytes() == payload
    assert out.stat().st_size > 0
    assert not (dest / "README.md").exists()


def test_download_extracts_from_tgz(monkeypatch, tmp_path):
    payload = b"fake-dat-payload-tgz"
    _mock_pip_download(
        monkeypatch,
        lambda d: _make_tar_gz(
            d / "face_recognition_models-0.3.0.tgz",
            {
                "face_recognition_models-0.3.0/models/shape_predictor_68_face_landmarks.dat": payload,
            },
        ),
    )

    dest = tmp_path / "models"
    download_models.download(dest)

    out = dest / "shape_predictor_68_face_landmarks.dat"
    assert out.is_file()
    assert out.read_bytes() == payload


def test_download_extracts_from_whl(monkeypatch, tmp_path):
    payload = b"fake-dat-payload-whl"
    _mock_pip_download(
        monkeypatch,
        lambda d: _make_whl(
            d / "face_recognition_models-0.3.0-py2.py3-none-any.whl",
            {
                "face_recognition_models/models/shape_predictor_68_face_landmarks.dat": payload,
                "face_recognition_models/__init__.py": b"",
            },
        ),
    )

    dest = tmp_path / "models"
    download_models.download(dest)

    out = dest / "shape_predictor_68_face_landmarks.dat"
    assert out.is_file()
    assert out.read_bytes() == payload
    assert out.stat().st_size > 0


def test_download_raises_when_no_archive_downloaded(monkeypatch, tmp_path):
    _mock_pip_download(monkeypatch, lambda d: None)

    with pytest.raises(RuntimeError):
        download_models.download(tmp_path / "models")


def test_download_raises_when_target_missing_in_archive(monkeypatch, tmp_path):
    _mock_pip_download(
        monkeypatch,
        lambda d: _make_tar_gz(
            d / "face_recognition_models-0.3.0.tar.gz",
            {"face_recognition_models-0.3.0/README.md": b"no model here"},
        ),
    )

    with pytest.raises(RuntimeError):
        download_models.download(tmp_path / "models")


def test_download_raises_when_extracted_file_empty(monkeypatch, tmp_path):
    _mock_pip_download(
        monkeypatch,
        lambda d: _make_tar_gz(
            d / "face_recognition_models-0.3.0.tar.gz",
            {
                "face_recognition_models-0.3.0/shape_predictor_68_face_landmarks.dat": b"",
            },
        ),
    )

    with pytest.raises(RuntimeError):
        download_models.download(tmp_path / "models")


def test_download_skips_when_nonempty_model_already_present(monkeypatch, tmp_path):
    dest = tmp_path / "models"
    dest.mkdir()
    existing = dest / "shape_predictor_68_face_landmarks.dat"
    existing.write_bytes(b"already-here")

    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("pip download should not be called")

    monkeypatch.setattr(subprocess, "check_call", fail_if_called)

    download_models.download(dest)
    assert not called
    assert existing.read_bytes() == b"already-here"


def test_download_redownloads_when_existing_model_is_empty(monkeypatch, tmp_path):
    dest = tmp_path / "models"
    dest.mkdir()
    existing = dest / "shape_predictor_68_face_landmarks.dat"
    existing.write_bytes(b"")

    payload = b"fresh-payload"
    _mock_pip_download(
        monkeypatch,
        lambda d: _make_tar_gz(
            d / "face_recognition_models-0.3.0.tar.gz",
            {
                "face_recognition_models-0.3.0/shape_predictor_68_face_landmarks.dat": payload,
            },
        ),
    )

    download_models.download(dest)
    assert existing.read_bytes() == payload


def test_download_prevents_path_traversal(monkeypatch, tmp_path):
    payload = b"safe-payload"
    _mock_pip_download(
        monkeypatch,
        lambda d: _make_tar_gz(
            d / "face_recognition_models-0.3.0.tar.gz",
            {"../../shape_predictor_68_face_landmarks.dat": payload},
        ),
    )

    dest = tmp_path / "nested" / "models"
    download_models.download(dest)

    assert (dest / "shape_predictor_68_face_landmarks.dat").read_bytes() == payload
    assert not (tmp_path / "shape_predictor_68_face_landmarks.dat").exists()


def test_faces_resolve_models_dir_env_priority(monkeypatch):
    assert isinstance(faces.MODELS_DIR, str)

    monkeypatch.setenv("MODELS_DIR", "/custom/models")
    monkeypatch.setenv("FACE_MODELS_DIR", "/legacy/models")
    assert faces._resolve_models_dir() == "/custom/models"

    monkeypatch.delenv("MODELS_DIR", raising=False)
    assert faces._resolve_models_dir() == "/legacy/models"

    monkeypatch.setenv("MODELS_DIR", "")
    assert faces._resolve_models_dir() == "/legacy/models"

    monkeypatch.delenv("MODELS_DIR", raising=False)
    monkeypatch.delenv("FACE_MODELS_DIR", raising=False)
    default_dir = faces._resolve_models_dir()
    assert default_dir.endswith("models")


def test_dockerfile_sets_models_dir():
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert "MODELS_DIR=/app/models" in text
    assert "FACE_MODELS_DIR=" not in text
