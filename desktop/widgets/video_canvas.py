"""VideoCanvas — paints the current video frame and draws the Vietnamese subtitle
on top as a draggable / resizable box, so the user positions subtitles directly on
the video (QVideoWidget's native surface hides overlay widgets, so we render frames
ourselves via QVideoSink → QImage and paint here).

The box is described purely by ASS margins over the fixed 1920×1080 canvas:
  left edge  → margin_l        right edge → margin_r   (together = the wrap width)
  vertical   → margin_v        (distance of the text bottom from the frame bottom)
Dragging emits ``marginsChanged(l, r, v)`` live and ``dragCommitted`` on release
(the editor then burns one exact FFmpeg frame for true WYSIWYG).
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

ASS_W, ASS_H = 1920, 1080
EDGE = 9          # px hit-zone around an edge
MIN_W = 120       # min wrap width in ASS px (keeps the box usable)


@dataclass
class SubBox:
    l: int = 60
    r: int = 60
    v: int = 60


class VideoCanvas(QWidget):
    marginsChanged = Signal(int, int, int)   # margin_l, margin_r, margin_v (ASS px)
    dragCommitted = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(180, 300)
        self.setMouseTracking(True)
        self._frame: QImage | None = None
        self._burned: QImage | None = None     # exact FFmpeg overlay (after a drag)
        self._nw, self._nh = ASS_W, ASS_H      # native video size (for letterbox aspect)
        self._caption = ""
        self._style: dict = {}
        self._size_px = 20
        self._font = "Be Vietnam Pro"
        self._box = SubBox()
        self._show_box = False                 # only after set_style (editor uses it; render preview doesn't)
        self._disp = QRectF()
        self._drag: str | None = None
        self._press = QPointF()
        self._press_box = SubBox()

    # --- inputs --------------------------------------------------------------
    def set_frame(self, img: QImage, nw: int, nh: int) -> None:
        self._frame = img
        if nw > 0 and nh > 0:
            self._nw, self._nh = nw, nh
        self._burned = None       # a fresh frame supersedes the burned still
        self.update()

    def set_burned_overlay(self, img: QImage) -> None:
        if img is not None and not img.isNull():
            self._burned = img
            self.update()

    def set_caption(self, text: str) -> None:
        if text != self._caption:
            self._caption = text
            self.update()

    def set_style(self, style: dict, size_px: int, font: str) -> None:
        self._style = style or {}
        self._size_px = int(size_px)
        self._font = font or "Be Vietnam Pro"
        self._show_box = True
        if self._drag is None:                # don't fight an in-progress drag
            self._box = SubBox(int(self._style.get("margin_l", 60)),
                               int(self._style.get("margin_r", 60)),
                               int(self._style.get("margin_v", 60)))
        self.update()

    # --- geometry ------------------------------------------------------------
    def _compute_disp(self) -> None:
        w, h = self.width(), self.height()
        ar = (self._nw / self._nh) if self._nh else 0.5625
        if h * ar <= w:
            dh = h
            dw = h * ar
        else:
            dw = w
            dh = w / ar
        self._disp = QRectF((w - dw) / 2, (h - dh) / 2, dw, dh)

    def _text_h_px(self) -> float:
        """Approximate one-line text height in widget px (ASS size = ui_px*3)."""
        if self._disp.height() <= 0:
            return 20.0
        return self._size_px * 3 * self._disp.height() / ASS_H

    def _box_rect(self) -> QRectF:
        d = self._disp
        if d.width() <= 0:
            return QRectF()
        lx = d.x() + d.width() * (self._box.l / ASS_W)
        rx = d.x() + d.width() * (1 - self._box.r / ASS_W)
        by = d.y() + d.height() * (1 - self._box.v / ASS_H)
        th = max(self._text_h_px() * 1.4, 16.0)
        return QRectF(lx, by - th, max(8.0, rx - lx), th)

    # --- paint ---------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        self._compute_disp()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        d = self._disp
        if self._burned is not None and not self._burned.isNull():
            p.drawImage(d, self._burned)
        elif self._frame is not None and not self._frame.isNull():
            p.drawImage(d, self._frame)
            self._draw_caption(p)
        else:
            p.setPen(QColor("#666"))
            p.drawText(self.rect(), Qt.AlignCenter, "…")
        self._draw_box(p)
        p.end()

    def _draw_caption(self, p: QPainter) -> None:
        if not self._caption:
            return
        box = self._box_rect()
        f = QFont(self._font)
        f.setPixelSize(max(8, int(self._text_h_px())))
        f.setBold(bool(self._style.get("bold")))
        f.setItalic(bool(self._style.get("italic")))
        p.setFont(f)
        # approximate box background for the "Hộp …" styles
        if int(self._style.get("border_style", 1)) in (3, 4):
            alpha = 255 - int(self._style.get("box_alpha", 128))  # store: 0=opaque
            bg = QColor(self._style.get("box", "#000000"))
            bg.setAlpha(max(0, min(255, alpha)))
            full = QRectF(self._disp.x(), box.y() - 4,
                          self._disp.width(), box.height() + 8)
            p.fillRect(full, bg)
        p.setPen(QColor(self._style.get("primary", "#ffffff")))
        flags = int(Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap)
        p.drawText(self._box_rect(), flags, self._caption)

    def _draw_box(self, p: QPainter) -> None:
        if not self._show_box or self._disp.width() <= 0:
            return
        box = self._box_rect()
        pen = QPen(QColor("#a78bfa"), 1.4, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        # edge handles
        p.setBrush(QColor("#a78bfa"))
        p.setPen(Qt.NoPen)
        for cx, cy in (
            (box.left(), box.center().y()),
            (box.right(), box.center().y()),
            (box.center().x(), box.top()),
            (box.center().x(), box.bottom()),
        ):
            p.drawRect(QRectF(cx - 4, cy - 4, 8, 8))

    # --- mouse ---------------------------------------------------------------
    def _hit(self, pos: QPointF) -> str | None:
        if not self._show_box:
            return None
        box = self._box_rect()
        if box.width() <= 0:
            return None
        near_l = abs(pos.x() - box.left()) <= EDGE
        near_r = abs(pos.x() - box.right()) <= EDGE
        in_y = box.top() - EDGE <= pos.y() <= box.bottom() + EDGE
        near_v = (abs(pos.y() - box.top()) <= EDGE or abs(pos.y() - box.bottom()) <= EDGE)
        in_x = box.left() - EDGE <= pos.x() <= box.right() + EDGE
        if near_l and in_y:
            return "left"
        if near_r and in_y:
            return "right"
        if near_v and in_x:
            return "vert"
        if box.adjusted(-EDGE, -EDGE, EDGE, EDGE).contains(pos):
            return "move"
        return None

    def mousePressEvent(self, e) -> None:
        pos = e.position()
        mode = self._hit(pos)
        if mode:
            self._drag = mode
            self._press = pos
            self._press_box = SubBox(self._box.l, self._box.r, self._box.v)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            self.setCursor(self._cursor_for(self._hit(e.position())))
            return
        d = self._disp
        if d.width() <= 0:
            return
        dx = (e.position().x() - self._press.x()) / d.width() * ASS_W
        dy = (e.position().y() - self._press.y()) / d.height() * ASS_H
        pb = self._press_box
        ln, rn, vn = pb.l, pb.r, pb.v
        if self._drag in ("left", "move"):
            ln = pb.l + dx
        if self._drag in ("right", "move"):
            rn = pb.r - dx
        if self._drag in ("vert", "move"):
            vn = pb.v - dy                       # drag down → text lower → smaller bottom margin
        ln = max(0.0, min(ln, ASS_W - MIN_W - max(0.0, rn)))
        rn = max(0.0, min(rn, ASS_W - MIN_W - max(0.0, ln)))
        vn = max(0.0, min(vn, ASS_H - 40))
        self._box = SubBox(round(ln), round(rn), round(vn))
        self.update()
        self.marginsChanged.emit(self._box.l, self._box.r, self._box.v)

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None:
            self._drag = None
            self.dragCommitted.emit()

    @staticmethod
    def _cursor_for(mode: str | None):
        return {
            "left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
            "vert": Qt.SizeVerCursor, "move": Qt.SizeAllCursor,
        }.get(mode, Qt.ArrowCursor)
