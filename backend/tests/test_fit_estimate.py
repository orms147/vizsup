"""N-6 (light): speech-fit estimation + pre-gate auto-shorten."""
from __future__ import annotations

from app.models import Cue
from app.pipeline import translate
from app.pipeline.fit_estimate import fits, slot_seconds, speech_seconds


def _c(i, s, e, t="x"):
    return Cue(index=i, start=s, end=e, text=t)


def test_speech_seconds_scales_with_words():
    assert speech_seconds("") == 0.0
    assert speech_seconds("một hai ba bốn tám") == 5 / 4.8


def test_slot_spans_to_next_cue_and_last_uses_duration():
    cues = [_c(1, 0.0, 1.0), _c(2, 5.0, 6.5)]
    assert slot_seconds(cues, 0) == 5.0     # gap reclaimed, like the assemble planner
    assert slot_seconds(cues, 1) == 1.5     # last cue → own duration


def test_fits_honours_tempo_cap():
    assert fits("một hai ba", 1.0)                   # 3 syl ≈ 0.63s
    assert not fits("một " * 20, 2.0)                # 20 syl ≈ 4.2s > 2.6s


class _Rewriter:
    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        return ["một " * 20] * len(texts)            # draft way too long for its slot

    def rewrite(self, body, instruction, **kw):      # API-5: batched, numbered reply
        n = len([ln for ln in body.splitlines() if ln.strip()])
        return "\n".join(f"{i}| ngắn gọn rồi" for i in range(1, n + 1))


class _NoRewrite:
    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        return ["một " * 20] * len(texts)


def test_autoshorten_rewrites_overlong_lines(monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _Rewriter())
    logs: list[str] = []
    cues = [_c(1, 0.0, 2.0), _c(2, 2.0, 60.0)]       # cue 1 tight, cue 2 huge slot
    out = translate.translate_cues(cues, draft="fake", log=logs.append)
    assert out[0].text == "ngắn gọn rồi"             # shortened to fit
    assert out[1].text.startswith("một")             # fits fine → untouched
    assert any("Tự rút gọn 1 câu" in m for m in logs)


def test_autoshorten_skipped_without_rewrite_support(monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _NoRewrite())
    cues = [_c(1, 0.0, 2.0)]
    out = translate.translate_cues(cues, draft="fake")
    assert out[0].text.startswith("một")             # no rewrite() → left as-is


def test_autoshorten_opt_out(monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _Rewriter())
    cues = [_c(1, 0.0, 2.0)]
    out = translate.translate_cues(cues, draft="fake", auto_shorten=False)
    assert out[0].text.startswith("một")
