"""API-9: parallel draft/refine batches (sliding-window, up to 4-wide), checkpoint
v2 (per-batch, gaps explicit), and cancel mid-translate. A hard failure or a cancel
must leave every completed batch on disk so a resume re-buys nothing, and cancel
must stop admitting new batches immediately (no further provider calls).
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from app.models import Cue
from app.pipeline import translate


def _cues(n=6):
    return [Cue(index=i + 1, start=float(i), end=i + 0.9, text=f"cn{i}") for i in range(n)]


def _wire(monkeypatch, prov):
    monkeypatch.setattr(translate, "get_translator", lambda name: prov)
    return prov


@pytest.fixture(autouse=True)
def small_batches(monkeypatch):
    monkeypatch.setattr(translate, "BATCH", 2)          # 6 cues → 3 batches


class Good:
    parallel_batches = 4

    def __init__(self):
        self.calls: list[list[str]] = []
        self._lock = threading.Lock()

    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        with self._lock:
            self.calls.append(list(texts))
        return [f"vi:{t}" for t in texts]


def test_batches_run_concurrently(monkeypatch):
    """3 batches must be in flight at once: each blocks on a Barrier(3) that only
    releases when all three threads reach it (else it times out and the task raises)."""
    class BarrierProvider(Good):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(3, timeout=5)

        def translate_texts(self, texts, **kw):
            with self._lock:
                self.calls.append(list(texts))
            self.barrier.wait()                          # proves 3-way concurrency
            return [f"vi:{t}" for t in texts]

    prov = _wire(monkeypatch, BarrierProvider())
    out = translate.translate_cues(_cues(6), draft="p")
    assert len(prov.calls) == 3
    assert [c.text for c in out] == [f"vi:cn{i}" for i in range(6)]


def test_out_of_order_completion_is_ordered(monkeypatch):
    class SlowFirst(Good):
        def translate_texts(self, texts, **kw):
            if texts[0] == "cn0":
                time.sleep(0.05)                         # batch 0 finishes last
            return super().translate_texts(texts, **kw)

    _wire(monkeypatch, SlowFirst())
    out = translate.translate_cues(_cues(6), draft="p")
    assert [c.text for c in out] == [f"vi:cn{i}" for i in range(6)]


def test_kill_middle_batch_checkpoints_the_rest(monkeypatch, tmp_path):
    class KillBatch2(Good):
        def translate_texts(self, texts, **kw):
            if texts[0] == "cn2":                        # batch index 1 (cn2,cn3)
                raise RuntimeError("kill batch 2")
            return super().translate_texts(texts, **kw)

    _wire(monkeypatch, KillBatch2())
    with pytest.raises(RuntimeError, match="kill batch 2"):
        translate.translate_cues(_cues(6), draft="p", work_dir=tmp_path, _sleep=lambda s: None)
    ckpt = json.loads((tmp_path / "translate.checkpoint.json").read_text(encoding="utf-8"))
    assert ckpt["v"] == 2 and set(ckpt["batches"]) == {"0", "2"}   # 1 & 3 preserved

    good = _wire(monkeypatch, Good())
    out = translate.translate_cues(_cues(6), draft="p", work_dir=tmp_path, _sleep=lambda s: None)
    assert [c.text for c in out] == [f"vi:cn{i}" for i in range(6)]
    assert good.calls == [["cn2", "cn3"]]               # only the killed batch re-bought


def test_v1_checkpoint_loads_as_prefix_batches(monkeypatch, tmp_path):
    src = [f"cn{i}" for i in range(6)]
    key = translate._ckpt_key("p", src)
    (tmp_path / "translate.checkpoint.json").write_text(
        json.dumps({"key": key, "done": ["vi:cn0", "vi:cn1", "vi:cn2", "vi:cn3"]}),
        encoding="utf-8")
    good = _wire(monkeypatch, Good())
    out = translate.translate_cues(_cues(6), draft="p", work_dir=tmp_path, _sleep=lambda s: None)
    assert [c.text for c in out] == [f"vi:cn{i}" for i in range(6)]
    assert good.calls == [["cn4", "cn5"]]              # v1 prefix covered batches 0,1


def test_cancel_stops_admitting_new_batches(monkeypatch, tmp_path):
    class Sequential(Good):
        parallel_batches = 1                            # width 1 → cancel gates the next

    prov = _wire(monkeypatch, Sequential())

    def cancel():
        return len(prov.calls) >= 1                     # cancel once batch 1 is done

    with pytest.raises(translate.TranslateCancelled):
        translate.translate_cues(_cues(6), draft="p", work_dir=tmp_path,
                                 cancel=cancel, _sleep=lambda s: None)
    ckpt = json.loads((tmp_path / "translate.checkpoint.json").read_text(encoding="utf-8"))
    assert set(ckpt["batches"]) == {"0"}               # only batch 1 completed
    assert prov.calls == [["cn0", "cn1"]]              # NO further provider calls


def test_parallel_refine_cancel(monkeypatch, tmp_path):
    """Cancel also applies to the refine pass; completed refine batches persist."""
    class SeqRefiner(Good):
        parallel_batches = 1

        def __init__(self):
            super().__init__()
            self.refine_calls: list[list[str]] = []

        def refine_texts(self, cn, vi, **kw):
            with self._lock:
                self.refine_calls.append(list(cn))
            return [f"{v}+r" for v in vi]

    prov = _wire(monkeypatch, SeqRefiner())

    def cancel():
        return len(prov.refine_calls) >= 1

    with pytest.raises(translate.TranslateCancelled):
        translate.translate_cues(_cues(6), draft="p", refine="p", work_dir=tmp_path,
                                 cancel=cancel, _sleep=lambda s: None)
    ckpt = json.loads(
        (tmp_path / "translate.refine.checkpoint.json").read_text(encoding="utf-8"))
    assert set(ckpt["batches"]) == {"0"}
    assert prov.refine_calls == [["cn0", "cn1"]]       # refine cancelled after batch 1
