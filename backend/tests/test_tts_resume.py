"""N-10: TTS resume manifest + retry + cancel. FakeTTS counts provider calls —
no network, no audio. The invariant: a re-render only synthesizes lines whose
audio-shaping inputs (provider|voice|rate|text) actually changed.
"""
from __future__ import annotations

import json

import pytest

from app.models import Job
from app.pipeline import tts as tts_mod
from app.pipeline.tts import _seg_hash, synthesize

VI = ("1\n00:00:00,000 --> 00:00:01,000\ncâu một\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\ncâu hai\n\n"
      "3\n00:00:02,000 --> 00:00:03,000\ncâu ba\n")


class FakeTTS:
    def __init__(self, fail_times: int = 0):
        self.calls: list[str] = []
        self._fail = fail_times

    def available(self):
        return True

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("403 transient")
        self.calls.append(text)
        out_path.write_bytes(b"mp3" + text.encode())
        return out_path


@pytest.fixture()
def job(tmp_path):
    j = Job(id="t", work_dir=tmp_path / "job")
    j.vi_srt.write_text(VI, encoding="utf-8")
    return j


def _use(monkeypatch, fake):
    monkeypatch.setattr(tts_mod, "get_tts", lambda name: fake)
    return fake


def test_second_run_synthesizes_nothing(job, monkeypatch):
    fake = _use(monkeypatch, FakeTTS())
    synthesize(job, provider="fake")
    assert len(fake.calls) == 3
    fake.calls.clear()
    segs = synthesize(job, provider="fake")
    assert fake.calls == []                      # all cached
    assert len(segs) == 3                        # segments still returned


def test_one_changed_line_resynthesizes_one(job, monkeypatch):
    fake = _use(monkeypatch, FakeTTS())
    synthesize(job, provider="fake")
    fake.calls.clear()
    job.vi_srt.write_text(VI.replace("câu hai", "câu hai ĐÃ SỬA"), encoding="utf-8")
    synthesize(job, provider="fake")
    assert fake.calls == ["câu hai ĐÃ SỬA"]


def test_voice_change_resynthesizes_all(job, monkeypatch):
    fake = _use(monkeypatch, FakeTTS())
    synthesize(job, provider="fake", voice="vi-VN-HoaiMyNeural")
    fake.calls.clear()
    synthesize(job, provider="fake", voice="vi-VN-NamMinhNeural")
    assert len(fake.calls) == 3


def test_retry_recovers_after_two_failures(job, monkeypatch):
    fake = _use(monkeypatch, FakeTTS(fail_times=2))
    sleeps: list[float] = []
    segs = synthesize(job, provider="fake", _sleep=sleeps.append)
    assert len(segs) == 3
    assert sleeps == [1.0, 4.0]                  # backoff observed, then success


def test_three_failures_raise(job, monkeypatch):
    _use(monkeypatch, FakeTTS(fail_times=3))
    sleeps: list[float] = []
    with pytest.raises(RuntimeError, match="403"):
        synthesize(job, provider="fake", _sleep=sleeps.append)
    assert sleeps == [1.0, 4.0]                  # all attempts spent on cue 1


def test_muted_cue_skipped_and_manifest_entry_dropped(job, monkeypatch):
    fake = _use(monkeypatch, FakeTTS())
    synthesize(job, provider="fake")             # seeds manifest with 1,2,3
    (job.work_dir / "overrides.json").write_text(
        json.dumps([{}, {"mute": True}, {}]), encoding="utf-8")
    fake.calls.clear()
    segs = synthesize(job, provider="fake")
    assert [c.index for c, _ in segs] == [1, 3]
    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "2" not in manifest                   # no stale cache for the muted line
    # unmute → line 2 must be re-synthesized (its cache entry is gone)
    (job.work_dir / "overrides.json").unlink()
    fake.calls.clear()
    synthesize(job, provider="fake")
    assert fake.calls == ["câu hai"]


def test_cancel_between_cues_returns_partial(job, monkeypatch):
    fake = _use(monkeypatch, FakeTTS())
    hits = iter([False, False, True])            # cancel arrives before cue 3
    segs = synthesize(job, provider="fake", cancel=lambda: next(hits))
    assert len(segs) == 2                        # partial, no exception
    assert len(fake.calls) == 2


class NoAudioReceived(RuntimeError):
    """Mirror of edge_tts.exceptions.NoAudioReceived (matched by class name)."""


class UnvoiceableTTS(FakeTTS):
    """Voice can't speak Chinese-only lines (the passthrough smoke path)."""

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        if "二" in text:
            raise NoAudioReceived("No audio was received.")
        return super().synthesize(text, out_path, voice=voice, rate=rate)


def test_unvoiceable_line_skipped_not_fatal(job, monkeypatch):
    job.vi_srt.write_text(VI.replace("câu hai", "第二句"), encoding="utf-8")   # passthrough
    fake = _use(monkeypatch, UnvoiceableTTS())
    logs: list[str] = []
    sleeps: list[float] = []
    segs = synthesize(job, provider="fake", log=logs.append, _sleep=sleeps.append)
    assert [c.index for c, _ in segs] == [1, 3]     # line 2 has no dub, render continues
    assert sleeps == []                             # deterministic error → NO retries
    assert any("không phát âm được" in m for m in logs)
    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "2" not in manifest                      # no cache entry for the skipped line


def test_hash_covers_every_audio_knob():
    base = _seg_hash("edge", "voiceA", "+10%", "xin chào")
    assert _seg_hash("azure", "voiceA", "+10%", "xin chào") != base
    assert _seg_hash("edge", "voiceB", "+10%", "xin chào") != base
    assert _seg_hash("edge", "voiceA", "+20%", "xin chào") != base
    assert _seg_hash("edge", "voiceA", "+10%", "xin chào!") != base
    assert _seg_hash("edge", "voiceA", "+10%", "xin chào") == base


class ThrottledTTS(FakeTTS):
    """edge-tts under a burst (2026-10-27): "no audio" for NORMAL text, then fine."""

    def __init__(self, empty_times: int):
        super().__init__()
        self.empty = empty_times

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        if "câu hai" in text and self.empty:
            self.empty -= 1
            raise NoAudioReceived("No audio was received.")
        return super().synthesize(text, out_path, voice=voice, rate=rate)


def test_no_audio_on_normal_text_is_retried(job, monkeypatch):
    """417 of 717 lines were dropped as "unvoiceable" while Microsoft was throttling."""
    _use(monkeypatch, ThrottledTTS(empty_times=1))
    sleeps: list[float] = []
    segs = synthesize(job, provider="fake", _sleep=sleeps.append)
    assert [c.index for c, _ in segs] == [1, 2, 3] and sleeps


def test_sustained_no_audio_on_normal_text_stops_instead_of_dropping(job, monkeypatch):
    _use(monkeypatch, ThrottledTTS(empty_times=99))
    with pytest.raises(RuntimeError, match="giới hạn"):
        synthesize(job, provider="fake", _sleep=lambda _s: None)
    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "1" in manifest                      # finished lines are kept for the retry


def test_has_speakable():
    from app.providers.base import has_speakable

    assert has_speakable("Xin chào") and has_speakable("Đ") and has_speakable("2025")
    assert not has_speakable("第二句") and not has_speakable("…!?") and not has_speakable("")
