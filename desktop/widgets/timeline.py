"""Timeline (v2): ruler + REAL waveform + draggable cue blocks + playhead, inside a
TimelinePanel with the design's head (visible range · Khớp câu · zoom).

Click empty space to seek; click a block to select; drag a block's body to move it
and its edges to retime. Locked cues select but never move. Ctrl+wheel zooms around
the cursor. The waveform comes from app.pipeline.waveform (RMS, 50 peaks/s).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSlider, QVBoxLayout, QWidget

from desktop.cuemodel import EditorCue, fmt_time
from desktop.theme import V2, fonts
from desktop.widgets.chrome import button, icon_button, label

RULER_H = 20
WAVE_H = 58
BLOCK_H = 38
TOTAL_H = RULER_H + WAVE_H + BLOCK_H + 6
EDGE = 6                  # px edge grab zone
MIN_PPS, MAX_PPS = 8.0, 400.0
MIN_LEN = 0.2


def _c(hex_str: str, alpha: int | None = None) -> QColor:
    q = QColor(hex_str)
    if alpha is not None:
        q.setAlpha(alpha)
    return q


class Timeline(QWidget):
    seeked = Signal(float)
    blockSelected = Signal(int)
    cueChanged = Signal(int)   # a cue's start/end was dragged (fires per mouse-move)
    dragStarted = Signal()     # a drag gesture BEGAN — one undo snapshot per drag
    dragFinished = Signal(int)
    zoomChanged = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._cues: list[EditorCue] = []
        self._issues: list[list[str]] = []
        self._peaks: list[float] = []
        self._speech: list[tuple[float, float]] = []   # VAD spans (green band)
        self._notes: list[tuple[float, str]] = []      # F-4 commentary (time, mode) flags
        self._per_sec = 50
        self._video_dur = 0.0
        self._dur = 1.0
        self._t = 0.0
        self._pps = 40.0
        self._sel = -1
        self._drag = None   # (idx, mode, startX, s0, e0)
        self.setMinimumHeight(TOTAL_H)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

    # --- API -----------------------------------------------------------------
    def set_cues(self, cues: list[EditorCue]) -> None:
        self._cues = cues
        self._update_dur()

    def set_issues(self, issues: list[list[str]]) -> None:
        self._issues = issues or []
        self.update()

    def set_peaks(self, peaks: list[float], per_sec: int = 50) -> None:
        self._peaks, self._per_sec = peaks or [], per_sec
        self.update()

    def set_speech(self, spans: list[tuple[float, float]] | None) -> None:
        """Where the voice is (app.pipeline.vad) — drawn under the waveform."""
        self._speech = list(spans or [])
        self.update()

    def set_notes(self, notes: list[tuple[float, str]]) -> None:
        """The user's commentary notes (F-4): an amber flag on the ruler + a line through
        the lanes — solid for "pause" (the video stops there), dashed for "over"."""
        self._notes = list(notes or [])
        self.update()

    def set_duration(self, seconds: float) -> None:
        self._video_dur = max(0.0, seconds)
        self._update_dur()

    def set_time(self, t: float) -> None:
        self._t = t
        self.update()

    def set_selected(self, idx: int) -> None:
        self._sel = idx
        self.update()

    def playhead_x(self) -> int:
        return int(self._t * self._pps)

    def pps(self) -> float:
        return self._pps

    def set_zoom(self, pps: float) -> None:
        pps = max(MIN_PPS, min(MAX_PPS, pps))
        if abs(pps - self._pps) > 1e-6:
            self._pps = pps
            self._relayout()
            self.zoomChanged.emit(pps)

    def zoom_in(self) -> None:
        self.set_zoom(self._pps * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._pps / 1.25)

    def x_of(self, t: float) -> float:
        return t * self._pps

    def _update_dur(self) -> None:
        self._dur = max(self._video_dur, max((c.end for c in self._cues), default=1.0), 1.0)
        self._relayout()

    def _relayout(self) -> None:
        self.setMinimumWidth(int(self._dur * self._pps) + 24)
        self.updateGeometry()
        self.update()

    # --- paint ---------------------------------------------------------------
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = ev.rect()
        p.fillRect(clip, _c(V2["panel"]))
        self._paint_ruler(p, clip)
        self._paint_wave(p, clip)
        self._paint_speech(p, clip)
        self._paint_blocks(p, clip)
        self._paint_notes(p, clip)
        self._paint_playhead(p)

    def _paint_ruler(self, p: QPainter, clip) -> None:
        step = 1.0
        while step * self._pps < 62:
            step = {1.0: 2.0, 2.0: 5.0, 5.0: 10.0, 10.0: 15.0, 15.0: 30.0, 30.0: 60.0}.get(
                step, step * 2)
        f = QFont(fonts()[1])
        f.setPixelSize(9)
        p.setFont(f)
        t = (int(clip.left() / self._pps / step)) * step
        end_t = clip.right() / self._pps + step
        while t <= min(end_t, self._dur + step):
            x = t * self._pps
            p.setPen(QPen(_c(V2["line"]), 1))
            p.drawLine(QPointF(x, RULER_H - 5), QPointF(x, RULER_H))
            p.setPen(_c(V2["muted"]))
            p.drawText(QPointF(x + 3, 12), fmt_time(t, 0))
            t += step
        p.setPen(QPen(_c(V2["line_soft"]), 1))
        p.drawLine(QPointF(clip.left(), RULER_H), QPointF(clip.right(), RULER_H))

    def _paint_wave(self, p: QPainter, clip) -> None:
        y0 = RULER_H + 2
        mid = y0 + WAVE_H / 2
        half = WAVE_H * 0.46
        played_x = self._t * self._pps
        bw = 2.0
        step = 3.0                                 # px per bar
        x = max(0.0, (int(clip.left() / step)) * step)
        p.setPen(Qt.NoPen)
        while x < clip.right() + step:
            t = x / self._pps
            v = self._peak_at(t, step / self._pps)
            if v is None:
                break
            h = max(1.0, v * half)
            p.setBrush(_c("#8d8ea3") if x <= played_x else _c("#4b4c5c"))
            p.drawRoundedRect(QRectF(x, mid - h, bw, h * 2), 1, 1)
            x += step

    def _paint_speech(self, p: QPainter, clip) -> None:
        if not self._speech:
            return
        y = RULER_H + WAVE_H - 2
        p.setPen(Qt.NoPen)
        p.setBrush(_c(V2["green"], 150))
        for a, b in self._speech:
            x0, x1 = a * self._pps, b * self._pps
            if x1 < clip.left() or x0 > clip.right():
                continue
            p.drawRoundedRect(QRectF(x0, y, max(2.0, x1 - x0), 3), 1.5, 1.5)

    def _paint_notes(self, p: QPainter, clip) -> None:
        col = _c(V2["amber"])
        for at, mode in self._notes:
            x = at * self._pps
            if x < clip.left() - 12 or x > clip.right() + 12:
                continue
            pen = QPen(col, 1.5)
            if mode != "pause":
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(x, RULER_H - 6), QPointF(x, TOTAL_H - 4))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(x, 2), QPointF(x + 10, 6.5), QPointF(x, 11)]))

    def _peak_at(self, t: float, span: float) -> float | None:
        if not self._peaks:
            return 0.06 if t <= self._dur else None
        i0 = int(t * self._per_sec)
        if i0 >= len(self._peaks):
            return None
        i1 = max(i0 + 1, int((t + span) * self._per_sec))
        return max(self._peaks[i0:i1])

    def _block_rect(self, c: EditorCue) -> QRectF:
        y0 = RULER_H + WAVE_H + 4
        bx = c.start * self._pps
        bw = max(6.0, (c.end - c.start) * self._pps)
        return QRectF(bx, y0, bw, BLOCK_H - 4)

    def _paint_blocks(self, p: QPainter, clip) -> None:
        sans = fonts()[0]
        f = QFont(sans)
        f.setPixelSize(10)
        fm = QFontMetrics(f)
        p.setFont(f)
        for idx, c in enumerate(self._cues):
            r = self._block_rect(c)
            if r.right() < clip.left() - 2 or r.left() > clip.right() + 2:
                continue
            selected = idx == self._sel
            flagged = idx < len(self._issues) and bool(self._issues[idx])
            if selected:
                p.setBrush(_c(V2["purple"], 225))
                p.setPen(QPen(_c(V2["purple_bright"]), 1))
            else:
                p.setBrush(_c(V2["purple_soft"]))
                p.setPen(QPen(_c(V2["amber"]) if flagged else _c(V2["purple_line"]), 1))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            if c.locked:
                p.setPen(QPen(_c(V2["muted"]), 1, Qt.DashLine))
                p.drawLine(QPointF(r.left() + 3, r.bottom() - 3), QPointF(r.right() - 3, r.bottom() - 3))
            if r.width() > 14:
                txt = fm.elidedText(f"{idx + 1} {c.vi}", Qt.ElideRight, int(r.width() - 10))
                p.setPen(_c("#ffffff") if selected else _c(V2["text"]))
                p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, txt)

    def _paint_playhead(self, p: QPainter) -> None:
        x = self._t * self._pps
        p.setPen(QPen(_c(V2["purple_bright"]), 1.4))
        p.drawLine(QPointF(x, 4), QPointF(x, self.height()))
        p.setBrush(_c(V2["purple_bright"]))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x + 5, 5),
                                 QPointF(x, 9), QPointF(x - 5, 5)]))

    # --- interaction ---------------------------------------------------------
    def _hit(self, x: float, y: float):
        y0 = RULER_H + WAVE_H
        if y < y0:
            return None
        for idx, c in enumerate(self._cues):
            r = self._block_rect(c)
            if r.left() - EDGE <= x <= r.right() + EDGE:
                if x <= r.left() + EDGE:
                    return (idx, "l")
                if x >= r.right() - EDGE:
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
            if not c.locked:              # locked timing: select only, never drag
                self._drag = (idx, mode, x, c.start, c.end)
                if mode == "move":        # the open hand closes while the cue is held
                    self.setCursor(Qt.ClosedHandCursor)
                self.dragStarted.emit()   # before any mutation — snapshot the pre-drag state
            self.update()
        else:
            self.seeked.emit(max(0.0, x / self._pps))

    def mouseMoveEvent(self, ev) -> None:
        if not self._drag:
            hit = self._hit(ev.position().x(), ev.position().y())
            shape = (Qt.SizeHorCursor if hit and hit[1] in ("l", "r") and
                     not self._cues[hit[0]].locked else
                     Qt.OpenHandCursor if hit and not self._cues[hit[0]].locked else
                     Qt.ArrowCursor)
            self.setCursor(shape)
            return
        idx, mode, x0, s0, e0 = self._drag
        dt = (ev.position().x() - x0) / self._pps
        c = self._cues[idx]
        hi = self._video_dur if self._video_dur > 0 else None
        if mode == "move":
            ns = max(0.0, s0 + dt)
            if hi is not None:
                ns = min(ns, hi - (e0 - s0))
            c.end = round(e0 + (ns - s0), 3)
            c.start = round(ns, 3)
        elif mode == "l":
            c.start = round(max(0.0, min(e0 - MIN_LEN, s0 + dt)), 3)
        else:
            ne = max(s0 + MIN_LEN, e0 + dt)
            c.end = round(min(ne, hi) if hi is not None else ne, 3)
        self.cueChanged.emit(idx)
        self.update()

    def mouseReleaseEvent(self, _ev) -> None:
        if self._drag:
            idx = self._drag[0]
            self._drag = None
            if self.cursor().shape() == Qt.ClosedHandCursor:
                self.setCursor(Qt.OpenHandCursor)    # still over the cue it let go of
            self._update_dur()
            self.dragFinished.emit(idx)

    def wheelEvent(self, ev) -> None:
        if ev.modifiers() & Qt.ControlModifier:
            factor = 1.2 if ev.angleDelta().y() > 0 else 1 / 1.2
            self.set_zoom(self._pps * factor)
            ev.accept()
        else:
            ev.ignore()


class TimelinePanel(QFrame):
    """Panel head (Dòng thời gian · visible range · Khớp câu · zoom) over a scrolling
    Timeline. Keeps the playhead in view while playing ("follow")."""

    fitRequested = Signal()
    snapAllRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QFrame()
        head.setObjectName("panelHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(14, 6, 10, 6)
        h.setSpacing(8)
        h.addWidget(label("Dòng thời gian", "h3"))
        self.range_lbl = label("", "mono")
        h.addWidget(self.range_lbl)
        h.addStretch(1)
        self.fit_btn = button("Khớp câu", "Focus", variant="ghost", small=True,
                              tip="Phóng vừa câu đang chọn")
        self.fit_btn.clicked.connect(self.fitRequested)
        h.addWidget(self.fit_btn)
        self.snap_all_btn = button("Tự canh tất cả", "Sparkles", variant="ghost", small=True,
                                   tip="Canh đầu / cuối mọi câu theo giọng nói (Shift+A) — "
                                       "vạch xanh dưới sóng âm là chỗ có giọng")
        self.snap_all_btn.setEnabled(False)
        self.snap_all_btn.clicked.connect(self.snapAllRequested)
        h.addWidget(self.snap_all_btn)
        zo = icon_button("Minimize2", "Thu nhỏ (Ctrl + cuộn)")
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(0, 100)
        self.zoom.setFixedWidth(84)
        self.zoom.setToolTip("Thu phóng dòng thời gian")
        zi = icon_button("Maximize2", "Phóng to (Ctrl + cuộn)")
        h.addWidget(zo)
        h.addWidget(self.zoom)
        h.addWidget(zi)
        v.addWidget(head)

        self.timeline = Timeline()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.timeline)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setMinimumHeight(TOTAL_H + 12)
        v.addWidget(self.scroll, 1)

        zo.clicked.connect(self.timeline.zoom_out)
        zi.clicked.connect(self.timeline.zoom_in)
        self.zoom.valueChanged.connect(self._slider_zoom)
        self.timeline.zoomChanged.connect(self._sync_slider)
        self.scroll.horizontalScrollBar().valueChanged.connect(self._update_range)
        self._sync_slider(self.timeline.pps())

    # zoom slider is logarithmic over [MIN_PPS, MAX_PPS]
    def _slider_zoom(self, v: int) -> None:
        import math

        pps = MIN_PPS * math.exp(v / 100 * math.log(MAX_PPS / MIN_PPS))
        anchor = self.timeline._t
        self.timeline.set_zoom(pps)
        self.ensure_visible(anchor, center=True)

    def _sync_slider(self, pps: float) -> None:
        import math

        self.zoom.blockSignals(True)
        self.zoom.setValue(int(round(100 * math.log(pps / MIN_PPS) / math.log(MAX_PPS / MIN_PPS))))
        self.zoom.blockSignals(False)
        self._update_range()

    def _update_range(self, *_a) -> None:
        sb = self.scroll.horizontalScrollBar()
        pps = self.timeline.pps()
        a = sb.value() / pps
        b = (sb.value() + self.scroll.viewport().width()) / pps
        self.range_lbl.setText(f"{fmt_time(a, 0)} — {fmt_time(b, 0)}")

    def ensure_visible(self, t: float, *, center: bool = False) -> None:
        sb = self.scroll.horizontalScrollBar()
        x = self.timeline.x_of(t)
        w = self.scroll.viewport().width()
        if center or x < sb.value() + 20 or x > sb.value() + w - 40:
            sb.setValue(int(max(0, x - (w / 2 if center else 40))))
        self._update_range()

    def fit_span(self, start: float, end: float) -> None:
        """Zoom so [start, end] (plus some context) fills the view."""
        w = max(200, self.scroll.viewport().width())
        span = max(1.0, (end - start) * 1.6)
        self.timeline.set_zoom(w / span)
        self.scroll.horizontalScrollBar().setValue(
            int(max(0, self.timeline.x_of(start) - (w - (end - start) * self.timeline.pps()) / 2)))
        self._update_range()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._update_range()
