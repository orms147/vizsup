"""The mouse wheel never changes a setting — it scrolls the page.

Scrolling a control-heavy panel used to change whatever combo / slider / spin box passed
under the pointer (user report 2026-10-27: "lăn chuột qua các ô dropbox, các lựa chọn bị
thay đổi"). One app-wide filter (``install``) swallows wheel events on those controls —
focused or not — and hands them to the nearest scroll area, so the panel scrolls as
expected. An open combo popup is a separate list view and still scrolls normally.
The ``NoWheel*`` classes stay as plain aliases for existing call sites.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QSlider,
)

_GUARDED = (QComboBox, QAbstractSpinBox, QSlider)


def _guarded(obj) -> QObject | None:
    """The combo / spin box / slider that owns ``obj`` (itself or its line edit)."""
    w = obj
    for _ in range(2):
        if isinstance(w, _GUARDED):
            return w
        w = w.parent() if w is not None else None
    return None


class WheelGuard(QObject):
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 - Qt API
        if ev.type() != QEvent.Wheel or _guarded(obj) is None:
            return False
        if ev.modifiers() & Qt.ControlModifier:
            return False                     # Ctrl+wheel stays a deliberate gesture
        w = obj.parent() if hasattr(obj, "parent") else None
        while w is not None and not isinstance(w, QAbstractScrollArea):
            w = w.parent()
        if w is not None:
            QCoreApplication.sendEvent(w.viewport(), ev)   # scroll the page instead
        return True


_GUARD: WheelGuard | None = None


def install(app: QApplication) -> WheelGuard:
    global _GUARD
    if _GUARD is None:
        _GUARD = WheelGuard(app)
        app.installEventFilter(_GUARD)
    return _GUARD


class NoWheelComboBox(QComboBox):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)  # wheel no longer grabs focus


class NoWheelSlider(QSlider):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)
