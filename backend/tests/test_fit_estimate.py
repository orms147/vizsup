"""N-6 (light): speech-fit estimation + pre-gate auto-shorten."""
from __future__ import annotations

import pytest

from app.models import Cue
from app.pipeline import translate
from app.pipeline.fit_estimate import SEG_PAD, SYL_PER_SEC, fits, slot_seconds, speech_seconds


def _c(i, s, e, t="x"):
    return Cue(index=i, start=s, end=e, text=t)


def test_speech_seconds_scales_with_words():
    assert speech_seconds("") == 0.0
    assert speech_seconds("một hai ba bốn tám") == pytest.approx(SEG_PAD + 5 / SYL_PER_SEC)
    # the fixed per-segment pad does not shrink with a faster voice; the words do
    assert speech_seconds("một hai ba bốn tám", 1.25) == pytest.approx(SEG_PAD + 5 / (SYL_PER_SEC * 1.25))


def test_slot_spans_to_next_cue_and_last_uses_duration():
    cues = [_c(1, 0.0, 1.0), _c(2, 5.0, 6.5)]
    assert slot_seconds(cues, 0) == 5.0     # gap reclaimed, like the assemble planner
    assert slot_seconds(cues, 1) == 1.5     # last cue → own duration


def test_fits_honours_tempo_cap():
    assert fits("một hai ba", 1.0)                   # 3 syl ≈ 0.92 s trimmed
    assert not fits("một " * 20, 2.0)                # 20 syl ≈ 4.7 s > 2.6 s


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


def test_measured_rate_is_speech_only_not_the_padded_one():
    """2026-10-30, 269 real +30% segments with their silence cut: 0.26 s + 0.166 s/word, i.e.
    4.6 words/s at 1.0×. The old 3.0 (2026-10-27) counted the ~0.9 s of silence each segment
    carried, so 3-5 word lines that fit were called "Quá dài" and shortened for nothing."""
    assert 4.0 <= SYL_PER_SEC <= 5.0
    assert 0.15 <= SEG_PAD <= 0.4


def test_suggest_rate_for_a_fast_talker():
    from app.pipeline.fit_estimate import late_share, suggest_rate

    # 9 words in a 1.4 s slot: 2.25 s at 1.0× vs 1.4 × 1.3 = 1.82 s → late; 1.79 s at 1.3×
    cues = [_c(i, i * 1.4, i * 1.4 + 1.3) for i in range(10)]
    for c in cues:
        c.text = "một hai ba bốn năm sáu bảy tám chín"
    assert late_share(cues, 1.0) > 0.5
    rate, share = suggest_rate(cues)
    assert rate == 1.3 and share <= 0.15
    slow = [_c(i, i * 3.0, i * 3.0 + 3.0) for i in range(5)]
    for c in slow:
        c.text = "một hai"
    assert suggest_rate(slow) == (1.0, 0.0)


def test_word_budget_inverts_fits():
    from app.pipeline.fit_estimate import word_budget

    for slot in (0.8, 1.5, 3.0):
        for rate in (1.0, 1.3):
            n = word_budget(slot, rate)
            assert fits("a " * n, slot, rate)
            assert not fits("a " * (n + 2), slot, rate)


def test_parse_rate():
    from app.pipeline.fit_estimate import parse_rate

    assert parse_rate("+30%") == pytest.approx(1.3) and parse_rate("-10%") == pytest.approx(0.9)
    assert parse_rate(1.25) == 1.25 and parse_rate("1.1") == pytest.approx(1.1)
    assert parse_rate(None) is None and parse_rate("fast") is None and parse_rate(0) is None


def test_job_rate_prefers_the_project_then_render_opts_then_the_series(tmp_path, monkeypatch):
    import json

    from app.config import settings
    from app.models import Job
    from app.pipeline import series
    from app.pipeline.fit_estimate import PREGATE_RATE, job_rate

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "st")
    job = Job(id="j", work_dir=tmp_path / "j", metadata={"title": "《某剧》03"})
    assert job_rate(job) == PREGATE_RATE                         # nothing known
    prof = series.resolve(job)
    prof.render = {"rate": "+10%"}
    series.save(prof)
    assert job_rate(job) == pytest.approx(1.1)                   # the series' look
    (job.work_dir / "render_opts.json").write_text(json.dumps({"rate": "+30%"}), encoding="utf-8")
    assert job_rate(job) == pytest.approx(1.3)                   # this project's last render
    (job.work_dir / "voice.json").write_text(json.dumps({"rate": 1.2}), encoding="utf-8")
    assert job_rate(job) == pytest.approx(1.2)                   # what the render page holds
    (job.work_dir / "voice.json").write_text("{broken", encoding="utf-8")
    assert job_rate(job) == pytest.approx(1.3)                   # junk files never raise
