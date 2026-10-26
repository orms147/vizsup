"""The mouse wheel scrolls the page and never changes a combo / slider / spin box
(user report 2026-10-27), focused or not."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets import no_wheel  # noqa: E402


def _wheel(widget, dy=-120, mods=Qt.NoModifier):
    ev = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0),
                     QPoint(0, dy), Qt.NoButton, mods, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_wheel_scrolls_the_page_not_the_controls():
    app = QApplication.instance() or QApplication([])
    no_wheel.install(app)
    area = QScrollArea()
    inner = QWidget()
    lay = QVBoxLayout(inner)
    combo, slider, spin = QComboBox(), QSlider(Qt.Horizontal), QSpinBox()
    combo.addItems(["a", "b", "c"])
    slider.setRange(0, 100)
    slider.setValue(50)
    for w in (combo, slider, spin):
        lay.addWidget(w)
    lay.addSpacing(3000)
    area.setWidget(inner)
    area.resize(300, 200)
    area.show()
    combo.setFocus()                                 # even a focused combo stays put
    for w in (combo, slider, spin):
        _wheel(w)
    assert combo.currentIndex() == 0 and slider.value() == 50 and spin.value() == 0
    assert area.verticalScrollBar().value() > 0      # …and the page scrolled instead
