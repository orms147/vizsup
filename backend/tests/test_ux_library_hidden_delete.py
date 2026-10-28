"""Thư viện (review U4): a chip or the search can hide a ticked row. Xóa, Chỉ xóa file nặng, Ghép and
Dựng hàng loạt act on the ticked rows that are SHOWING only, and the label says how many ticked rows
the filter is hiding."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from test_library import _wait, make_job  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from desktop.views.library_view import LibraryView

    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    make_job(base, "aaaaaaaaaaaa", title="Mèo con")                       # chờ duyệt
    make_job(base, "bbbbbbbbbbbb", title="Chó lớn", output=True)          # đã dựng
    make_job(base, "cccccccccccc", title="Cá vàng", output=True)          # đã dựng
    v = LibraryView()
    v.resize(1100, 800)
    v.show()
    v.refresh()
    _wait(lambda: len(v._details) == 3)
    qapp.processEvents()
    yield v
    v.shutdown()
    v.close()


def test_selected_is_only_what_is_showing(view):
    for iid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
        view._rows[iid].check.setChecked(True)
    assert {it["id"] for it in view.selected()} == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    view.set_chip("rendered")                                     # hides the ticked "Mèo con"
    assert [it["id"] for it in view.selected()] == ["bbbbbbbbbbbb"]
    assert view.hidden_checked() == 1
    assert "Đã chọn 1" in view.sel_label.text() and "1 đã tick đang bị bộ lọc ẩn" in view.sel_label.text()
    assert "1 dự án" in view.delete_btn.text() or "Xóa 1" in view.delete_btn.text()
    view.set_chip("all")
    assert {it["id"] for it in view.selected()} == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    assert "bộ lọc" not in view.sel_label.text()


def test_delete_clean_compose_and_batch_get_only_the_visible_rows(view, monkeypatch):
    seen = {}
    monkeypatch.setattr(view, "delete_items", lambda items, heavy_only=False: seen.setdefault(
        "heavy" if heavy_only else "delete", [it["id"] for it in items]) and True)
    monkeypatch.setattr(view, "compose", lambda items: seen.setdefault("compose", [it["id"] for it in items]))
    monkeypatch.setattr(view, "open_batch", lambda items: seen.setdefault("batch", [it["id"] for it in items]))
    for iid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        view._rows[iid].check.setChecked(True)
    view.search.setText("ca vang")                                # only "Cá vàng" shows
    assert view.visible_ids() == ["cccccccccccc"]
    view.delete_btn.click()
    view.clean_btn.click()
    view.compile_btn.click()
    view.batch_btn.click()
    assert seen == {"delete": ["cccccccccccc"], "heavy": ["cccccccccccc"],
                    "compose": ["cccccccccccc"], "batch": ["cccccccccccc"]}


def test_nothing_visible_ticked_disables_the_buttons(view):
    view._rows["aaaaaaaaaaaa"].check.setChecked(True)
    view.set_chip("rendered")
    assert view.selected() == [] and not view.delete_btn.isEnabled()
    assert not view.clean_btn.isEnabled() and not view.compile_btn.isEnabled()
    assert "1 đã tick đang bị bộ lọc ẩn" in view.sel_label.text()


def test_the_delete_key_follows_the_same_rule(view, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    asked = []
    monkeypatch.setattr(view, "delete_items", lambda items, **kw: asked.append([it["id"] for it in items]) or True)
    view._rows["aaaaaaaaaaaa"].check.setChecked(True)
    view.set_chip("rendered")                                     # the ticked row is hidden
    view.setFocus()
    view.move_cursor(+1)                                          # the cursor is on a visible row
    QTest.keyClick(view, Qt.Key_Delete)
    assert asked == [[view.cursor_id()]] and "aaaaaaaaaaaa" not in asked[0]
