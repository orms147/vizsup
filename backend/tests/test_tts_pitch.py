"""Cao độ giọng: each dub segment moved by N semitones after the TTS (tts.pitched), cached
beside the segment so another pitch never re-buys the voice."""
from __future__ import annotations

import os
import subprocess

from app.ffmpegutil import ffmpeg_bin
from app.models import Cue, Job
from app.pipeline import assemble, tts


def _seg(path):
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=0.6", "-c:a", "libmp3lame", str(path)], check=True)
    return path


def test_pitched_writes_a_cached_copy_of_the_same_length(tmp_path):
    seg = _seg(tmp_path / "seg_0001.mp3")
    out = tts.pitched(seg, 2)
    assert out.name == "seg_0001.p+2.mp3" and out.stat().st_size > 0
    assert abs(assemble.probe_duration(out) - assemble.probe_duration(seg)) < 0.1   # tempo kept
    made = out.stat().st_mtime_ns
    assert tts.pitched(seg, 2) == out and out.stat().st_mtime_ns == made             # cached
    os.utime(seg, ns=(made + 10**9, made + 10**9))                                    # re-voiced
    tts.pitched(seg, 2)
    assert out.stat().st_mtime_ns > made
    assert tts.pitched(seg, -1.5).name == "seg_0001.p-1.5.mp3"


def test_zero_pitch_keeps_the_segments(tmp_path):
    segs = [(Cue(index=1, start=0, end=1, text="a"), tmp_path / "seg_0001.mp3")]
    assert tts.pitch_segments(segs, 0) is segs


def test_synthesize_moves_the_trimmed_segments(tmp_path, monkeypatch):
    job = Job(id="j", work_dir=tmp_path, url="", metadata={})
    seg = _seg(tmp_path / "seg_0001.mp3")
    cue = Cue(index=1, start=0, end=1, text="a")
    monkeypatch.setattr(tts, "_synthesize", lambda *a, **k: [(cue, seg)])
    monkeypatch.setattr(tts, "trim_files", lambda *a, **k: 0)
    logs: list[str] = []
    out = tts.synthesize(job, pitch=2, log=logs.append)
    assert out == [(cue, tmp_path / "seg_0001.p+2.mp3")]
    assert any("Cao độ giọng +2" in m for m in logs)
    assert tts.synthesize(job) == [(cue, seg)]                       # 0: as made
