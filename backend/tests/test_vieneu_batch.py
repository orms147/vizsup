"""2026-11-11: VieNeu on the graphics card speaks whole groups of lines in one batched call
(``infer_batch``: 0.37 → 0.06 s/line on the RTX 4070 Laptop). The provider's ``synthesize_many``
with a fake engine: grouping, per-line failures, the empty answer the pipeline skips, cancel, the
CPU fallback, and the pipeline's batch path picking it up. No model, no ffmpeg."""
from __future__ import annotations

import pytest

from app.models import Cue, Job
from app.pipeline import tts as tts_mod
from app.pipeline.srt import write_srt
from app.providers.tts import vieneu


class FakeEngine:
    backend = "pytorch"
    sample_rate = 48000

    def __init__(self, *, batch_fails=False):
        self.batches: list[int] = []
        self.singles: list[str] = []
        self.batch_fails = batch_fails

    def infer_batch(self, texts, voice=None, batch_size=None):
        if self.batch_fails:
            raise RuntimeError("CUDA out of memory")
        self.batches.append(len(texts))
        return [[] if "rỗng" in t else [0.1] * 10 for t in texts]

    def infer(self, text, voice=None):
        self.singles.append(text)
        if "hỏng" in text:
            raise RuntimeError("bad line")
        return [] if "rỗng" in text else [0.1] * 10


@pytest.fixture()
def gpu(monkeypatch, tmp_path):
    eng = FakeEngine()
    monkeypatch.setattr(vieneu, "use_gpu", lambda: True)
    monkeypatch.setattr(vieneu, "_acquire", lambda: eng)
    monkeypatch.setattr(vieneu, "_release", lambda e: None)

    def save(wav, rate_hz, out_path, rate):
        if len(wav) == 0:
            raise vieneu.NoAudioReceived("no audio")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp3")
        return out_path

    monkeypatch.setattr(vieneu, "_save", save)
    return eng


def items(tmp_path, texts):
    return [(str(i), t, tmp_path / f"s{i}.mp3") for i, t in enumerate(texts, 1)]


def test_groups_of_lines_share_one_call(gpu, tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "GROUP", 4)
    seen = []
    res = vieneu.VieNeuTTS().synthesize_many(items(tmp_path, [f"câu {i}" for i in range(10)]),
                                             voice="Ngọc Huyền", rate="+21%",
                                             on_done=lambda k, e: seen.append((k, e)))
    assert gpu.batches == [4, 4, 2] and gpu.singles == []
    assert [k for k, e in res] == [str(i) for i in range(1, 11)] and all(e is None for _k, e in res)
    assert seen == res and all((tmp_path / f"s{i}.mp3").exists() for i in range(1, 11))


def test_an_empty_answer_is_the_skippable_error(gpu, tmp_path):
    res = dict(vieneu.VieNeuTTS().synthesize_many(items(tmp_path, ["một", "rỗng", "ba"])))
    assert res["1"] is None and res["3"] is None
    assert type(res["2"]).__name__ == "NoAudioReceived" and tts_mod._unvoiceable(res["2"])


def test_a_failed_batch_falls_back_to_single_lines(gpu, tmp_path):
    gpu.batch_fails = True
    res = dict(vieneu.VieNeuTTS().synthesize_many(items(tmp_path, ["một", "hỏng", "ba"])))
    assert gpu.singles == ["một", "hỏng", "ba"]
    assert res["1"] is None and res["3"] is None and isinstance(res["2"], RuntimeError)


def test_cancel_between_groups_keeps_what_is_done(gpu, tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "GROUP", 2)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1                       # after the first group

    res = vieneu.VieNeuTTS().synthesize_many(items(tmp_path, ["a", "b", "c", "d"]), cancel=cancel)
    assert [type(e).__name__ if e else None for _k, e in res] == [
        None, None, "InterruptedError", "InterruptedError"]


def test_cpu_engine_after_a_refused_card_uses_the_line_path(gpu, tmp_path, monkeypatch):
    gpu.backend = "onnx"
    said = []
    monkeypatch.setattr(vieneu, "workers", lambda: 2)
    monkeypatch.setattr(vieneu.VieNeuTTS, "synthesize",
                        lambda self, t, p, voice=None, rate=None: said.append(t))
    res = vieneu.VieNeuTTS().synthesize_many(items(tmp_path, ["a", "b", "c"]))
    assert sorted(said) == ["a", "b", "c"] and all(e is None for _k, e in res)
    assert gpu.batches == []


def test_batch_only_on_the_card(monkeypatch):
    monkeypatch.setattr(vieneu, "use_gpu", lambda: False)
    assert vieneu.VieNeuTTS().supports_batch is False
    monkeypatch.setattr(vieneu, "use_gpu", lambda: True)
    assert vieneu.VieNeuTTS().supports_batch is True


def test_pipeline_takes_the_batch_path_and_resumes(gpu, tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu.VieNeuTTS, "available", lambda self: True)
    job = Job(id="b", work_dir=tmp_path / "b")
    write_srt([Cue(index=i, start=float(i), end=i + 0.9, text=t)
               for i, t in enumerate(["xin chào", "rỗng", "tạm biệt"], 1)], job.vi_srt)
    logs = []
    segs = tts_mod._synthesize(job, provider="vieneu", voice="Ngọc Huyền", log=logs.append)
    assert [c.index for c, _p in segs] == [1, 3]            # the empty one is skipped, not fatal
    assert any("Câu 2" in m for m in logs)
    assert gpu.batches == [3]
    again = tts_mod._synthesize(job, provider="vieneu", voice="Ngọc Huyền")
    assert [c.index for c, _p in again] == [1, 3]
    assert gpu.batches == [3] and gpu.singles == ["rỗng"]   # only line 2 is tried again


def test_prewarm_is_off_in_tests_and_idempotent(monkeypatch):
    started = []
    monkeypatch.setattr(vieneu.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    vieneu.prewarm()                                        # conftest: VIZSUP_VIENEU_PREWARM=0
    assert started == []
    monkeypatch.setenv("VIZSUP_VIENEU_PREWARM", "1")
    monkeypatch.setattr(vieneu, "use_gpu", lambda: True)
    monkeypatch.setattr(vieneu, "model_cached", lambda: True)
    monkeypatch.setattr(vieneu, "_made", 0)
    vieneu.prewarm()
    assert len(started) == 1
    monkeypatch.setattr(vieneu, "_made", 1)                 # an engine exists: nothing to do
    vieneu.prewarm()
    assert len(started) == 1
