"""Find bar (UX audit E2): searching takes ONE row (the list keeps its room at 768 px high);
the replace row appears on "Thay…" / Ctrl+H only."""
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
def view(qapp):
    from desktop.cuemodel import EditorCue
    from desktop.theme import qss
    from desktop.views.editor_view import EditorView

    v = EditorView()
    v.setStyleSheet(qss())
    v.cues = [EditorCue(start=float(i * 5), end=float(i * 5 + 3), vi=f"anh {i}", zh="一")
              for i in range(30)]
    v._reload(dirty=False)
    v.resize(1300, 700)
    v.show()
    v.activateWindow()
    for _ in range(3):
        qapp.processEvents()
    yield v
    v.close()


def test_ctrl_f_is_one_row_and_replace_is_on_demand(view, qapp):
    list_h = view.cue_list.view.height()
    view._toggle_find(True)
    qapp.processEvents()
    assert view.repl_row.isHidden()
    assert view.find_bar.height() <= 62                  # was ~100 with both rows
    assert list_h - view.cue_list.view.height() <= 62
    view.repl_toggle.setChecked(True)
    qapp.processEvents()
    assert not view.repl_row.isHidden() and view.repl_in.hasFocus()
    view.repl_toggle.setChecked(False)
    assert view.repl_row.isHidden()


def test_ctrl_h_opens_find_with_the_replace_row(view, qapp):
    QTest.keyClick(view, Qt.Key_H, Qt.ControlModifier)
    qapp.processEvents()
    assert not view.find_bar.isHidden()
    assert not view.repl_row.isHidden()
    view.find_in.setText("anh 1")
    view.repl_in.setText("em 1")
    view._replace_all()
    assert view.cues[1].vi == "em 1" and view.cues[10].vi == "em 10"
