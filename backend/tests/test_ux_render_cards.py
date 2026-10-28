"""Render page audio cards (UX audit R1): with the app stylesheet (every QPushButton has
min-height 36) the cards were 36 px tall while title + description need ~45, so the two lines
were squeezed onto each other. Each card now grows to its text."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    saved = (app.font(), {k: app.property(k) for k in ("vz_sans", "vz_mono", "vz_cjk")})
    load_fonts(app)
    yield app
    app.setFont(saved[0])
    for k, val in saved[1].items():
        app.setProperty(k, val)


def _view(qapp, width):
    from desktop.theme import qss
    from desktop.views.render_view import RenderView

    v = RenderView()
    v.setStyleSheet(qss())
    v.resize(width, 900)
    v.show()
    for _ in range(4):
        qapp.processEvents()
    return v


@pytest.mark.parametrize("width", [1000, 1366, 1920])
def test_mode_cards_are_tall_enough_for_their_two_lines(qapp, width):
    v = _view(qapp, width)
    cards = list(v.mode_cards.values())
    assert len(cards) == 4
    for c in cards:
        for lab in (c._title, c._desc):
            assert lab.height() >= lab.heightForWidth(lab.width()), (c._title.text(), lab.text())
        need = c.layout().totalHeightForWidth(c.width())
        assert c.height() >= need, (c._title.text(), c.height(), need)
    v.close()


def test_card_follows_a_longer_description(qapp):
    v = _view(qapp, 1000)
    c = next(iter(v.mode_cards.values()))
    before = c.height()
    c.set_desc("Một mô tả rất dài " * 12)               # wraps onto several lines
    for _ in range(4):
        qapp.processEvents()
    assert c.height() > before
    assert c._desc.height() >= c._desc.heightForWidth(c._desc.width())
    v.close()
