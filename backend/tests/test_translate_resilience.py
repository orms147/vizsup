"""N-4: translate resilience. One transient 429 in batch N used to discard all
N-1 completed paid batches plus the whole download/OCR run. Now: per-batch retry
with backoff, draft checkpointing (translate.checkpoint.json + vi.draft.srt),
and refine failures degrade to that batch's draft instead of killing the run.
BATCH is monkeypatched to 2 so 4 cues = 2 batches.
"""
from __future__ import annotations

import json

import pytest

from app.models import Cue, Job
from app.pipeline import translate
from app.pipeline.srt import parse_srt


def _cues(n=4):
    return [Cue(index=i + 1, start=float(i), end=i + 0.9, text=f"cn{i + 1}") for i in range(n)]


class Good:
    def __init__(self):
        self.calls: list[list[str]] = []

    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        self.calls.append(list(texts))
        return [f"vi:{t}" for t in texts]

    def refine_texts(self, cn, vi, **kw):
        return [f"{v}+polished" for v in vi]


class FlakyOnce(Good):
    """First provider call raises (transient), everything after succeeds."""

    def __init__(self):
        super().__init__()
        self._fails = 1

    def translate_texts(self, texts, **kw):
        if self._fails:
            self._fails -= 1
            raise RuntimeError("429 rate limit")
        return super().translate_texts(texts, **kw)


class DraftDeadBatch2(Good):
    def translate_texts(self, texts, **kw):
        if texts[0] == "cn3":                       # second batch (BATCH=2)
            self.calls.append(list(texts))
            raise RuntimeError("hard provider failure")
        return super().translate_texts(texts, **kw)


class RefineDeadBatch2(Good):
    def refine_texts(self, cn, vi, **kw):
        if cn[0] == "cn3":
            raise RuntimeError("refiner down")
        return [f"{v}+polished" for v in vi]


@pytest.fixture(autouse=True)
def small_batches(monkeypatch):
    monkeypatch.setattr(translate, "BATCH", 2)


def _wire(monkeypatch, **providers):
    monkeypatch.setattr(translate, "get_translator", lambda name: providers[name])


def test_transient_failure_recovers_with_backoff(monkeypatch):
    _wire(monkeypatch, flaky=FlakyOnce())
    sleeps: list[float] = []
    out = translate.translate_cues(_cues(), draft="flaky", _sleep=sleeps.append)
    assert [c.text for c in out] == ["vi:cn1", "vi:cn2", "vi:cn3", "vi:cn4"]
    assert sleeps == [2.0]                          # one retry, then clean run


def test_refine_failure_degrades_to_draft(monkeypatch, tmp_path):
    _wire(monkeypatch, good=Good(), dead=RefineDeadBatch2())
    logs: list[str] = []
    out = translate.translate_cues(_cues(), draft="good", refine="dead",
                                   work_dir=tmp_path, log=logs.append,
                                   _sleep=lambda s: None)
    assert [c.text for c in out] == [
        "vi:cn1+polished", "vi:cn2+polished",       # batch 1 refined
        "vi:cn3", "vi:cn4",                         # batch 2 kept as draft
    ]
    assert any("Tinh chỉnh thất bại" in m for m in logs)


def test_draft_hard_failure_checkpoints_then_resumes(monkeypatch, tmp_path):
    dead = DraftDeadBatch2()
    _wire(monkeypatch, prov=dead)
    sleeps: list[float] = []
    with pytest.raises(RuntimeError, match="hard provider"):
        translate.translate_cues(_cues(), draft="prov", work_dir=tmp_path,
                                 _sleep=sleeps.append)
    assert sleeps == [2.0, 8.0]                     # all 3 attempts spent on batch 2
    ckpt = json.loads((tmp_path / "translate.checkpoint.json").read_text(encoding="utf-8"))
    assert ckpt["done"] == ["vi:cn1", "vi:cn2"]     # batch 1 preserved on disk
    draft = parse_srt(tmp_path / "vi.draft.srt")
    assert [c.text for c in draft] == ["vi:cn1", "vi:cn2"]

    good = Good()
    _wire(monkeypatch, prov=good)
    logs: list[str] = []
    out = translate.translate_cues(_cues(), draft="prov", work_dir=tmp_path,
                                   log=logs.append, _sleep=lambda s: None)
    assert [c.text for c in out] == ["vi:cn1", "vi:cn2", "vi:cn3", "vi:cn4"]
    assert good.calls == [["cn3", "cn4"]]           # batch 1 NOT re-bought
    assert any("Tiếp tục từ điểm dừng" in m for m in logs)


def test_stale_checkpoint_ignored_when_source_changed(monkeypatch, tmp_path):
    dead = DraftDeadBatch2()
    _wire(monkeypatch, prov=dead)
    with pytest.raises(RuntimeError):
        translate.translate_cues(_cues(), draft="prov", work_dir=tmp_path,
                                 _sleep=lambda s: None)
    good = Good()
    _wire(monkeypatch, prov=good)
    changed = _cues()
    changed[0].text = "cn1-SỬA"                     # cn.srt changed → new key
    translate.translate_cues(changed, draft="prov", work_dir=tmp_path,
                             _sleep=lambda s: None)
    assert good.calls[0][0] == "cn1-SỬA"            # full re-run from batch 1


# --- API-2: retry-ladder intelligence ------------------------------------------

