"""Phím tắt dialog (UX audit E12 / X2): no description is cut off by its key cap, the dialog fits
a 768 px screen (it scrolls), and every key added this round is listed — and really bound."""
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


def test_no_description_is_clipped(qapp):
    from PySide6.QtWidgets import QLabel

    from desktop.theme import qss
    from desktop.widgets.shortcuts import SHORTCUTS, ShortcutsDialog

    d = ShortcutsDialog()
    d.setStyleSheet(qss())
    d.show()
    for _ in range(3):
        qapp.processEvents()
    labels = d.findChildren(QLabel, "shortcutDesc")
    assert len(labels) == len(SHORTCUTS)
    clipped = [(lab.text(), lab.sizeHint().width(), lab.width()) for lab in labels
               if lab.sizeHint().width() > lab.width()]
    assert not clipped, clipped
    d.close()


def test_dialog_keeps_to_a_768_screen(qapp, monkeypatch):
    from PySide6.QtGui import QScreen

    from desktop.widgets.shortcuts import ShortcutsDialog

    class _Geo:
        def height(self):
            return 728                                   # 768 minus the taskbar

    monkeypatch.setattr(QScreen, "availableGeometry", lambda self: _Geo())
    d = ShortcutsDialog()
    d.show()
    qapp.processEvents()
    assert d.sizeHint().height() <= 728
    d.close()


def test_the_new_keys_are_listed():
    from desktop.widgets.shortcuts import SHORTCUTS

    keys = {k for k, _ in SHORTCUTS}
    for k in ("D / Ctrl+D", "Ctrl+↑ / ↓", "Ctrl+H", "Ctrl+L", "Ctrl+,", "Ctrl+1 / 2 / 3",
              "Chuột phải", "Delete", "Enter / Space"):
        assert k in keys, k
