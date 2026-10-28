"""Right-click menus (UX audit E8): a row of the cue list and a block of the timeline open
the same menu, built only from existing editor actions; Xóa keeps its undo step; a right
press on the timeline never starts a drag or a scrub."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402


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
              for i in range(6)]
    v._reload(dirty=False)
    monkeypatch.setattr(v.video, "current_time", lambda: 1.0)
    monkeypatch.setattr(v.video, "duration", lambda: 30.0)
    v.resize(1300, 800)
    v.show()
    qapp.processEvents()
    yield v
    v.close()


def _menus_open(view) -> int:
    from PySide6.QtWidgets import QMenu

    return sum(1 for m in view.findChildren(QMenu) if m.isVisible())


def _act(menu, prefix):
    return next(a for a in menu.actions() if a.text().startswith(prefix))


def test_menu_offers_the_editor_actions(view):
    view.cue_list.selectRow(2)
    m = view._cue_menu(2)
    texts = [a.text().split("\t")[0] for a in m.actions() if not a.isSeparator()]
    assert texts == ["Tách tại vị trí phát", "Gộp với câu sau", "Thêm câu sau câu này",
                     "Dịch lại (3 phương án)", "Rút gọn cho vừa thời lượng",
                     "Tự canh theo giọng nói", "Khóa timing",
                     "Đã xem, sang câu cần xem lại tiếp", "Xóa câu này (Ctrl+Z hoàn tác)"]
    assert not _act(m, "Đã xem").isEnabled()           # nothing flagged on this line
    assert not view._cue_menu(5).actions()[1].isEnabled()   # no "next line" to merge with


def test_delete_split_merge_lock_from_the_menu(view):
    view.cue_list.selectRow(2)
    _act(view._cue_menu(2), "Xóa câu này").trigger()
    assert len(view.cues) == 5 and view.cues[2].vi == "câu 3"
    view._undo_action()
    assert len(view.cues) == 6 and view.cues[2].vi == "câu 2"

    view.cue_list.selectRow(0)                          # playhead 1.0 s is inside 0–3 s
    _act(view._cue_menu(0), "Tách").trigger()
    assert len(view.cues) == 7
    view.cue_list.selectRow(0)
    _act(view._cue_menu(0), "Gộp").trigger()
    assert len(view.cues) == 6

    view.cue_list.selectRow(1)
    _act(view._cue_menu(1), "Khóa timing").trigger()
    assert view.cues[1].locked
    assert _act(view._cue_menu(1), "Khóa timing").isChecked()


def test_seen_is_enabled_on_a_pending_line(view):
    from desktop import cuemodel

    view.cue_list.set_issues([["Quá dài"] if i in (1, 4) else [] for i in range(6)])
    view.cue_list.selectRow(1)
    a = _act(view._cue_menu(1), "Đã xem")
    assert a.isEnabled()
    a.trigger()
    assert cuemodel.is_reviewed(view.cues[1]) and view._sel == 4


def test_right_click_on_a_block_selects_it_and_never_drags(view, qapp):
    tl = view.timeline
    got = []
    tl.contextRequested.connect(lambda i, p: got.append(i))
    r = tl._block_rect(view.cues[3])
    pos = QPointF(r.center().x(), r.center().y())
    before = [(c.start, c.end) for c in view.cues]
    ev = QMouseEvent(QEvent.MouseButtonPress, pos, tl.mapToGlobal(pos), Qt.RightButton,
                     Qt.RightButton, Qt.NoModifier)
    tl.mousePressEvent(ev)
    assert got == [3] and view._sel == 3 and _menus_open(view) == 1
    assert tl._drag is None and not tl._scrub
    assert [(c.start, c.end) for c in view.cues] == before
    # empty area with the right button: nothing at all (used to start a scrub)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(3, 3), tl.mapToGlobal(QPointF(3, 3)),
                     Qt.RightButton, Qt.RightButton, Qt.NoModifier)
    tl.mousePressEvent(ev)
    assert not tl._scrub and got == [3]


def test_list_row_context_selects_the_row_and_emits(view, qapp):
    got = []
    view.cue_list.contextRequested.connect(lambda r, p: got.append(r))
    ix = view.cue_list.model.index(4)
    pos = view.cue_list.view.visualRect(ix).center()
    view.cue_list.view.scrollTo(ix)
    pos = view.cue_list.view.visualRect(ix).center()
    view.cue_list._on_context(pos)
    assert got == [4] and view.cue_list.currentRow() == 4 and _menus_open(view) == 1