def _api_error(msg, *, status=None, retry_after=None):
    from types import SimpleNamespace
    exc = RuntimeError(msg)
    if status is not None:
        exc.status_code = status                # openai/anthropic APIStatusError shape
    if retry_after is not None:
        exc.response = SimpleNamespace(headers={"retry-after": str(retry_after)})
    return exc


def test_retry_fails_fast_on_non_retryable():
    """A 401/bad-key must raise on the FIRST failure — no paid duplicate calls."""
    calls, sleeps = [], []

    def fn():
        calls.append(1)
        raise _api_error("bad key", status=401)

    with pytest.raises(RuntimeError, match="bad key"):
        translate._retry(fn, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == []


def test_retry_honors_retry_after_clamped():
    """429 with Retry-After waits that long; backoff floor still applies (max of both)."""
    calls, sleeps = [], []

    def fn():
        calls.append(1)
        if len(calls) <= 2:
            raise _api_error("rate", status=429, retry_after=7)
        return "ok"

    assert translate._retry(fn, sleep=sleeps.append) == "ok"
    assert sleeps == [7.0, 8.0]                 # max(2,7)=7 then max(8,7)=8


def test_retry_after_is_clamped_to_30s():
    sleeps = []

    def fn():
        raise _api_error("rate", status=429, retry_after=9999)

    with pytest.raises(RuntimeError):
        translate._retry(fn, sleep=sleeps.append)
    assert sleeps == [30.0, 30.0]               # hostile value clamped, not obeyed


def test_retry_plain_exception_keeps_legacy_behavior():
    calls, sleeps = [], []

    def fn():
        calls.append(1)
        raise RuntimeError("boom")              # no status_code → transient

    with pytest.raises(RuntimeError, match="boom"):
        translate._retry(fn, sleep=sleeps.append)
    assert len(calls) == 3 and sleeps == [2.0, 8.0]


# --- API-6: refine-pass checkpointing (protect the most expensive tokens) -------

class CountingRefiner(Good):
    def __init__(self):
        super().__init__()
        self.refine_calls: list[list[str]] = []

    def refine_texts(self, cn, vi, **kw):
        self.refine_calls.append(list(cn))
        return [f"{v}+polished" for v in vi]


_DRAFT4 = ["vi:cn1", "vi:cn2", "vi:cn3", "vi:cn4"]   # Good() draft of _cues(4)
_CN4 = ["cn1", "cn2", "cn3", "cn4"]


def test_refine_checkpoint_skips_completed_batch(monkeypatch, tmp_path):
    refiner = CountingRefiner()
    _wire(monkeypatch, good=Good(), ref=refiner)
    key = translate._ckpt_key("refine|ref", _CN4 + ["\x00"] + _DRAFT4)
    (tmp_path / "translate.refine.checkpoint.json").write_text(
        json.dumps({"key": key, "done": ["vi:cn1+polished", "vi:cn2+polished"]}),
        encoding="utf-8")
    out = translate.translate_cues(_cues(), draft="good", refine="ref",
                                   work_dir=tmp_path, _sleep=lambda s: None)
    assert [c.text for c in out] == [
        "vi:cn1+polished", "vi:cn2+polished", "vi:cn3+polished", "vi:cn4+polished"]
    assert refiner.refine_calls == [["cn3", "cn4"]]     # batch 1 NOT re-refined


def test_refine_checkpoint_stale_key_reruns_all(monkeypatch, tmp_path):
    refiner = CountingRefiner()
    _wire(monkeypatch, good=Good(), ref=refiner)
    (tmp_path / "translate.refine.checkpoint.json").write_text(
        json.dumps({"key": "STALE", "done": ["x1", "x2"]}), encoding="utf-8")
    out = translate.translate_cues(_cues(), draft="good", refine="ref",
                                   work_dir=tmp_path, _sleep=lambda s: None)
    assert [c.text for c in out] == [
        "vi:cn1+polished", "vi:cn2+polished", "vi:cn3+polished", "vi:cn4+polished"]
    assert refiner.refine_calls == [["cn1", "cn2"], ["cn3", "cn4"]]   # full re-run


def test_refine_failure_still_checkpoints_the_degraded_batch(monkeypatch, tmp_path):
    _wire(monkeypatch, good=Good(), dead=RefineDeadBatch2())
    translate.translate_cues(_cues(), draft="good", refine="dead",
                             work_dir=tmp_path, _sleep=lambda s: None)
    ckpt = json.loads((tmp_path / "translate.refine.checkpoint.json").read_text(encoding="utf-8"))
    # batch 1 refined, batch 2 kept as draft — both recorded so a resume re-buys neither
    assert ckpt["done"] == ["vi:cn1+polished", "vi:cn2+polished", "vi:cn3", "vi:cn4"]


def test_translate_success_deletes_recovery_artifacts(monkeypatch, tmp_path):
    _wire(monkeypatch, prov=Good(), ref=CountingRefiner())
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好世界\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n完全不同的一句\n", encoding="utf-8")
    translate.translate(job, draft="prov", refine="ref", glossary={})
    assert job.vi_srt.exists()
    assert not (job.work_dir / "translate.checkpoint.json").exists()
    assert not (job.work_dir / "translate.refine.checkpoint.json").exists()
    assert not (job.work_dir / "vi.draft.srt").exists()
