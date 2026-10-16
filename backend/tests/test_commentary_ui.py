"""F-4 editor tab "Bình luận": add / place / warn / save the user's own notes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

pytest.importorskip("PySide6.QtWidgets")

from app.models import Job  # noqa: E402
from app.pipeline import commentary as cm  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, tmp_path, monkeypatch):
    from desktop.cuemodel import EditorCue
    from desktop.views import editor_view

    v = editor_view.EditorView()
    v.job = Job(id="u", work_dir=tmp_path / "u")
    v.cues = [EditorCue(start=2.0, end=5.0, vi="xin chào", zh="你好"),
              EditorCue(start=8.0, end=11.0, vi="tạm biệt", zh="再见")]
    v._reload(dirty=False)
    monkeypatch.setattr(v, "_duration", lambda: 30.0)
    v.commentary.set_duration(30.0)
    return v


def test_intro_note_is_saved_and_flagged_on_the_timeline(view):
    view._add_note("intro")
    view.commentary.text_edit.setPlainText("Chào các bạn, hôm nay mình xem video này.")
    notes = cm.load(view.job)
    assert [(n.at, n.mode) for n in notes] == [(0.0, "pause")]
    assert "Chào các bạn" in notes[0].text
    assert view.timeline._notes == [(0.0, "pause")]
    assert "1 đoạn" in view.commentary.share_lbl.text()


def test_here_moves_a_pause_to_the_end_of_the_sentence(view, monkeypatch):
    monkeypatch.setattr(view.video, "current_time", lambda: 3.5)      # inside "xin chào"
    view._add_note("here")
    assert view.commentary.selected().at == 5.0


def test_an_over_note_on_a_line_warns(view, monkeypatch):
    monkeypatch.setattr(view.video, "current_time", lambda: 6.0)      # a gap
    view._add_note("here")
    view.commentary.text_edit.setPlainText("Một hai ba bốn năm sáu bảy tám chín mười")
    view.commentary.mode_over.setChecked(True)
    assert "Nói đè lên câu 2" in view.commentary.warn_lbl.text()
    view.commentary.mode_pause.setChecked(True)
    assert view.commentary.warn_lbl.text() == ""


def test_deleting_the_last_note_removes_the_file(view):
    view._add_note("outro")
    view.commentary.text_edit.setPlainText("Cảm ơn đã xem")
    assert (view.job.work_dir / cm.FILE).exists()
    view.commentary.del_btn.click()
    assert not (view.job.work_dir / cm.FILE).exists() and view.timeline._notes == []


def test_post_checklist_counts_the_commentary():
    from app.pipeline import disclosure

    none = disclosure.checklist(ai_voice=True, credit=True, rights="own")
    some = disclosure.checklist(ai_voice=True, credit=True, rights="own", commentary=(3, 0.034))
    assert none[-1][0] is False and some[-1][0] is True and "3.4%" in some[-1][1]
