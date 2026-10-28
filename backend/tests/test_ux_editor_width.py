"""The editor must fit the window whatever the project title says (2026-10-29 report: a 1700 px
window, a long Douyin caption → the project bar's title refused to shrink, the whole editor became
wider than the window, the inspector and the timeline header were cut at the right). The title
now elides to the room that is left; the three columns always add up to the splitter's width."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402

LONG = ("Tập 6 — Tôi là một tài xế xe buýt bình thường, bỗng nhiên chở cả xe nữ sinh đại học "
        "xuyên không về một thế giới khác, chuyện gì sẽ xảy ra tiếp theo đây " * 2)
FRAME = 40                       # window frame / scroll bar allowance


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    saved = (app.font(), app.styleSheet(),
             {k: app.property(k) for k in ("vz_sans", "vz_mono", "vz_cjk")})
    load_fonts(app)
    yield app
    app.setFont(saved[0])
    app.setStyleSheet(saved[1])
    for k, val in saved[2].items():
        app.setProperty(k, val)


@pytest.fixture()
def win(qapp, tmp_path, monkeypatch):
    from desktop import main as main_mod
    from desktop.theme import qss

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    qapp.setStyleSheet(qss())             # before the widgets exist, as in the real app
    w = main_mod.MainWindow()
    yield w
    w.close()


def _settle(app, n=6):
    for _ in range(n):
        app.processEvents()


def test_a_long_title_does_not_widen_the_editor(win, qapp):
    from desktop import main as m

    ed = win.editor
    win.resize(1366, 768)
    win.show()
    win.stack.setCurrentIndex(m.EDITOR)
    _settle(qapp)
    short = ed.minimumSizeHint().width()
    ed._meta["title"] = LONG
    ed._update_project()
    _settle(qapp)
    ed.layout().activate()
    assert ed.minimumSizeHint().width() <= 1366 - FRAME, ed.minimumSizeHint().width()
    # the title may never be what decides the editor's width: ~700 px is the bar's own need
    assert ed.project.minimumSizeHint().width() <= 700, ed.project.minimumSizeHint().width()
    assert ed.minimumSizeHint().width() <= max(short, ed.split.minimumSizeHint().width()) + 1


def test_the_title_elides_to_the_room_and_keeps_its_tooltip(win, qapp):
    from desktop import main as m

    win.resize(1366, 768)
    win.show()
    win.stack.setCurrentIndex(m.EDITOR)
    win.editor._meta["title"] = LONG
    win.editor._update_project()
    _settle(qapp)
    bar = win.editor.project
    assert bar.title.text().endswith("…") and bar.title.text() != LONG
    assert bar.title.toolTip() == LONG
    # the project bar's actions are all still on the page
    right = max(bar.actions.itemAt(i).widget().geometry().right()
                for i in range(bar.actions.count()) if bar.actions.itemAt(i).widget())
    assert bar.actions.itemAt(0).widget().mapTo(bar, bar.actions.itemAt(0).widget().rect().topLeft()).x() > 0
    assert bar.mapFromGlobal(bar.mapToGlobal(bar.rect().topRight())).x() >= right


@pytest.mark.parametrize("width,height", [(1366, 768), (1700, 1000), (1130, 680)])
def test_the_page_never_scrolls_sideways_and_the_columns_add_up(win, qapp, width, height):
    from desktop import main as m

    win.resize(width, height)
    win.show()
    win.stack.setCurrentIndex(m.EDITOR)
    win.editor._meta["title"] = LONG
    win.editor._on_dims(1922, 1080)                       # a landscape source, like the report
    _settle(qapp, 10)
    page = win.stack.currentWidget()                       # the scroll area around the editor
    assert page.horizontalScrollBar().maximum() == 0, "the page scrolls sideways"
    ed = win.editor
    assert ed.width() <= page.viewport().width()
    top = ed.top
    assert sum(top.sizes()) + 2 * top.handleWidth() <= top.width()
    assert top.width() <= ed._scroll.viewport().width()
    assert ed._scroll.horizontalScrollBar().maximum() == 0
    # the inspector (last column) ends inside the window
    right_edge = ed.inspector.mapTo(win, ed.inspector.rect().topRight()).x()
    assert right_edge <= win.width()


def test_the_render_page_title_also_gives_way(win, qapp):
    from desktop import main as m

    win.resize(1366, 768)
    win.show()
    win.stack.setCurrentIndex(m.RENDER)
    win.render.set_project(LONG, "Douyin · 01:41")
    _settle(qapp, 8)
    assert win.stack.currentWidget().horizontalScrollBar().maximum() == 0
    assert win.render.project.title.text().endswith("…")
