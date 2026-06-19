"""Custom-painted timeline: ruler + synthetic waveform + draggable cue blocks +
playhead. Click to seek, click a block to select, drag block body to move and
edges to retime. Too-long lines show a red overflow tail past the block end.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from desktop.cuemodel import EditorCue, fmt_time
from desktop.theme import C

RULER_H = 22
WAVE_H = 54
BLOCK_H = 64
TOTAL_H = RULER_H + WAVE_H + BLOCK_H
EDGE = 6  # px edge grab zone


def _col(hex_or_rgba: str) -> QColor:
    return QColor(hex_or_rgba)


class Timeline(QWidget):
    seeked = Signal(float)
    blockSelected = Signal(int)
    cueChanged = Signal(int)  # a cue's start/end was dragged

    def __init__(self) -> None:
        super().__init__()
        self._cues: list[EditorCue] = []
        self._dur = 1.0
        self._t = 0.0
        self._pps = 74.0
        self._sel = -1
        self._drag = None  # (idx, mode, startX, s0, e0)
        self.setMinimumHeight(TOTAL_H)
        self.setMouseTracking(True)

    # --- API -----------------------------------------------------------------
    def set_cues(self, cues: list[EditorCue]) -> None:
        self._cues = cues
        self._dur = max((c.end for c in cues), default=1.0)
        self._relayout()

    def set_time(self, t: float) -> None:
        self._t = t
        self.update()

    def set_selected(self, idx: int) -> None:
        self._sel = idx
        self.update()

    def playhead_x(self) -> int:
        return int(self._t * self._pps)

    def zoom_in(self) -> None:
        self._pps = min(300.0, self._pps * 1.25)
        self._relayout()

    def zoom_out(self) -> None:
        self._pps = max(20.0, self._pps / 1.25)
        self._relayout()

    def _relayout(self) -> None:
        self.setMinimumWidth(int(self._dur * self._pps) + 40)
        self.updateGeometry()
        self.update()

    # --- paint ---------------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W = self.width()
        p.fillRect(self.rect(), _col(C["panel"]))

        self._paint_ruler(p, W)
        self._paint_wave(p, W)
        self._paint_blocks(p)
        self._paint_playhead(p)

    def _paint_ruler(self, p: QPainter, W: int) -> None:
        p.fillRect(0, 0, W, RULER_H, _col(C["panel"]))
        p.setPen(QPen(_col(C["line"]), 1))
        p.drawLine(0, RULER_H, W, RULER_H)
        # tick every ~1s scaled so labels don't crowd
        step = 1.0
        while step * self._pps < 54:
            step *= 2
        p.setFont(QFont("JetBrains Mono", 7))
        p.setPen(_col(C["muted4"]))
        t = 0.0
        while t <= self._dur + step:
            x = int(t * self._pps)
            p.drawLine(x, RULER_H - 5, x, RULER_H)
            p.drawText(x + 4, 13, fmt_time(t))
            t += step

    def _paint_wave(self, p: QPainter, W: int) -> None:
        y0 = RULER_H
        p.fillRect(0, y0, W, WAVE_H, _col(C["deep"]))
        mid = y0 + WAVE_H / 2
        bw, gap = 2, 1
        played_x = self._t * self._pps
        x = 0
        i = 0
        while x < W:
            env = 0.55 + 0.45 * math.sin(i * 0.06 + 1)
            v = (math.sin(i * 0.5) * 0.5 + 0.5) * 0.6 + (math.sin(i * 1.7) * 0.5 + 0.5) * 0.4
            noise = ((i * 9301 + 49297) % 233280) / 233280
            h = max(0.07, min(1.0, (v * 0.7 + noise * 0.3) * env)) * (WAVE_H * 0.46)
            color = _col(C["accent_lt"]) if x <= played_x else QColor(60, 66, 78)
            p.fillRect(QRectF(x, mid - h, bw, h * 2), color)
            x += bw + gap
            i += 1

    def _paint_blocks(self, p: QPainter) -> None:
        y0 = RULER_H + WAVE_H
        p.fillRect(0, y0, self.width(), BLOCK_H, _col("#070809"))
        fm = QFontMetrics(QFont("Be Vietnam Pro", 8))
        for idx, c in enumerate(self._cues):
            bx = c.start * self._pps
            bw = max(8.0, c.dur * self._pps)
            rect = QRectF(bx, y0 + 6, bw, BLOCK_H - 12)
            selected = idx == self._sel
            base = QColor(139, 92, 246, 60 if not selected else 110)
            p.setBrush(base)
            p.setPen(QPen(_col(C["accent"]) if selected else QColor(139, 92, 246, 120), 1.5 if selected else 1))
            p.drawRoundedRect(rect, 6, 6)
            # overflow tail
            if c.too_long:
                over = min((c.est_speak - c.dur) * self._pps, 60.0)
                if over > 1:
                    p.setBrush(QColor(240, 89, 79, 150))
                    p.setPen(Qt.NoPen)
                    p.drawRoundedRect(QRectF(bx + bw, y0 + 6, over, BLOCK_H - 12), 4, 4)
            # number + text
            p.setPen(_col(C["text"]) if selected else _col(C["text3"]))
            p.setFont(QFont("JetBrains Mono", 7, QFont.Bold))
            p.drawText(QRectF(bx + 6, y0 + 8, bw - 8, 12), Qt.AlignLeft, str(idx + 1))
            p.setFont(QFont("Be Vietnam Pro", 8))
            txt = fm.elidedText(c.vi, Qt.ElideRight, int(bw - 10))
            p.drawText(QRectF(bx + 6, y0 + 22, bw - 10, BLOCK_H - 30), Qt.TextWordWrap, txt)

    def _paint_playhead(self, p: QPainter) -> None:
        x = self._t * self._pps
        p.setPen(QPen(_col(C["amber"]), 1))
        p.drawLine(int(x), 0, int(x), TOTAL_H)
        p.setBrush(_col(C["amber"]))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 9)]))

    # --- interaction ---------------------------------------------------------
    def _hit(self, x: float, y: float):
        y0 = RULER_H + WAVE_H
        if y < y0:
            return None
        for idx, c in enumerate(self._cues):
            bx = c.start * self._pps
            bw = max(8.0, c.dur * self._pps)
            if bx - EDGE <= x <= bx + bw + EDGE:
                if x <= bx + EDGE:
                    return (idx, "l")
                if x >= bx + bw - EDGE:
                    return (idx, "r")
                return (idx, "move")
        return None

    def mousePressEvent(self, ev) -> None:
        x, y = ev.position().x(), ev.position().y()
        hit = self._hit(x, y)
        if hit:
            idx, mode = hit
            self._sel = idx
            self.blockSelected.emit(idx)
            c = self._cues[idx]
            self._drag = (idx, mode, x, c.start, c.end)
            self.update()
        else:
            t = max(0.0, x / self._pps)
            self.seeked.emit(t)

    def mouseMoveEvent(self, ev) -> None:
        if not self._drag:
            return
        idx, mode, x0, s0, e0 = self._drag
        dt = (ev.position().x() - x0) / self._pps
        c = self._cues[idx]
        if mode == "move":
            ns = max(0.0, s0 + dt)
            c.end = e0 + (ns - s0)
            c.start = ns
        elif mode == "l":
            c.start = max(0.0, min(e0 - 0.2, s0 + dt))
        else:
            c.end = max(s0 + 0.2, e0 + dt)
        self._dur = max((cc.end for cc in self._cues), default=1.0)
        self.cueChanged.emit(idx)
        self.update()

    def mouseReleaseEvent(self, _ev) -> None:
        if self._drag:
            self._relayout()
        self._drag = None
