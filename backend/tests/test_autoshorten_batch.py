"""API-5: batch the pre-gate auto-shorten. Overlong lines used to cost one API
round-trip EACH (serial, pre-gate); now every overlong line in a BATCH-sized chunk
goes in ONE rewrite() call. The per-line acceptance gate (only take a genuinely
shorter candidate) and the parse_numbered fallback (a missing/echoed line keeps the
original) are preserved, so the worst case is "a line stays long", never corruption.
"""
from __future__ import annotations

import pytest

from app.models import Cue
from app.pipeline import translate

LONG = "một hai ba bốn năm sáu bảy tám chín mười"   # 10 words → overflows a 1.0 s slot
SHORT = "ngắn gọn"                                    # 2 words → fits


def _mixed_cues():
    # starts 0,1,2,… → each slot ~1.0 s; lines 0,3,6 are long (overlong), rest short
    cues, vi = [], []
    for i in range(10):
        cues.append(Cue(index=i + 1, start=float(i), end=i + 0.9, text="cn"))
        vi.append(LONG if i in (0, 3, 6) else SHORT)
    return cues, vi


class RecordingRewriter:
    def __init__(self, reply_fn):
        self.calls: list[tuple[str, int]] = []
        self._reply_fn = reply_fn

    def available(self):
        return True

    def rewrite(self, body, instruction, *, max_tokens=300):
        self.calls.append((body, max_tokens))
        return self._reply_fn(body)


def _wire(monkeypatch, prov):
    monkeypatch.setattr(translate, "get_translator", lambda name: prov)
    return prov


def test_all_overlong_lines_one_call(monkeypatch):
    cues, vi = _mixed_cues()
    prov = _wire(monkeypatch, RecordingRewriter(lambda body: "1| a b\n2| a b\n3| a b"))
    out = translate._autoshorten(cues, vi, "fake")
    assert len(prov.calls) == 1                        # ONE round-trip, not three
    body = prov.calls[0][0]
    assert body.count("(rút còn ~") == 3               # exactly the 3 overlong lines
    assert [out[i] for i in (0, 3, 6)] == ["a b", "a b", "a b"]
    assert [out[i] for i in (1, 2, 4, 5, 7, 8, 9)] == [SHORT] * 7   # fitting lines untouched


def test_missing_reply_line_keeps_original(monkeypatch):
    cues, vi = _mixed_cues()
    # reply omits line 2 (the 2nd overlong cue, index 3) → it keeps its original
    _wire(monkeypatch, RecordingRewriter(lambda body: "1| a b\n3| a b"))
    out = translate._autoshorten(cues, vi, "fake")
    assert out[0] == "a b" and out[6] == "a b"
    assert out[3] == LONG                              # missing line falls back to original


def test_longer_candidate_rejected(monkeypatch):
    cues, vi = _mixed_cues()
    longer = " ".join(["w"] * 12)                       # 12 words > original 10
    _wire(monkeypatch, RecordingRewriter(lambda body: f"1| {longer}\n2| a b\n3| a b"))
    out = translate._autoshorten(cues, vi, "fake")
    assert out[0] == LONG                              # acceptance gate rejects the longer line
    assert out[3] == "a b" and out[6] == "a b"


def test_no_overlong_lines_zero_calls(monkeypatch):
    cues = [Cue(index=i + 1, start=float(i), end=i + 0.9, text="cn") for i in range(4)]
    vi = [SHORT] * 4
    prov = _wire(monkeypatch, RecordingRewriter(lambda body: "unused"))
    out = translate._autoshorten(cues, vi, "fake")
    assert prov.calls == [] and out == vi              # nothing overflows → no API call


def test_provider_without_rewrite_is_noop(monkeypatch):
    cues, vi = _mixed_cues()

    class NoRewrite:
        def available(self):
            return True

    _wire(monkeypatch, NoRewrite())
    assert translate._autoshorten(cues, vi, "fake") == vi
