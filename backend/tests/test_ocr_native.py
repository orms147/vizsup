"""2026-11-12: text detection at the strip's own size (rapidocr's default upscaled a 1920x360
strip to ~3925x736: 2.5x slower and it missed short lines), and the scrap filter that goes with
it. No model is loaded here."""
from __future__ import annotations

import sys
import types

from app.models import Cue
from app.pipeline import ocr


def cue(text, dur=0.5):
    return Cue(index=1, start=10.0, end=10.0 + dur, text=text)


def test_scraps_are_short_low_confidence_reads_without_a_chinese_word():
    assert ocr.is_scrap(cue("一"), 0.52) and ocr.is_scrap(cue("YA"), 0.54)
    assert ocr.is_scrap(cue("TAMAKAI"), 0.66) and ocr.is_scrap(cue("2上"), 0.64)
    assert not ocr.is_scrap(cue("好"), 1.0)                 # a real one-character line
    assert not ocr.is_scrap(cue("快"), 0.93)
    assert not ocr.is_scrap(cue("別的"), 0.76)              # two Chinese characters always stay
    assert not ocr.is_scrap(cue("一", dur=2.0), 0.52)       # stays on screen: not a flicker
    assert not ocr.is_scrap(cue("OK"), 0.95)


def test_a_bare_number_that_flashes_by_is_a_scrap_at_any_score():
    assert ocr.is_scrap(cue("1"), 0.98) and ocr.is_scrap(cue("11"), 0.87)
    assert not ocr.is_scrap(cue("2026"), 0.98)              # a year / a price is content
    assert not ocr.is_scrap(cue("1", dur=1.5), 0.98)
    assert not ocr.is_scrap(cue("10:17 能确认的"), 0.6)


def test_engine_is_built_with_native_detection_and_falls_back(monkeypatch):
    seen = []

    class FakeOCR:
        def __init__(self, params=None):
            seen.append(dict(params or {}))
            if params and "Det.limit_type" in params and len(seen) == 1:
                raise ValueError("unknown key")          # a build that rejects the first rung

    mod = types.SimpleNamespace(RapidOCR=FakeOCR)
    monkeypatch.setitem(sys.modules, "rapidocr", mod)
    monkeypatch.setattr(ocr, "_installed", lambda name: name == "rapidocr")
    monkeypatch.setattr(ocr, "accelerator", lambda: "dml")
    ocr._build_ocr()
    assert seen[0]["Det.limit_type"] == "max" and seen[0]["Det.limit_side_len"] == 1920
    assert seen[0]["EngineConfig.onnxruntime.use_dml"] is True
    assert len(seen) >= 2 and seen[-1].get("EngineConfig.onnxruntime.use_dml") is True


def test_checkpoints_of_the_old_sizing_are_not_reused(tmp_path):
    from app.providers.ocr.rapid import RapidOCRLocal

    tag = RapidOCRLocal().cache_tag()
    assert ocr.DET_TAG in tag
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    old = ocr._checkpoint_key(src, 2.0, "vf", 0.5, "rapidocr")
    assert ocr._checkpoint_key(src, 2.0, "vf", 0.5, tag) != old
