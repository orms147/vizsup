"""Editor keyboard loop (UX audit E3/E4): D / Ctrl+D = "Đã xem" and on to the next pending
line, Ctrl+↑/↓ change line from inside the text box (cursor stays in the box), Esc in the box
hands the keyboard back to the list."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, monkeypatch):
    from desktop.cuemodel import EditorCue
    from desktop.views.editor_view import EditorView

    v = EditorView()
    v.cues = [EditorCue(start=float(i * 5), end=float(i * 5 + 3), vi=f"câu {i}", zh="一")
              for i in range(8)]
    v._reload(dirty=False)
    t = {"now": 0.0}
    monkeypatch.setattr(v.video, "current_time", lambda: t["now"])
    monkeypatch.setattr(v.video, "duration", lambda: 40.0)
    monkeypatch.setattr(v.video, "seek_seconds", lambda s: t.__setitem__("now", s))
    v.resize(1300, 800)
    v.show()
    v.activateWindow()
    qapp.processEvents()
    yield v
    v.close()


def _flag(v, rows):
    """Make ``rows`` pending (flagged, not yet seen) without depending on the QA rules."""
    issues = [["Quá dài"] if i in rows else [] for i in range(len(v.cues))]
    v.cue_list.set_issues(issues)
    v.cue_list.reapply_filter()


def test_d_marks_seen_and_jumps_to_next_pending(view):
    from desktop import cuemodel

    _flag(view, {2, 5})
    view.cue_list.selectRow(2)
    view.cue_list.view.setFocus()
    QTest.keyClick(view.cue_list.view, Qt.Key_D)
    assert cuemodel.is_reviewed(view.cues[2])
    assert view._sel == 5
    assert view.pending_count() == 1


def test_ctrl_d_works_from_the_text_box(view):
    from desktop import cuemodel

    _flag(view, {1, 4})
    view.cue_list.selectRow(1)
    view.inspector.focus_text()
    assert view.inspector.vi_edit.hasFocus()
    QTest.keyClick(view.inspector.vi_edit, Qt.Key_D, Qt.ControlModifier)
    assert cuemodel.is_reviewed(view.cues[1])
    assert view._sel == 4
    assert view.cues[1].vi == "câu 1"          # the key did not type a letter into the box


def test_ctrl_arrows_change_line_and_keep_the_cursor_in_the_box(view):
    view.cue_list.selectRow(3)
    view.inspector.focus_text()
    QTest.keyClick(view.inspector.vi_edit, Qt.Key_Down, Qt.ControlModifier)
    assert view._sel == 4 and view.inspector.vi_edit.hasFocus()
    assert view.inspector.vi_edit.toPlainText() == "câu 4"
    QTest.keyClick(view.inspector.vi_edit, Qt.Key_Up, Qt.ControlModifier)
    QTest.keyClick(view.inspector.vi_edit, Qt.Key_Up, Qt.ControlModifier)
    assert view._sel == 2 and view.inspector.vi_edit.hasFocus()


def test_esc_in_the_text_box_returns_to_the_list(view):
    view.cue_list.selectRow(2)
    view.inspector.focus_text()
    assert view.inspector.vi_edit.hasFocus()
    QTest.keyClick(view.inspector.vi_edit, Qt.Key_Escape)
    assert view.cue_list.view.hasFocus()
    assert view.cues[2].vi == "câu 2"
    # and now the plain keys are live again: ↓ steps to the next line
    QTest.keyClick(view.cue_list.view, Qt.Key_Down)
    assert view._sel == 3
