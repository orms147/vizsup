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

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from app.pipeline.glass import look as glass_look

ASS_W, ASS_H = 1920, 1080
EDGE = 9          # px hit-zone around an edge
MIN_W = 120       # min wrap width in ASS px (keeps the box usable)


def glass_shrink(blur: float, frame_h: int) -> int:
    """Down/up-scale factor that roughly matches the render's gblur ``blur`` (sigma
    at 1080 lines) on a ``frame_h``-line frame: 22 at 1080 → 14, like before."""
    return max(1, round(float(blur) * max(1, frame_h) / 1080 * 14 / 22))


@dataclass
class SubBox:
    l: int = 60  # noqa: E741 - margin_l; symmetric with r/v, mirrors the ASS field names
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

    def _wrapped_text_h(self, width_px: float) -> float:
        """Height needed for the caption wrapped to ``width_px`` — so the box grows
        to fit 2+ lines instead of clipping them (anchored at the bottom)."""
        one = self._text_h_px()
        if not self._caption or width_px <= 1:
            return one * 1.35
        f = QFont(self._font)
        f.setPixelSize(max(8, int(one)))
        f.setBold(bool(self._style.get("bold")))
        f.setItalic(bool(self._style.get("italic")))
        fm = QFontMetrics(f)
        br = fm.boundingRect(QRect(0, 0, int(width_px), 100000),
                             int(Qt.TextWordWrap | Qt.AlignHCenter), self._caption)
        return max(one * 1.35, br.height() + one * 0.3)

    def _box_rect(self) -> QRectF:
        d = self._disp
        if d.width() <= 0:
            return QRectF()
        lx = d.x() + d.width() * (self._box.l / ASS_W)
        rx = d.x() + d.width() * (1 - self._box.r / ASS_W)
        by = d.y() + d.height() * (1 - self._box.v / ASS_H)
        th = self._wrapped_text_h(max(8.0, rx - lx))   # grows with the wrapped line count
        return QRectF(lx, by - th, max(8.0, rx - lx), th)

    # --- paint ---------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        self._compute_disp()
        p = QPainter(self)
        # smooth scaling: without it a 1080p frame shrunk into the panel (or 720p stretched)
        # is nearest-neighbour — thin subtitle text looks jagged and "soft" (2026-10-28)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.Antialiasing, True)
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
        if self._style.get("glass"):
            self._draw_glass(p, QFontMetrics(f))
        # approximate box background for the "Hộp …" styles
        elif int(self._style.get("border_style", 1)) in (3, 4):
            alpha = 255 - int(self._style.get("box_alpha", 128))  # store: 0=opaque
            bg = QColor(self._style.get("box", "#000000"))
            bg.setAlpha(max(0, min(255, alpha)))
            full = QRectF(self._disp.x(), box.y() - 4,
                          self._disp.width(), box.height() + 8)
            p.fillRect(full, bg)
        rect = self._box_rect()
        flags = int(Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap)
        o = int(self._style.get("outline_w", 2))
        opacity = 1.0 - max(0, min(255, int(self._style.get("text_alpha") or 0))) / 255.0
        p.save()
        p.setOpacity(opacity)        # "Độ mờ chữ": letters + outline fade together
        if o > 0:   # approximate the character outline so "Màu viền / Độ dày viền" show
            p.setPen(QColor(self._style.get("outline", "#000000")))
            for dxo, dyo in ((-o, 0), (o, 0), (0, -o), (0, o),
                             (-o, -o), (o, -o), (-o, o), (o, o)):
                p.drawText(rect.translated(dxo, dyo), flags, self._caption)
        p.setPen(QColor(self._style.get("primary", "#ffffff")))
        p.drawText(rect, flags, self._caption)
        p.restore()

    def glass_rect(self, fm: QFontMetrics) -> QRectF:
        """The frosted pill around the wrapped caption (widget px) — same padding as
        the render (app.pipeline.glass PAD_X / PAD_Y / RADIUS, × the font size)."""
        box = self._box_rect()
        one = self._text_h_px()
        br = fm.boundingRect(box.toRect(), int(Qt.AlignHCenter | Qt.AlignVCenter
                                               | Qt.TextWordWrap), self._caption)
        return QRectF(br).adjusted(-0.65 * one, -0.22 * one, 0.65 * one, 0.22 * one)

    def _draw_glass(self, p: QPainter, fm: QFontMetrics) -> None:
        """Approximate “Kính mờ trắng”: the frame under the pill blurred (a cheap
        down/up-scale, coarser as "Độ nhòe phía sau" rises) + the "Độ trắng của nền"
        white tint, rounded. The render also covers the Chinese band; "Xem thử kiểu"
        burns the exact frame."""
        pill = self.glass_rect(fm)
        if pill.width() <= 2 or pill.height() <= 2:
            return
        lk = glass_look(self._style)
        one = self._text_h_px()
        path = QPainterPath()
        path.addRoundedRect(pill, min(pill.height() / 2, 0.5 * one), min(pill.height() / 2, 0.5 * one))
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setClipPath(path)
        img, d = self._frame, self._disp
        if img is not None and not img.isNull() and d.width() > 0:
            sx, sy = img.width() / d.width(), img.height() / d.height()
            src = QRect(int((pill.x() - d.x()) * sx), int((pill.y() - d.y()) * sy),
                        max(1, int(pill.width() * sx)), max(1, int(pill.height() * sy)))
            src = src.intersected(img.rect())
            if not src.isEmpty():
                k = glass_shrink(lk["blur"], img.height())
                small = img.copy(src).scaled(max(1, src.width() // k), max(1, src.height() // k),
                                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                blur = small.scaled(src.width(), src.height(), Qt.IgnoreAspectRatio,
                                    Qt.SmoothTransformation)
                p.drawImage(QRectF(d.x() + src.x() / sx, d.y() + src.y() / sy,
                                   src.width() / sx, src.height() / sy), blur)
        p.fillPath(path, QColor(255, 255, 255, round(lk["tint"] * 255)))
        p.restore()

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
