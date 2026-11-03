"""2026-11-02: a local voice that can speak several lines at once (VieNeu on a many-core CPU: one
ONNX session left a 32-thread CPU at ~25 %, 1592 lines took ~25 min). pipeline/tts.py runs
``tts.parallel`` ``synthesize`` calls together; the cache, the manifest, the order of the result
and the error / cancel rules stay those of the one-at-a-time loop. A fake voice, no network."""
from __future__ import annotations

import threading
import time

import pytest

from app.models import Job
from app.pipeline import tts as tts_mod
from app.pipeline.srt import write_srt
from app.models import Cue


class NoAudioReceived(Exception):          # what edge-tts raises (matched by class name)
    pass


WORDS = ["an", "bình", "cường", "dũng", "em", "phúc", "giang", "hà", "khoa", "lan", "mai", "nam"]


def lines(over=None):
    return [Cue(index=i, start=float(i), end=i + 0.9, text=(over or {}).get(i, f"câu {w}"))
            for i, w in enumerate(WORDS, 1)]


class FakeVoice:
    """Records how many lines were spoken at the same moment."""

    name = "fake"
    parallel = 4

    def __init__(self, fail_on: str = "", unvoiceable_on: str = ""):
        self.lock = threading.Lock()
        self.now = self.peak = self.calls = 0
        self.fail_on, self.unvoiceable_on = fail_on, unvoiceable_on

    def available(self):
        return True

    def voices(self):
        return ["v"]

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        with self.lock:
            self.now += 1
            self.calls += 1
            self.peak = max(self.peak, self.now)
        time.sleep(0.05)
        with self.lock:
            self.now -= 1
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("boom " + text)
        if self.unvoiceable_on and self.unvoiceable_on in text:
            raise NoAudioReceived("No audio was received")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp3:" + text.encode())
        return out_path


@pytest.fixture()
def job(tmp_path):
    j = Job(id="p", work_dir=tmp_path / "p")
    write_srt(lines(), j.vi_srt)
    return j


def run(job, voice, monkeypatch, **kw):
    monkeypatch.setattr(tts_mod, "get_tts", lambda name: voice)
    kw.setdefault("_sleep", lambda _s: None)
    return tts_mod.synthesize(job, provider="fake", voice="v", trim=False, **kw)


def test_lines_are_spoken_together_and_come_back_in_order(job, monkeypatch):
    voice = FakeVoice()
    done = []
    segs = run(job, voice, monkeypatch, progress=lambda a, b: done.append((a, b)))
    assert [c.index for c, _p in segs] == list(range(1, 13))
    assert all(p.read_bytes().startswith(b"mp3:") for _c, p in segs)
    assert 2 <= voice.peak <= 4                                  # really parallel, never above its width
    assert done[-1] == (12, 12) and [a for a, _b in done] == sorted(a for a, _b in done)


def test_a_second_run_speaks_nothing_new(job, monkeypatch):
    voice = FakeVoice()
    run(job, voice, monkeypatch)
    first = voice.calls
    run(job, voice, monkeypatch)
    assert voice.calls == first                                   # every line came from the cache
    assert len(tts_mod._load_manifest(job)) == 12


def test_a_changed_line_is_the_only_one_made_again(job, monkeypatch):
    voice = FakeVoice()
    run(job, voice, monkeypatch)
    write_srt(lines({5: "khác hẳn"}), job.vi_srt)
    before = voice.calls
    run(job, voice, monkeypatch)
    assert voice.calls == before + 1


def test_the_one_at_a_time_loop_is_unchanged_for_other_voices(job, monkeypatch):
    voice = FakeVoice()
    voice.parallel = 1
    run(job, voice, monkeypatch)
    assert voice.peak == 1


def test_a_failing_line_raises_but_keeps_what_was_finished(job, monkeypatch):
    voice = FakeVoice(fail_on="giang")
    with pytest.raises(Exception) as exc:
        run(job, voice, monkeypatch)
    assert "boom" in str(exc.value)
    kept = tts_mod._load_manifest(job)
    assert kept and "7" not in kept                               # the finished lines stay cached
    assert (job.tts_dir / "seg_0001.mp3").exists()


def test_an_unvoiceable_line_is_dropped_the_rest_is_dubbed(job, monkeypatch):
    voice = FakeVoice(unvoiceable_on="中文")
    write_srt(lines({3: "中文"}), job.vi_srt)                       # nothing a vi-VN voice could say
    logs = []
    segs = run(job, voice, monkeypatch, log=logs.append)
    assert [c.index for c, _p in segs] == [i for i in range(1, 13) if i != 3]
    assert any("Câu 3" in m for m in logs)


def test_cancel_stops_the_queue_and_returns_a_partial_list(job, monkeypatch):
    voice = FakeVoice()
    flag = {"n": 0}

    def cancel():
        flag["n"] += 1
        return flag["n"] > 3

    segs = run(job, voice, monkeypatch, cancel=cancel)
    assert len(segs) < 12 and voice.calls < 12


def test_vieneu_asks_for_the_cores_it_can_use(monkeypatch):
    from app.providers.tts import vieneu

    monkeypatch.delenv("VIZSUP_VIENEU_WORKERS", raising=False)
    monkeypatch.setenv("VIZSUP_VIENEU_DEVICE", "cpu")                   # the CPU pool rules, not the card
    monkeypatch.setattr(vieneu.os, "cpu_count", lambda: 32)
    monkeypatch.setitem(__import__("sys").modules, "psutil", None)      # no memory probe: core rule only
    assert vieneu.workers() == 6 and vieneu._threads_each(6) == 2
    monkeypatch.setattr(vieneu.os, "cpu_count", lambda: 8)
    assert vieneu.workers() == 1                                        # a small CPU: as before
    monkeypatch.setenv("VIZSUP_VIENEU_WORKERS", "3")
    assert vieneu.workers() == 3
    monkeypatch.setenv("VIZSUP_VIENEU_WORKERS", "1")
    assert vieneu.workers() == 1
    assert vieneu.VieNeuTTS().parallel == 1


def test_the_graphics_card_is_one_engine_and_can_be_switched_off(monkeypatch):
    from app.providers.tts import vieneu

    monkeypatch.setattr(vieneu, "_gpu_failed", False)
    monkeypatch.setenv("VIZSUP_VIENEU_DEVICE", "cpu")
    assert vieneu.use_gpu() is False
    monkeypatch.setenv("VIZSUP_VIENEU_DEVICE", "auto")
    monkeypatch.setattr(vieneu, "_gpu_failed", True)             # a failed load: the CPU from then on
    assert vieneu.use_gpu() is False
    monkeypatch.setattr(vieneu, "_gpu_failed", False)
    monkeypatch.setattr(vieneu, "use_gpu", lambda: True)
    assert vieneu.workers() == 1
