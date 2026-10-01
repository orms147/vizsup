"""Lucide icons (ISC, desktop/assets/icons) rendered for Qt in any color.

The SVGs use ``stroke="currentColor"``; ``icon()`` substitutes a concrete color and
rasterizes at the device pixel ratio so they stay crisp on scaled Windows displays.
Use the same names as the v2 design (``Play``, ``Scissors``, ``ShieldCheck`` …).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_DIR = Path(__file__).parent / "assets" / "icons"


@lru_cache(maxsize=512)
def _svg(name: str) -> str:
    p = _DIR / f"{name}.svg"
    return p.read_text(encoding="utf-8") if p.exists() else ""


@lru_cache(maxsize=1024)
def pixmap(name: str, color: str = "#b4b4c4", size: int = 16, stroke: float = 2.0) -> QPixmap:
    """A ``size``×``size`` (logical px) pixmap of icon ``name`` in ``color``."""
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    svg = _svg(name)
    if not svg:
        return pm
    svg = svg.replace("currentColor", color).replace('stroke-width="2"',
                                                     f'stroke-width="{stroke:g}"')
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name: str, color: str = "#b4b4c4", size: int = 16, *,
         active: str | None = None, disabled: str = "#5d5e6e") -> QIcon:
    """QIcon with Normal / Disabled (and optional Active-hover / checked) colors."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(name, disabled, size), QIcon.Disabled, QIcon.Off)
    if active:
        ic.addPixmap(pixmap(name, active, size), QIcon.Active, QIcon.Off)
        ic.addPixmap(pixmap(name, active, size), QIcon.Normal, QIcon.On)
    return ic


def size(n: int) -> QSize:
    return QSize(n, n)
