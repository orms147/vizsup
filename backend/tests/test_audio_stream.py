"""Long videos: VAD and the waveform stream the decoded audio in blocks (a 3-hour
track is ~1 GB as float32) — with results identical to the old whole-array code."""
from __future__ import annotations

import io
import math
import subprocess

import numpy as np
import pytest

from app import ffmpegutil
from app.pipeline import vad, waveform

RNG = np.random.default_rng(7)
AUDIO = (RNG.normal(0, 3000, 16000 * 3 + 333)).clip(-32768, 32767).astype(np.int16)  # odd length


def _uneven(x: np.ndarray):
    """Blocks of awkward sizes, like a pipe would hand them over."""
    cuts = [1000, 1007, 50_000, 7]
    i = 0
    for c in cuts:
        yield x[i:i + c]
        i += c
    yield x[i:]


class _FakeSession:
    def run(self, _names, feed):
        frame = feed["input"]
        return np.array([[float(frame.mean())]]), feed["state"] + 1.0


def _old_speech_probs(x: np.ndarray) -> list[float]:
    """The pre-streaming algorithm, kept as the reference."""
    x = x.astype(np.float32) / 32768.0
    sess, state = _FakeSession(), np.zeros((2, 1, 128), dtype=np.float32)
    ctx = np.zeros((1, vad.CONTEXT), dtype=np.float32)
    probs = []
    for i in range(0, len(x) - vad.FRAME + 1, vad.FRAME):
        frame = np.concatenate([ctx, x[i:i + vad.FRAME][None, :]], axis=1)
        out, state = sess.run(None, {"input": frame, "state": state, "sr": None})
        probs.append(float(out[0][0]))
        ctx = frame[:, -vad.CONTEXT:]
    return probs


def test_speech_probs_streamed_equals_whole_array(monkeypatch):
    monkeypatch.setattr(vad, "_session", lambda path: _FakeSession())
    monkeypatch.setattr(ffmpegutil, "pcm_blocks", lambda src, rate, n: _uneven(AUDIO))
    got = vad.speech_probs(ffmpegutil.Path("a.wav"), ffmpegutil.Path("m.onnx"))
    assert got == _old_speech_probs(AUDIO)


def _old_waveform(x: np.ndarray) -> list[float]:
    win = waveform.RATE // waveform.PER_SEC
    out = []
    for i in range(0, len(x), win):
        chunk = [int(s) for s in x[i:i + win]]
        out.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)))
    top = max(out, default=0.0) or 1.0
    return [round(v / top, 3) for v in out]


def test_waveform_streamed_equals_the_old_loop(monkeypatch):
    x = AUDIO[:20_013]
    monkeypatch.setattr(ffmpegutil, "pcm_blocks", lambda src, rate, n: _uneven(x))
    assert waveform.compute(ffmpegutil.Path("a.wav")) == _old_waveform(x)


class _Trickle(io.BytesIO):
    def read(self, n=-1):
        return super().read(min(n, 5) if n and n > 0 else 5)


class _Proc:
    def __init__(self, data: bytes, rc: int = 0):
        self.stdout, self.rc = _Trickle(data), rc

    def wait(self):
        return self.rc

    def poll(self):
        return self.rc

    def kill(self):
        pass


def test_pcm_blocks_fill_blocks_from_short_reads(monkeypatch):
    data = np.arange(11, dtype=np.int16).tobytes() + b"\x01"      # odd trailing byte
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc(data))
    monkeypatch.setattr(ffmpegutil, "ffmpeg_bin", lambda: "ffmpeg")
    blocks = list(ffmpegutil.pcm_blocks(ffmpegutil.Path("a.wav"), 16000, 4))
    assert [b.tolist() for b in blocks] == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10]]


def test_pcm_blocks_raise_when_ffmpeg_fails(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc(b"", rc=1))
    monkeypatch.setattr(ffmpegutil, "ffmpeg_bin", lambda: "ffmpeg")
    with pytest.raises(subprocess.CalledProcessError):
        list(ffmpegutil.pcm_blocks(ffmpegutil.Path("a.wav"), 16000, 4))
