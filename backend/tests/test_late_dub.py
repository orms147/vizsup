"""F-10: flag lines whose dub started noticeably after their subtitle (= the speech)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.models import Cue, Job
from app.pipeline import assemble, review

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`


@pytest.fixture()
def job(tmp_path, monkeypatch):
    j = Job(id="l", work_dir=tmp_path / "l")
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    return j


def test_a_long_line_makes_the_next_one_late(job, monkeypatch):
    durs = {"a.mp3": 6.0, "b.mp3": 1.0, "c.mp3": 1.0}   # line 1 overruns even at 1.3x → pushes 2
    monkeypatch.setattr(assemble, "probe_duration", lambda p: durs[Path(p).name])
    segs = [(Cue(1, 0.0, 2.0, "một câu rất dài"), job.tts_dir / "a.mp3"),
            (Cue(2, 3.0, 4.0, "câu hai"), job.tts_dir / "b.mp3"),
            (Cue(3, 9.0, 10.0, "câu ba"), job.tts_dir / "c.mp3")]
    assemble._build_render(job, segs, audio_mode="replace", orig_volume=1, dub_volume=1)
    report = json.loads((job.work_dir / "fit_report.json").read_text(encoding="utf-8"))
    late = {r["index"]: r for r in report["late"]}
    assert set(late) == {2} and late[2]["text"] == "câu hai" and late[2]["carry_s"] > 0.2


def test_late_flags_follow_the_same_line_only():
    rows = [{"index": 2, "text": "câu hai", "carry_s": 0.6}]
    cues = [Cue(1, 0, 2, "một"), Cue(2, 3, 4, "câu hai"), Cue(3, 9, 10, "câu ba")]
    assert review.late_lines(cues, rows) == {1: 0.6}
    assert "late" in review.cue_issues(cues, late=rows)[1]
    cues[1].text = "câu hai đã sửa"                              # rewritten → measure again
    assert review.late_lines(cues, rows) == {}
    assert review.late_lines(cues[:1], rows) == {}               # lines merged away


def test_editor_shows_late_lines_after_a_render(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from desktop.cuemodel import EditorCue
    from desktop.views import editor_view

    QApplication.instance() or QApplication([])
    v = editor_view.EditorView()
    v.job = Job(id="e", work_dir=tmp_path / "e")
    v.cues = [EditorCue(start=0.0, end=2.0, vi="một"), EditorCue(start=3.0, end=4.0, vi="câu hai")]
    v._reload(dirty=False)
    assert "late" not in v.issues[1]
    (v.job.work_dir / "fit_report.json").write_text(json.dumps(
        {"late": [{"index": 2, "text": "câu hai", "carry_s": 0.7}]}), encoding="utf-8")
    v.reload_fit_report()
    assert "late" in v.issues[1] and review.LABELS["late"] == "Lồng tiếng trễ"
