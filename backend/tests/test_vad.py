"""Voice activity for tự canh lời: hysteresis segments, the per-job cache, and that a
missing / broken model degrades to None instead of failing a run."""
from __future__ import annotations

import wave

import pytest

from app.models import Job
from app.pipeline import vad

F = vad.FRAME_SEC   # 32 ms


def frames(*runs: tuple[float, float]) -> list[float]:
    """[(prob, seconds), …] → per-frame probabilities."""
    out: list[float] = []
    for p, sec in runs:
        out += [p] * round(sec / F)
    return out


def test_speech_between_silences_becomes_one_padded_span():
    spans = vad.segments(frames((0.0, 1.0), (0.9, 2.0), (0.0, 1.0)))
    assert len(spans) == 1
    a, b = spans[0]
    assert a == pytest.approx(1.0 - vad.PAD, abs=F)
    assert b == pytest.approx(3.0 + vad.PAD, abs=F)


def test_short_blips_are_dropped():
    assert vad.segments(frames((0.0, 1.0), (0.9, 0.1), (0.0, 1.0))) == []


def test_hysteresis_keeps_speech_through_mid_probabilities():
    """Between NEG_THRESHOLD and THRESHOLD the voice is still on."""
    spans = vad.segments(frames((0.0, 0.5), (0.9, 1.0), (0.4, 1.0), (0.9, 1.0), (0.0, 0.5)))
    assert len(spans) == 1


def test_pauses_shorter_than_merge_gap_stay_inside_one_span():
    """A breath mid-sentence must not become an edge a cue can snap into."""
    one = vad.segments(frames((0.0, 0.5), (0.9, 1.0), (0.0, 0.15), (0.9, 1.0), (0.0, 0.5)))
    two = vad.segments(frames((0.0, 0.5), (0.9, 1.0), (0.0, 0.6), (0.9, 1.0), (0.0, 0.5)))
    assert len(one) == 1 and len(two) == 2


@pytest.fixture()
def job(tmp_path):
    j = Job(id="v", work_dir=tmp_path / "v")
    j.source_audio.write_bytes(b"fake")
    return j


def test_spans_are_cached_per_source(job, monkeypatch):
    calls = []
    monkeypatch.setattr(vad, "ensure_model", lambda log=None: job.work_dir / "m.onnx")
    monkeypatch.setattr(vad, "speech_probs",
                        lambda src, model: calls.append(src) or frames((0.0, 1.0), (0.9, 1.0)))
    first = vad.speech_spans(job)
    assert first and vad.speech_spans(job) == first
    assert len(calls) == 1                      # second call read vad.json


def test_no_model_means_none_and_a_log_line(job, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "empty")
    logs = []                                   # conftest: downloads fail in tests
    assert not vad.model_ready()
    assert vad.speech_spans(job, log=logs.append) is None
    assert logs and logs[-1].startswith("⚠")


def test_no_source_means_none(tmp_path):
    assert vad.speech_spans(Job(id="e", work_dir=tmp_path / "e")) is None


def test_real_model_finds_no_voice_in_silence(tmp_path):
    """Integration (skipped unless the model was downloaded once already)."""
    if not vad.model_ready():
        pytest.skip("Silero VAD not downloaded")
    pytest.importorskip("onnxruntime")
    wav = tmp_path / "silence.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(vad.RATE)
        w.writeframes(b"\0\0" * vad.RATE * 2)
    probs = vad.speech_probs(wav, vad.model_path())
    assert len(probs) == pytest.approx(2 / F, abs=2)
    assert max(probs) < vad.THRESHOLD
