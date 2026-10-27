"""One subtitle per spoken clause (user report 2026-10-27): the original captions showed
"并且还自带支架，" · "可以把手机立起来，" · "真挺好。" one after another, the app showed the whole
line — and its punchline — at once."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models import Cue, Job  # noqa: E402
from app.pipeline import clauses  # noqa: E402
from app.pipeline.srt import parse_srt, write_srt  # noqa: E402

LINE = "并且还自带支架，可以把手机立起来，真挺好。"
# the real word track of that line (asr_words.json of the e2e job), + a neighbour's words
WORDS = [["机", 123.32, 123.54], ["并", 123.54, 124.04], ["且", 124.04, 124.20],
         ["还", 124.20, 124.36], ["自", 124.36, 124.50], ["带", 124.50, 124.60],
         ["支", 124.60, 124.74], ["架", 124.74, 125.16], ["可以", 125.16, 125.30],
         ["把", 125.30, 125.38], ["手", 125.38, 125.52], ["机", 125.52, 125.66],
         ["立", 125.66, 125.82], ["起", 125.82, 125.94], ["来", 125.94, 126.10],
         ["真", 126.10, 126.40], ["挺", 126.40, 126.54], ["好", 126.54, 126.76],
         ["只", 126.76, 126.90]]


def test_line_splits_where_each_clause_is_spoken():
    out = clauses.split_cn([Cue(index=1, start=123.54, end=126.71, text=LINE)], WORDS)
    assert [c.text for c in out] == ["并且还自带支架，", "可以把手机立起来，", "真挺好。"]
    assert [round(c.start, 2) for c in out] == [123.54, 125.16, 126.10]
    assert out[-1].end == 126.71 and [c.index for c in out] == [1, 2, 3]


def test_short_clauses_stay_joined_and_single_clauses_untouched():
    cues = [Cue(index=1, start=0.0, end=1.0, text="对，好的。"),        # 0.5 s pieces → one line
            Cue(index=2, start=1.0, end=3.0, text="今天天气很好")]
    out = clauses.split_cn(cues, [])
    assert [c.text for c in out] == ["对，好的。", "今天天气很好"]


def test_without_matching_words_falls_back_to_character_share():
    out = clauses.split_cn([Cue(index=1, start=0.0, end=4.0, text="一二三四，五六七八。")], [])
    assert [(c.start, c.end) for c in out] == [(0.0, 2.0), (2.0, 4.0)]


def test_translated_line_splits_with_matching_vietnamese_clauses():
    vi = "Và còn có sẵn giá đỡ, có thể dựng máy đứng lên, thật sự rất ổn."
    parts = clauses.split_pair(LINE, vi, 123.54, 126.71, WORDS)
    assert [p[1] for p in parts] == ["Và còn có sẵn giá đỡ,", "có thể dựng máy đứng lên,",
                                     "thật sự rất ổn."]
    assert round(parts[2][2], 2) == 126.10
    # different clause count in the translation → leave it alone (never guess)
    assert clauses.split_pair(LINE, "Còn có giá đỡ để dựng máy, rất ổn.", 123.54, 126.71,
                              WORDS) is None


def test_split_job_rewrites_cn_srt_and_needs_a_word_track(tmp_path):
    job = Job(id="j", work_dir=tmp_path)
    write_srt([Cue(index=1, start=123.54, end=126.71, text=LINE)], job.cn_srt)
    assert clauses.split_job(job) is None                   # no asr_words.json → untouched
    (tmp_path / "asr_words.json").write_text(json.dumps({"words": WORDS, "groups": []}),
                                             encoding="utf-8")
    assert clauses.split_job(job) == (1, 3)
    assert len(parse_srt(job.cn_srt)) == 3


def test_editor_action_is_one_undo_step(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from desktop.cuemodel import EditorCue
    from desktop.views import editor_view

    QApplication.instance() or QApplication([])
    v = editor_view.EditorView()
    v.job = Job(id="j", work_dir=tmp_path)
    (tmp_path / "asr_words.json").write_text(json.dumps({"words": WORDS, "groups": []}),
                                             encoding="utf-8")
    v.cues = [EditorCue(start=123.54, end=126.71, zh=LINE,
                        vi="Và còn có sẵn giá đỡ, có thể dựng máy đứng lên, thật sự rất ổn.")]
    v._reload(dirty=False)
    v._undo_stack.clear()
    v._split_clauses()
    assert [c.vi for c in v.cues][-1] == "thật sự rất ổn." and len(v.cues) == 3
    assert len(v._undo_stack) == 1
    v._undo_action()
    assert len(v.cues) == 1
