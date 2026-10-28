"""Render page on a DPI-scaled laptop (logical ≈ 1133×667), real-window report 2026-10-29:
 1. the result pane's action row held the long "N câu lồng tiếng vào trễ…" line, so the right pane
    asked for far more than the window had → the page scrolled sideways and the left column's
    footer (Dựng video…) was pushed out of view;
 2. audio cards that were never laid out before the page was shown kept min-height 0 and drew as
    empty boxes with their text squashed into a line.
The action buttons now reflow onto a second row and the status line wraps under them; every card
carries a usable minimum from the moment it is built."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402

LATE = ("64 câu lồng tiếng vào trễ hơn 0,2 s (chậm nhất 3.4 s) — xem cờ “Lồng tiếng trễ” ở màn "
        "Sửa phụ đề, rút gọn các câu đó rồi dựng lại để lời khớp hình hơn nữa.")


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
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _view(qapp):
    from desktop.theme import qss
    from desktop.views.render_view import RenderView

    qapp.setStyleSheet(qss())
    return RenderView()


def test_audio_cards_have_a_usable_minimum_before_the_page_is_ever_shown(qapp, storage):
    rv = _view(qapp)                                  # never shown: no resize event reached them
    assert len(rv.mode_cards) == 4
    for key, card in rv.mode_cards.items():
        assert card._min_h >= 36, (key, card._min_h)
    rv.resize(900, 700)
    rv.show()
    for _ in range(5):
        qapp.processEvents()
    for key, card in rv.mode_cards.items():
        assert card.height() >= card.layout().totalHeightForWidth(card.width()), key
        assert card._title.height() >= card._title.heightForWidth(card._title.width()), key
    rv.close()


@pytest.mark.parametrize("width", [1000, 1133])
def test_a_long_late_line_never_widens_the_result_pane_or_scrolls_the_page(qapp, storage, width):
    rv = _view(qapp)
    rv.res_status.setText(LATE)
    rv._right_stack.setCurrentIndex(2)               # the result pane (an existing output.mp4)
    rv.resize(width, 640)
    rv.show()
    for _ in range(6):
        qapp.processEvents()
    split = rv._split
    assert split.minimumSizeHint().width() <= width - 60, split.minimumSizeHint().width()
    from PySide6.QtWidgets import QScrollArea

    areas = [a for a in rv.findChildren(QScrollArea) if a.widget() is split]
    assert areas and areas[0].horizontalScrollBar().maximum() == 0
    rv.close()


def test_the_result_buttons_all_stay_inside_the_pane_and_the_status_wraps(qapp, storage):
    rv = _view(qapp)
    rv.res_status.setText(LATE)
    rv._right_stack.setCurrentIndex(2)
    rv.resize(1000, 640)
    rv.show()
    for _ in range(6):
        qapp.processEvents()
    pane = rv._right_stack.widget(2)
    for b in (rv.save_btn, rv.open_btn, rv.export_btn, rv.post_btn):
        right = b.mapTo(pane, b.rect().bottomRight()).x()
        assert 0 < right <= pane.width(), (b.text(), right, pane.width())
        assert b.isVisible()
    assert rv.res_status.wordWrap()
    assert rv.res_status.mapTo(pane, rv.res_status.rect().bottomRight()).x() <= pane.width()
    rv.close()


@pytest.mark.parametrize("height", [497, 560])
def test_a_short_window_does_not_push_the_render_buttons_out_of_view(qapp, storage, height):
    """~497 px is what the render page gets on a 1133×667 logical window (150 % DPI): the result
    pane's picture used to insist on 300 px, the page scrolled and the footer fell off the bottom."""
    from PySide6.QtWidgets import QScrollArea

    rv = _view(qapp)
    rv.res_status.setText(LATE)
    rv._right_stack.setCurrentIndex(2)
    rv.resize(1097, height)
    rv.show()
    for _ in range(8):
        qapp.processEvents()
    area = next(a for a in rv.findChildren(QScrollArea) if a.widget() is rv._split)
    assert area.verticalScrollBar().maximum() == 0, "the page scrolls vertically"
    for b in (rv.render_btn, rv.quick_btn, rv.save_btn):
        assert b.mapTo(rv, b.rect().bottomRight()).y() <= rv.height(), b.text()
    rv.close()
