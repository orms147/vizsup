"""Render page (wave 4, item 1b / audit R4): the "Người nói nhanh…" warning gets a "Xem N câu" button
that opens the editor showing only the lines that will run late at the chosen speed; ↑/↓ walk just
those lines; the amber bar above the list (or a structural edit) brings the full list back."""
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

from app.config import settings  # noqa: E402
from app.pipeline import fit_estimate  # noqa: E402


class Cue:
    def __init__(self, start, end, vi):
        self.start, self.end, self.vi = start, end, vi


def fast_cues():
    """10 lines; 1, 4 and 7 are far too wordy for a 2 s slot even at 1.3×."""
    long = "một hai ba bốn năm sáu bảy tám chín mười mười một mười hai"
    return [Cue(i * 2.0, i * 2.0 + 1.5, long if i in (1, 4, 7) else "ngắn thôi") for i in range(10)]


# --- the pure helper -----------------------------------------------------------------------

def test_late_indexes_are_the_lines_behind_late_share():
    cues = fast_cues()
    rows = fit_estimate.late_indexes(cues, 1.0)
    assert rows == [1, 4, 7]
    assert len(rows) / 10 == pytest.approx(fit_estimate.late_share(cues, 1.0))
    assert fit_estimate.late_indexes(cues, 3.0) == []              # a very fast voice: all fit
    assert fit_estimate.late_indexes([], 1.0) == []
    blank = [Cue(0, 1, ""), Cue(2, 3, "   ")]
    assert fit_estimate.late_indexes(blank, 1.0) == []


# --- the render page -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def test_the_warning_offers_the_lines_and_emits_them(qapp, storage):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    rv.show()
    rv._fit_cues = fast_cues()
    rv._update_fit_hint()
    qapp.processEvents()
    assert not rv.fit_hint.isHidden() and not rv.fit_lines_btn.isHidden()
    assert rv.fit_lines_btn.text() == "Xem 3 câu"
    got = []
    rv.showLinesRequested.connect(lambda rows, why: got.append((rows, why)))
    rv.fit_lines_btn.click()
    assert got == [([1, 4, 7], "Không kịp ở 1.00×")]
    rv._fit_cues = [Cue(i * 2.0, i * 2.0 + 1.5, "ngắn thôi") for i in range(10)]   # nothing late
    rv._update_fit_hint()
    assert rv.fit_lines_btn.isHidden() and rv.fit_hint.isHidden()
    rv.close()


# --- the editor ---------------------------------------------------------------------------------

@pytest.fixture()
def view(qapp, storage):
    from desktop.cuemodel import EditorCue
    from desktop.views.editor_view import EditorView

    v = EditorView()
    v.cues = [EditorCue(start=i * 2.0, end=i * 2.0 + 1.5, vi=f"câu {i}", zh="一") for i in range(10)]
    v._reload(dirty=False)
    v.resize(1300, 800)
    v.show()
    v.activateWindow()
    qapp.processEvents()
    yield v
    v.close()


def test_the_editor_lists_only_those_lines_and_arrows_stay_inside(view, qapp):
    view.show_lines([1, 4, 7], "Không kịp ở 1.00×")
    cl = view.cue_list
    assert cl.visible_rows() == [1, 4, 7] and cl.currentRow() == 1
    assert not cl.lines_bar.isHidden() and cl.lines_btn.text() == "Không kịp ở 1.00× · 3 câu  ✕"
    QTest.keyClick(cl.view, Qt.Key_Down)
    assert view._sel == 4                                         # skips 2 and 3
    QTest.keyClick(cl.view, Qt.Key_Down)
    QTest.keyClick(cl.view, Qt.Key_Down)                          # nothing after 7: stays
    assert view._sel == 7 and cl.lines_filter() == {1, 4, 7}
    QTest.keyClick(cl.view, Qt.Key_Up)
    assert view._sel == 4


def test_the_bar_a_structural_edit_or_a_hidden_row_bring_the_full_list_back(view, qapp):
    cl = view.cue_list
    view.show_lines([1, 4, 7], "x")
    cl.lines_btn.click()
    assert cl.lines_filter() is None and cl.lines_bar.isHidden() and len(cl.visible_rows()) == 10

    view.show_lines([1, 4, 7], "x")
    view.cue_list.selectRow(5)                                    # a row outside the list
    assert cl.lines_filter() is None and len(cl.visible_rows()) == 10

    view.show_lines([1, 4, 7], "x")
    view.cue_list.selectRow(4)
    view._delete()                                                # rows moved: the numbers are stale
    assert cl.lines_filter() is None and cl.lines_bar.isHidden()

    view.show_lines([], "nothing")                                # an empty list is no filter
    assert cl.lines_filter() is None


def test_the_review_filter_and_the_lines_filter_do_not_fight(view, qapp):
    cl = view.cue_list
    cl.set_issues([["Quá dài"] if i in (0, 4) else [] for i in range(10)])
    cl.set_review_only(True)
    assert cl.visible_rows() == [0, 4]
    view.show_lines([1, 4, 7], "x")
    assert cl.visible_rows() == [4]                               # both: the common row


def test_main_window_wires_the_link_to_the_editor(qapp, tmp_path, monkeypatch):
    from desktop import main as m

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    win = m.MainWindow()
    from desktop.cuemodel import EditorCue

    win.editor.cues = [EditorCue(start=i * 2.0, end=i * 2.0 + 1.5, vi=f"câu {i}", zh="一")
                       for i in range(10)]
    win.editor._reload(dirty=False)
    win.stack.setCurrentIndex(m.RENDER)
    win.render.showLinesRequested.emit([2, 3], "Không kịp ở 1.00×")
    assert win.stack.currentIndex() == m.EDITOR
    assert win.editor.cue_list.visible_rows() == [2, 3]
    win.close()
