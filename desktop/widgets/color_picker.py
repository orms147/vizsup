"""A colour popup for "Kiểu chữ": a hue/saturation wheel (drag around it), a brightness
bar, a hex field, quick swatches and the colours used lately. Changes apply live while
dragging (``colorChanged``); Esc puts the starting colour back.

The wheel is drawn with gradients (conical hue + radial white, darkened for the
brightness), so it repaints instantly at any size."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import V2

SWATCHES = ("#FFFFFF", "#FFD966", "#FFE600", "#FF9F1C", "#FF4D4D", "#FF6FB5",
            "#7CF29C", "#4DD0E1", "#5B9BFF", "#B388FF", "#1C1C1E", "#000000")
RECENT_MAX = 8


def recent_colors() -> list[str]:
    from desktop import store

    got = store.load_ui_state().get("recent_colors") or []
    return [c for c in got if isinstance(c, str) and QColor(c).isValid()][:RECENT_MAX]


def remember_color(hex_str: str) -> None:
    from desktop import store

    c = QColor(hex_str).name().upper()
    rest = [x for x in recent_colors() if x.upper() != c]
    store.update_ui_state(recent_colors=[c, *rest][:RECENT_MAX])


class ColorWheel(QWidget):
    """Angle = hue, distance from the centre = saturation, at a fixed brightness."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(180, 180)
        self.setCursor(Qt.CrossCursor)
        self._h, self._s, self._v = 0.0, 0.0, 1.0

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, 200)

    def set_hsv(self, h: float, s: float, v: float) -> None:
        self._h, self._s, self._v = h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v))
        self.update()

    def hsv(self) -> tuple[float, float, float]:
        return self._h, self._s, self._v

    def _geom(self) -> tuple[QPointF, float]:
        r = min(self.width(), self.height()) / 2 - 6
        return QPointF(self.width() / 2, self.height() / 2), max(10.0, r)

    def paintEvent(self, _e) -> None:  # noqa: N802
        c, r = self._geom()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        disc = QPainterPath()
        disc.addEllipse(c, r, r)
        hue = QConicalGradient(c, 0)
        for k in range(13):                       # Qt's conical angle runs anticlockwise
            hue.setColorAt(k / 12, QColor.fromHsvF((k / 12) % 1.0, 1.0, 1.0))
        p.fillPath(disc, hue)
        white = QRadialGradient(c, r)
        white.setColorAt(0.0, QColor(255, 255, 255, 255))
        white.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(disc, white)
        if self._v < 1.0:
            p.fillPath(disc, QColor(0, 0, 0, int(round((1 - self._v) * 255))))
        p.setPen(QPen(QColor(V2["line"]), 1))
        p.drawEllipse(c, r, r)
        ang = self._h * 2 * math.pi
        m = QPointF(c.x() + math.cos(ang) * self._s * r, c.y() - math.sin(ang) * self._s * r)
        p.setBrush(QColor.fromHsvF(self._h, self._s, self._v))
        p.setPen(QPen(QColor("#000000"), 3))
        p.drawEllipse(m, 7, 7)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawEllipse(m, 7, 7)
        p.end()

    def _pick(self, pos: QPointF) -> None:
        c, r = self._geom()
        dx, dy = pos.x() - c.x(), c.y() - pos.y()
        self._h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self._s = min(1.0, math.hypot(dx, dy) / r)
        self.update()
        self.changed.emit()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._pick(e.position())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if e.buttons() & Qt.LeftButton:
            self._pick(e.position())


