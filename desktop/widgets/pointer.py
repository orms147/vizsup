"""Pointer feedback in one place: which cursor a widget shows, and the soft glow that
follows the mouse over cards and primary buttons.

* ``CursorPolicy`` — an application event filter. When a widget is first polished it
  gets a pointing hand if it is clickable (buttons, combos, check boxes, tabs, sliders,
  menu items); text fields keep Qt's I-beam; disabled widgets show the arrow. A cursor
  set explicitly in code (timeline edges, the video caption box, ``saved``) wins.
* ``glow(widget)`` — a radial spotlight painted on that widget's own background, under
  its children, while the mouse is over it. Only the hovered widget repaints (a small
  rect around the old and new mouse point); nothing runs while the mouse is elsewhere.
  ``set_glow_enabled(False)`` turns every glow off (ui_state key ``hover_glow``).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QMenu,
    QSlider,
    QTabBar,
    QWidget,
)

from desktop.theme import V2

_HAND = (QAbstractButton, QComboBox, QTabBar, QSlider)
_AUTO = "vzAutoCursor"          # property: this widget's cursor came from the policy
_glow_on = True


def is_clickable(w: QWidget) -> bool:
    """A widget the user clicks (not types into): it gets the pointing hand."""
    if isinstance(w, _HAND):
        return True
    # the item list of a combo box popup (its viewport inherits the cursor)
    par = w.parentWidget()
    return (isinstance(w, QAbstractItemView) and par is not None
            and par.inherits("QComboBoxPrivateContainer"))


def cursor_for(w: QWidget) -> Qt.CursorShape:
    return Qt.PointingHandCursor if w.isEnabled() else Qt.ArrowCursor


class CursorPolicy(QObject):
    """App-wide: pointing hand on clickable widgets, arrow when they are disabled."""

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        try:
            self._apply(obj, ev)
        except RuntimeError:   # a Qt-internal widget replaced while being polished
            pass
        return False

    @staticmethod
    def _apply(obj, ev) -> None:
        t = ev.type()
        if t == QEvent.Polish:
            if (isinstance(obj, QWidget) and not obj.testAttribute(Qt.WA_SetCursor)
                    and is_clickable(obj)):
                obj.setProperty(_AUTO, True)
                obj.setCursor(cursor_for(obj))
        elif t == QEvent.EnabledChange:
            if isinstance(obj, QWidget) and obj.property(_AUTO):
                obj.setCursor(cursor_for(obj))
        elif t == QEvent.MouseMove and isinstance(obj, QMenu):
            # a hand over real items only — not separators or greyed-out actions
            act = obj.actionAt(ev.position().toPoint())
            on = act is not None and act.isEnabled() and not act.isSeparator()
            obj.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)


def install(app: QApplication, *, glow_enabled: bool = True) -> CursorPolicy:
    """Call once, before the first window is built (widgets polished earlier keep
    their cursor)."""
    set_glow_enabled(glow_enabled)
    policy = CursorPolicy(app)
    app.installEventFilter(policy)
    return policy


# --- hover glow -------------------------------------------------------------------

def set_glow_enabled(on: bool) -> None:
    global _glow_on
    _glow_on = bool(on)


def glow_enabled() -> bool:
    return _glow_on


_KINDS = {  # color, peak alpha, radius px
    "card": (V2["purple"], 0.11, 190),
    "button": ("#ffffff", 0.20, 70),
}


class HoverGlow(QObject):
    """Paints a radial gradient at the mouse on ``target`` (see module doc)."""

    def __init__(self, target: QWidget, *, kind: str = "card", corner: float = 10) -> None:
        super().__init__(target)                 # dies with its widget
        color, alpha, self.radius = _KINDS[kind]
        self._c0 = QColor(color)
        self._c0.setAlphaF(alpha)
        self._c1 = QColor(color)
        self._c1.setAlphaF(0.0)
        self.corner = corner
        self.pos: QPointF | None = None
        target.setAttribute(Qt.WA_Hover, True)   # HoverMove even over child widgets
        target.installEventFilter(self)

    def _dirty(self, w: QWidget, p: QPointF | None) -> None:
        if p is not None:
            r = self.radius
            w.update(QRect(int(p.x() - r), int(p.y() - r), 2 * r + 2, 2 * r + 2))

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        t = ev.type()
        if t in (QEvent.HoverEnter, QEvent.HoverMove):
            if not (_glow_on and obj.isEnabled()):
                return False
            old, self.pos = self.pos, ev.position()
            self._dirty(obj, old)
            self._dirty(obj, self.pos)
        elif t == QEvent.HoverLeave:
            old, self.pos = self.pos, None
            self._dirty(obj, old)
        elif t == QEvent.Paint and self.pos is not None and _glow_on and obj.isEnabled():
            type(obj).paintEvent(obj, ev)        # the widget's own look first …
            self._paint(obj)                     # … then the light; children paint on top
            return True
        return False

    def _paint(self, w: QWidget) -> None:
        p = QPainter(w)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(w.rect()).adjusted(1, 1, -1, -1), self.corner, self.corner)
        p.setClipPath(path)
        g = QRadialGradient(self.pos, self.radius)
        g.setColorAt(0.0, self._c0)
        g.setColorAt(1.0, self._c1)
        r = self.radius
        p.fillRect(QRectF(self.pos.x() - r, self.pos.y() - r, 2 * r, 2 * r), g)
        p.end()


def glow(w: QWidget, kind: str = "card", *, corner: float = 10) -> HoverGlow:
    """Give ``w`` the hover spotlight. ``kind``: "card" (soft purple, wide) or
    "button" (white, tight — for primary buttons). ``corner`` = the widget's radius."""
    existing = w.findChild(HoverGlow, options=Qt.FindDirectChildrenOnly)
    return existing or HoverGlow(w, kind=kind, corner=corner)
