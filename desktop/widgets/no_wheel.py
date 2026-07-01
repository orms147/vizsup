"""Combo/slider variants that ignore the mouse wheel unless they have focus — so
scrolling a control-heavy panel doesn't silently change settings. Ignoring the
wheel event lets it bubble up to the QScrollArea, which then scrolls as expected.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QSlider


class NoWheelComboBox(QComboBox):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)  # wheel no longer grabs focus

    def wheelEvent(self, e) -> None:
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class NoWheelSlider(QSlider):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e) -> None:
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()