class ValueBar(QWidget):
    """Brightness: black → the wheel's colour at full brightness."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(18)
        self.setCursor(Qt.PointingHandCursor)
        self._h = self._s = 0.0
        self._v = 1.0

    def set_hsv(self, h: float, s: float, v: float) -> None:
        self._h, self._s, self._v = h, s, max(0.0, min(1.0, v))
        self.update()

    def value(self) -> float:
        return self._v

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        g = QLinearGradient(rect.topLeft(), rect.topRight())
        g.setColorAt(0, QColor("#000000"))
        g.setColorAt(1, QColor.fromHsvF(self._h, self._s, 1.0))
        p.setPen(QPen(QColor(V2["line"]), 1))
        p.setBrush(g)
        p.drawRoundedRect(rect, 6, 6)
        x = rect.left() + self._v * rect.width()
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x - 4, 1, 8, self.height() - 2), 3, 3)
        p.end()

    def _pick(self, x: float) -> None:
        w = max(1.0, self.width() - 2)
        self._v = max(0.0, min(1.0, (x - 1) / w))
        self.update()
        self.changed.emit()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._pick(e.position().x())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if e.buttons() & Qt.LeftButton:
            self._pick(e.position().x())


def _swatch(hex_str: str, size: int = 22) -> QPushButton:
    b = QPushButton()
    b.setFixedSize(size, size)
    b.setToolTip(hex_str.upper())
    b.setStyleSheet(f"QPushButton{{background:{hex_str};border:1px solid {V2['line']};"
                    f"border-radius:{size // 2}px;padding:0;min-height:0;}}"
                    f"QPushButton:hover{{border:2px solid {V2['purple_bright']};}}")
    return b


class ColorPopup(QFrame):
    """The popup itself. ``colorChanged(hex)`` on every change; ``finished(hex)`` when
    it closes (the colour kept — or the start colour after Esc)."""

    colorChanged = Signal(str)
    finished = Signal(str)

    def __init__(self, hex_str: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self.setObjectName("panel")
        self.setStyleSheet(f"QFrame#panel{{background:{V2['panel']};border:1px solid "
                           f"{V2['line']};border-radius:10px;}}")
        self._start = QColor(hex_str).name()
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)
        self.wheel = ColorWheel()
        v.addWidget(self.wheel, 0, Qt.AlignHCenter)
        self.value = ValueBar()
        v.addWidget(self.value)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.preview = QLabel()
        self.preview.setFixedSize(34, 26)
        row.addWidget(self.preview)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setPlaceholderText("#RRGGBB")
        self.hex.editingFinished.connect(self._from_hex)
        self.hex.textEdited.connect(lambda t: len(t.lstrip("#")) == 6 and self._from_hex())
        row.addWidget(self.hex, 1)
        v.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(6)
        for k, c in enumerate(SWATCHES):
            b = _swatch(c)
            b.clicked.connect(lambda _=False, c=c: self.set_color(c, emit=True))
            grid.addWidget(b, k // 6, k % 6)
        v.addLayout(grid)
        recent = recent_colors()
        if recent:
            v.addWidget(QLabel("Dùng gần đây"))
            rr = QHBoxLayout()
            rr.setSpacing(6)
            for c in recent:
                b = _swatch(c, 20)
                b.clicked.connect(lambda _=False, c=c: self.set_color(c, emit=True))
                rr.addWidget(b)
            rr.addStretch(1)
            v.addLayout(rr)
        self.wheel.changed.connect(self._from_wheel)
        self.value.changed.connect(self._from_wheel)
        self._cancelled = False
        self.set_color(self._start)

    def color(self) -> str:
        h, s, _v = self.wheel.hsv()
        return QColor.fromHsvF(h, s, self.value.value()).name().upper()

    def set_color(self, hex_str: str, *, emit: bool = False) -> None:
        c = QColor(hex_str)
        if not c.isValid():
            return
        h, s, v, _a = c.getHsvF()
        h = self.wheel.hsv()[0] if h < 0 else h      # grey: keep the hue where it was
        self.wheel.set_hsv(h, s, v)
        self.value.set_hsv(h, s, v)
        self._show(c.name().upper())
        if emit:
            self.colorChanged.emit(c.name().upper())

    def _show(self, hex_str: str) -> None:
        self.preview.setStyleSheet(f"background:{hex_str};border:1px solid {V2['line']};"
                                   "border-radius:5px;")
        if not self.hex.hasFocus():
            self.hex.setText(hex_str)

    def _from_wheel(self) -> None:
        h, s, _ = self.wheel.hsv()
        self.value.set_hsv(h, s, self.value.value())
        self.wheel.set_hsv(h, s, self.value.value())
        c = self.color()
        self._show(c)
        self.colorChanged.emit(c)

    def _from_hex(self) -> None:
        t = self.hex.text().strip()
        if not t.startswith("#"):
            t = "#" + t
        if QColor(t).isValid() and len(t) == 7:
            self.set_color(t, emit=True)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape:
            self._cancelled = True
            self.colorChanged.emit(self._start.upper())
            self.close()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._from_hex()
            self.close()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e) -> None:  # noqa: N802
        final = self._start.upper() if self._cancelled else self.color()
        if not self._cancelled and final != self._start.upper():
            remember_color(final)
        self.finished.emit(final)
        super().closeEvent(e)
