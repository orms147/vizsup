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
from desktop.widgets import safe_zones

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
        self._disp = QRectF()                  # the composition (the frame the render makes)
        self._pic = QRectF()                   # the source picture inside it
        self._zone: str | None = None          # platform whose UI strips are drawn (safe_zones)
        self._comp_aspect: float | None = None  # 9/16 when the render frames a clip as TikTok
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
    @staticmethod
    def _fit(w: float, h: float, ar: float) -> QRectF:
        """The largest ``ar`` (width / height) rectangle centred in w × h."""
        if h * ar <= w:
            dh, dw = h, h * ar
        else:
            dw, dh = w, w / ar
        return QRectF((w - dw) / 2, (h - dh) / 2, dw, dh)

    def _compute_disp(self) -> None:
        w, h = self.width(), self.height()
        src = (self._nw / self._nh) if self._nh else 0.5625
        # the composition is the source frame, or the 9:16 canvas the render builds for
        # "Dọc 9:16" (the picture then sits inside it, bars around)
        self._disp = self._fit(w, h, self._comp_aspect or src)
        d = self._disp
        if self._comp_aspect and abs(self._comp_aspect - src) > 1e-3:
            inner = self._fit(d.width(), d.height(), src)
            self._pic = inner.translated(d.x(), d.y())
        else:
            self._pic = QRectF(d)

    # --- platform safe zones ---------------------------------------------------
    def set_safe_zone(self, key: str | None) -> None:
        """Draw the UI strips of platform ``key`` (safe_zones.SAFE_ZONES) over the picture,
        or nothing for None / ""."""
        self._zone = key if key in safe_zones.SAFE_ZONES else None
        self.update()

    def safe_zone(self) -> str | None:
        return self._zone

    def set_frame_aspect(self, ratio: float | None) -> None:
        """The aspect of the frame the render composes (9/16 for "Dọc 9:16"), None = the
        source's own."""
        self._comp_aspect = ratio if ratio and ratio > 0 else None
        self.update()

    def safe_zone_hint(self) -> str:
        """Why the subtitle box is in a covered strip ("" when it is clear or no zone)."""
        if not self._zone or not self._show_box:
            return ""
        self._compute_disp()
        d, b = self._disp, self._box_rect()
        if d.width() <= 0 or b.width() <= 0:
            return ""
        return safe_zones.hint_for(self._zone, ((b.left() - d.x()) / d.width(),
                                                (b.top() - d.y()) / d.height(),
                                                (b.right() - d.x()) / d.width(),
                                                (b.bottom() - d.y()) / d.height()))

    def _draw_safe_zone(self, p: QPainter) -> None:
        if not self._zone or self._disp.width() <= 0:
            return
        d = self._disp
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        fill, edge = QColor(239, 68, 68, 58), QColor(248, 113, 113, 150)
        for z in safe_zones.zones(self._zone):
            r = QRectF(d.x() + z.x0 * d.width(), d.y() + z.y0 * d.height(),
                       (z.x1 - z.x0) * d.width(), (z.y1 - z.y0) * d.height())
            p.fillRect(r, fill)
            p.setPen(QPen(edge, 1, Qt.DashLine))
            if z.name == "top":
                p.drawLine(r.bottomLeft(), r.bottomRight())
            elif z.name == "bottom":
                p.drawLine(r.topLeft(), r.topRight())
            else:
                p.drawLine(r.topLeft(), r.bottomLeft())
        hint = self.safe_zone_hint()
        if hint:
            f = QFont(self._font)
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            fm = QFontMetrics(f)
            wrap = Qt.TextWordWrap | Qt.AlignCenter
            inner = max(40, int(d.width()) - 28)
            br = fm.boundingRect(QRect(0, 0, inner, 400), int(wrap), hint)    # wraps on a narrow picture
            pill = QRectF(d.x() + (d.width() - br.width() - 16) / 2, d.y() + 6, br.width() + 16,
                          br.height() + 8)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(17, 18, 24, 225))
            p.drawRoundedRect(pill, 7, 7)
            p.setPen(QColor("#fbbf24"))
            p.drawText(pill.adjusted(8, 4, -8, -4), int(wrap), hint)
        p.restore()

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
        d = self._pic
        if self._burned is not None and not self._burned.isNull():
            p.drawImage(d, self._burned)
        elif self._frame is not None and not self._frame.isNull():
            p.drawImage(d, self._frame)
            self._draw_caption(p)
        else:
            p.setPen(QColor("#666"))
            p.drawText(self.rect(), Qt.AlignCenter, "…")
        self._draw_watermark(p)
        self._draw_safe_zone(p)
        self._draw_box(p)
        p.end()

    def set_watermark(self, wm: dict | None) -> None:
        self._wm = wm
        self.update()

    def _draw_watermark(self, p: QPainter) -> None:
        """The translator's mark, placed like the render (1920×1080 layout scaled to
        the picture): a corner, top corners below the 5-second notes; "move" shows the
        first corner."""
        wm = getattr(self, "_wm", None)
        if not wm or self._frame is None:
            return
        d = self._disp
        sx, sy = d.width() / 1920.0, d.height() / 1080.0
        f = QFont(self._font)
        f.setBold(True)
        f.setPixelSize(max(6, int(int(wm.get("size", 14)) * 3 * sy)))
        p.save()
        p.setFont(f)
        p.setOpacity(max(5, min(100, int(wm.get("opacity", 35)))) / 100.0)
        pos = wm.get("pos") or "tr"
        pos = "tr" if pos == "move" else pos
        fm = QFontMetrics(f)
        text = str(wm.get("text") or "")
        w, h = fm.horizontalAdvance(text), fm.height()
        x = d.x() + (1880 * sx - w if pos in ("tr", "br") else 40 * sx)
        y = d.y() + (170 * sy if pos in ("tr", "tl") else 1040 * sy - h)
        p.setPen(QColor("#000000"))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(QRectF(x + dx, y + dy, w + 2, h), int(Qt.AlignLeft | Qt.AlignTop), text)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(x, y, w + 2, h), int(Qt.AlignLeft | Qt.AlignTop), text)
        p.restore()

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
        img, d = self._frame, self._pic
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
