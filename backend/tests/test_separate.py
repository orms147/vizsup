"""ENG-2: Demucs background separation (out-of-process, cached bg.wav). Demucs isn't
installed in the base venv, so these mock the subprocess calls — they lock the cache,
the guard, and the stem→bg.wav flow without running torch.
"""
from __future__ import annotations

import os

import pytest

from app.models import Job
from app.pipeline import separate


def _job(tmp_path):
    job = Job(id="t", work_dir=tmp_path / "job")
    job.source_video.write_bytes(b"fake video")
    return job


def test_available_reflects_install(monkeypatch):
    monkeypatch.setattr(separate, "_installed", lambda m: m == "demucs")
    assert separate.available() is True
    monkeypatch.setattr(separate, "_installed", lambda m: False)
    assert separate.available() is False


def test_cache_hit_skips_demucs(tmp_path, monkeypatch):
    job = _job(tmp_path)
    bg = job.work_dir / "bg.wav"
    bg.write_bytes(b"wav")
    src_mtime = job.source_video.stat().st_mtime
    os.utime(bg, (src_mtime + 10, src_mtime + 10))          # bg newer than source
    calls: list = []
    monkeypatch.setattr(separate.subprocess, "run", lambda *a, **k: calls.append(a))
    assert separate.separate_background(job) == bg
    assert calls == []                                       # no demucs / ffmpeg run


def test_unavailable_raises(tmp_path, monkeypatch):
    job = _job(tmp_path)
    monkeypatch.setattr(separate, "available", lambda: False)
    with pytest.raises(RuntimeError, match="Demucs"):
        separate.separate_background(job)


def test_separation_success_mocked(tmp_path, monkeypatch):
    job = _job(tmp_path)
    monkeypatch.setattr(separate, "available", lambda: True)
    monkeypatch.setattr(separate, "ffmpeg_bin", lambda: "ffmpeg")

    def fake_run(cmd, **kw):
        if "demucs" in cmd:                                 # demucs writes the stem
            d = job.work_dir / "stems" / "htdemucs" / "source"
            d.mkdir(parents=True, exist_ok=True)
            (d / "no_vocals.wav").write_bytes(b"instrumental")
        else:                                               # ffmpeg → the tmp path (atomic)
            from pathlib import Path
            Path(cmd[-1]).write_bytes(b"bg")

    monkeypatch.setattr(separate.subprocess, "run", fake_run)
    out = separate.separate_background(job)
    assert out == job.work_dir / "bg.wav" and out.exists()
    assert not (job.work_dir / "bg.wav.tmp").exists()       # atomic rename left no temp


def test_missing_stem_raises(tmp_path, monkeypatch):
    job = _job(tmp_path)
    monkeypatch.setattr(separate, "available", lambda: True)
    monkeypatch.setattr(separate.subprocess, "run", lambda *a, **k: None)   # produces nothing
    with pytest.raises(RuntimeError, match="no_vocals"):
        separate.separate_background(job)
