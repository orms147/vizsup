"""Thư viện keyboard (UX audit L2): ↑/↓ move a cursor row, Enter opens it, Space watches it,
Delete asks to trash it (the existing confirm + guard), Ctrl+A ticks every visible row, Ctrl+F
focuses the search; Space / Delete / Ctrl+A stay text editing inside the search box, and
↓ + Enter work from it. Storage is a tmp dir; the Recycle Bin is mocked."""
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

from test_library import _wait, make_job  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    from app.config import settings

    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


@pytest.fixture()
def view(qapp, storage):
    from desktop.views.library_view import LibraryView

    make_job(storage, "aaaaaaaaaaaa", title="Mèo con", output=True)
    make_job(storage, "bbbbbbbbbbbb", title="Chó lớn", output=True)
    make_job(storage, "cccccccccccc", title="Cá vàng", output=True)
    v = LibraryView()
    v.resize(1100, 800)
    v.show()
    v.refresh()
    _wait(lambda: len(v._details) == 3)
    v.activateWindow()
    qapp.processEvents()
    yield v
    v.shutdown()
    v.close()


def test_arrows_move_the_cursor_and_enter_opens(view):
    ids = view.visible_ids()
    opened = []
    view.openRequested.connect(opened.append)
    QTest.keyClick(view, Qt.Key_Down)
    assert view.cursor_id() == ids[0]
    QTest.keyClick(view, Qt.Key_Down)
    QTest.keyClick(view, Qt.Key_Down)
    QTest.keyClick(view, Qt.Key_Down)                    # stops at the last row
    assert view.cursor_id() == ids[2]
    QTest.keyClick(view, Qt.Key_Up)
    assert view.cursor_id() == ids[1]
    assert view._rows[ids[1]].property("cur") is True and not view._rows[ids[0]].property("cur")
    QTest.keyClick(view, Qt.Key_Return)
    assert opened == [ids[1]]


def test_space_watches_and_ctrl_a_ticks_everything(view, monkeypatch):
    watched = []
    monkeypatch.setattr(view, "_watch", watched.append)
    view.move_cursor(+1)
    QTest.keyClick(view, Qt.Key_Space)
    assert watched == [view.cursor_id()]
    QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
    assert {it["id"] for it in view.selected()} == set(view.visible_ids())


def test_delete_goes_through_the_confirm_for_the_cursor_row(view, monkeypatch):
    asked = []
    monkeypatch.setattr(view, "delete_items", lambda items, **kw: asked.append(
        [it["id"] for it in items]) or True)
    view.move_cursor(+1)
    QTest.keyClick(view, Qt.Key_Delete)
    assert asked == [[view.cursor_id()]]
    view._rows["cccccccccccc"].check.setChecked(True)      # ticked rows win over the cursor
    QTest.keyClick(view, Qt.Key_Delete)
    assert asked[-1] == ["cccccccccccc"]


def test_search_box_keeps_its_own_keys_but_down_enter_work(view, monkeypatch):
    opened = []
    view.openRequested.connect(opened.append)
    view.search.setFocus()
    QTest.keyClicks(view.search, "meo")
    assert view.visible_ids() == ["aaaaaaaaaaaa"]
    QTest.keyClick(view.search, Qt.Key_Space)             # a space in the text, not "watch"
    assert view.search.text() == "meo "
    QTest.keyClick(view.search, Qt.Key_A, Qt.ControlModifier)   # select all text, not rows
    assert not view.selected()
    QTest.keyClick(view.search, Qt.Key_Delete)            # deletes the selected text only
    assert view.search.text() == ""
    view.search.setText("meo")
    QTest.keyClick(view.search, Qt.Key_Return)            # Enter from the box opens the first hit
    assert opened == ["aaaaaaaaaaaa"]


def test_ctrl_f_focuses_the_search(view, qapp):
    view.setFocus()
    QTest.keyClick(view, Qt.Key_F, Qt.ControlModifier)
    assert view.search.hasFocus()
