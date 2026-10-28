"""Video panel (v2): a titled panel holding the VideoCanvas — the real frame with the
Vietnamese subtitle drawn on it as a draggable / resizable box — and a transport
row: play, loop-current-line, time, volume, speed (optional scrub bar).

The frame comes via QVideoSink → QImage so the subtitle can be painted ON it; the
aspect ratio of the source is always kept (720×1566 stays 720×1566, never 9:16).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.audio import follow_default_output
from desktop.cuemodel import fmt_time
from desktop.icons import icon
from desktop.theme import V2
from desktop.widgets.chrome import badge, button, icon_button, icon_label, label
from desktop.widgets.video_canvas import VideoCanvas

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

    HAVE_MM = True
except Exception:  # noqa: BLE001
    HAVE_MM = False

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


class VideoPanel(QFrame):
    positionChanged = Signal(float)          # seconds
    durationChanged = Signal(float)          # seconds
    marginsChanged = Signal(int, int, int)   # subtitle box dragged → margin_l/r/v
    dragCommitted = Signal()                 # drag released → re-burn an exact frame
    loopToggled = Signal(bool)               # "lặp câu đang chọn"
    expandRequested = Signal()               # ⤢ in the panel head
    playStateChanged = Signal(bool)          # playing?
    dimsChanged = Signal(int, int)           # source frame size (w, h)

    def __init__(self, title: str = "Video nguồn", *, scrub: bool = False) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._dur = 0.0
        self._cues: list = []
        self._dims = (0, 0)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # head: title · orientation badge · expand
        head = QFrame()
        head.setObjectName("panelHead")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(14, 9, 10, 9)
        hh.setSpacing(8)
        self.title = label(title, "h3")
        self._orient = badge("")
        self._orient.setVisible(False)
        self._expand = icon_button("Maximize2", "Toàn màn hình (F) — Esc để thoát")
        self._expand.clicked.connect(self.expandRequested)
        hh.addWidget(self.title)
        hh.addStretch(1)
        hh.addWidget(self._orient)
        hh.addWidget(self._expand)
        root.addWidget(head)

        # stage: placeholder | canvas, with a dims badge floating top-left
        stage = QWidget()
        stage.setStyleSheet(f"background:{V2['input']};")
        self._stack = QStackedLayout(stage)
        self._placeholder = QLabel("Chưa có video")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(f"color:{V2['muted']};")
        self._stack.addWidget(self._placeholder)
        self._canvas = VideoCanvas()
        self._canvas.marginsChanged.connect(self.marginsChanged)
        self._canvas.dragCommitted.connect(self.dragCommitted)
        self._stack.addWidget(self._canvas)
        self._dims_lbl = QLabel(stage)
        self._dims_lbl.setStyleSheet(
            f"background:rgba(17,18,24,0.82);color:{V2['secondary']};border-radius:5px;"
            f"padding:3px 7px;font-size:10px;")
        self._dims_lbl.move(12, 12)
        self._dims_lbl.setVisible(False)
        root.addWidget(stage, 1)

        if HAVE_MM:
            self._player = QMediaPlayer()
            self._audio = QAudioOutput()
            self._player.setAudioOutput(self._audio)
            follow_default_output(self._audio)
            self._sink = QVideoSink()
            self._player.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._on_frame)
            self._player.positionChanged.connect(self._on_pos)
            self._player.durationChanged.connect(self._on_dur)
            self._player.errorOccurred.connect(self._on_media_error)
            self._player.playbackStateChanged.connect(self._on_state)
            self._player.mediaStatusChanged.connect(self._on_status)
        else:
            self._player = None
            self._audio = None

        # transport
        bar = QFrame()
        bar.setObjectName("panelHead")
        bar.setStyleSheet(f"QFrame#panelHead{{border-bottom:none;border-top:1px solid "
                          f"{V2['line_soft']};}}")
        tl = QVBoxLayout(bar)
        tl.setContentsMargins(12, 8, 12, 8)
        tl.setSpacing(6)
        self._scrub = QSlider(Qt.Horizontal)
        self._scrub.setRange(0, 0)
        self._scrub.sliderMoved.connect(self._on_scrub)
        self._scrub.setVisible(scrub)
        tl.addWidget(self._scrub)
        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        self._play = button("", "Play", variant="primary", tip="Phát / dừng (Space)")
        self._play.setFixedSize(31, 31)
        self._play.setIconSize(QSize(15, 15))
        self._play.setStyleSheet("QPushButton#primary{padding:0;min-height:31px;}")
        self._play.clicked.connect(self.toggle)
        self._loop = icon_button("Repeat2", "Lặp câu đang chọn (Ctrl+Space)", checkable=True)
        self._loop.toggled.connect(self.loopToggled)
        self._time = label("00:00.00 / 00:00", "monoText")
        ctl.addWidget(self._play)
        ctl.addWidget(self._loop)
        ctl.addWidget(self._time)
        ctl.addStretch(1)
        ctl.addWidget(icon_label("Volume2", V2["secondary"], 16))
        self._vol = QSlider(Qt.Horizontal)
        self._vol.setFixedWidth(96)
        self._vol.setRange(0, 100)
        self._vol.setValue(100)
        self._vol.setToolTip("Âm lượng xem trước")
        self._vol.valueChanged.connect(self._on_volume)
        ctl.addWidget(self._vol)
        self._speed = QComboBox()
        for s in SPEEDS:
            self._speed.addItem(f"{s:g}×", s)
        self._speed.setCurrentIndex(SPEEDS.index(1.0))
        self._speed.setFixedWidth(64)
        self._speed.setToolTip("Tốc độ phát")
        self._speed.currentIndexChanged.connect(self._on_speed)
        ctl.addWidget(self._speed)
        tl.addLayout(ctl)
        root.addWidget(bar)

    # --- public API ----------------------------------------------------------
    def load(self, path: Path | None) -> None:
        self._placeholder.setText("Chưa có video")
        dims = self._probe(path) if path and Path(path).exists() else None
        ok = bool(HAVE_MM and self._player and dims is not None)
        if dims and dims[0] > 0:
            self.set_dims(*dims)
        store.debug(f"VideoPanel.load path={path} ok={ok}")
        if ok:
            from PySide6.QtCore import QUrl

            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._stack.setCurrentWidget(self._canvas)
        else:
            if path and Path(path).exists():
                self._placeholder.setText("Không mở được video (tệp hỏng hoặc tải dở).")
            self._stack.setCurrentWidget(self._placeholder)

    def unload(self) -> None:
        """Stop and drop the file — Windows keeps a playing file locked, so a file shown
        here could not be moved to the Recycle Bin until the player lets go."""
        if self._player:
            self._player.stop()
            from PySide6.QtCore import QUrl

            self._player.setSource(QUrl())
        self._stack.setCurrentWidget(self._placeholder)

    def set_viewer_mode(self) -> None:
        """Plain watching (the library's player): no editor-only controls."""
        self._expand.setVisible(False)
        self._loop.setVisible(False)

    def set_dims(self, w: int, h: int) -> None:
        """Source size → the floating "720 × 1566" badge and the orientation chip."""
        self._dims = (w, h)
        ok = w > 0 and h > 0
        self._dims_lbl.setVisible(ok)
        self._orient.setVisible(ok)
        if ok:
            self._dims_lbl.setText(f"● {w} × {h}")
            self._dims_lbl.adjustSize()
            self._orient.setText("Dọc" if h > w else "Ngang")      # short: the panel can be ~300 px
            self._orient.setToolTip("Video dọc, giữ đúng tỉ lệ gốc" if h > w
                                    else "Video ngang, giữ đúng tỉ lệ gốc")
            self.dimsChanged.emit(w, h)

    def set_style(self, style: dict, size_px: int, font: str) -> None:
        self._canvas.set_style(style, size_px, font)

    def set_burned_overlay(self, path: str) -> None:
        self._canvas.set_burned_overlay(QImage(str(path)))

    def set_watermark(self, wm: dict | None) -> None:
        """Show the translator's watermark on the preview as it will be burned."""
        self._canvas.set_watermark(wm)

    def set_fullscreen_icon(self, on: bool) -> None:
        from desktop.icons import icon as _icon

        self._expand.setIcon(_icon("Minimize2" if on else "Maximize2", V2["secondary"], 16,
                                   active=V2["purple_bright"]))
        self._expand.setToolTip("Thoát toàn màn hình (Esc)" if on else
                                "Toàn màn hình (F) — Esc để thoát")

    def current_time(self) -> float:
        return (self._player.position() / 1000.0) if self._player else 0.0

    def duration(self) -> float:
        return self._dur

    def is_playing(self) -> bool:
        return bool(self._player and
                    self._player.playbackState() == QMediaPlayer.PlayingState)

    def play(self) -> None:
        if self._player:
            self._player.play()

    def pause(self) -> None:
        if self._player and self.is_playing():
            self._player.pause()

    def set_cues(self, cues: list) -> None:
        self._cues = cues or []
        self._refresh_caption(self.current_time())

    def toggle(self) -> None:
        if not self._player:
            return
        if self.is_playing():
            self._player.pause()
        else:
            self._player.play()

    def seek_seconds(self, s: float) -> None:
        if self._player:
            self._player.setPosition(int(max(0.0, s) * 1000))
        self._refresh_caption(max(0.0, s))

    def loop_enabled(self) -> bool:
        return self._loop.isChecked()

    def set_loop(self, on: bool) -> None:
        self._loop.setChecked(on)

    # --- internal ------------------------------------------------------------
    @staticmethod
    def _probe(path: Path) -> tuple[int, int] | None:
        """Validate the file with ffprobe before handing it to QMediaPlayer; returns
        the video size ((0, 0) for audio-only) or None when the file is unusable."""
        import json
        import subprocess

        try:
            from app.ffmpegutil import ffprobe_bin

            r = subprocess.run(
                [ffprobe_bin(), "-v", "error", "-show_entries",
                 "format=duration:stream=width,height,codec_type", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(r.stdout or "{}")
            if r.returncode != 0 or data.get("format", {}).get("duration") in (None, "N/A"):
                return None
            v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            return int(v.get("width") or 0), int(v.get("height") or 0)
        except Exception:  # noqa: BLE001
            return None

    def _on_frame(self, frame) -> None:
        try:
            if not frame.isValid():
                return
            img = frame.toImage()
        except Exception:  # noqa: BLE001
            return
        if img.isNull():
            return
        nw, nh = img.width(), img.height()
        if (nw, nh) != self._dims:
            self.set_dims(nw, nh)
        if max(nw, nh) > 960:
            img = img.scaled(960, 960, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._canvas.set_frame(img, nw, nh)

    def _on_status(self, status) -> None:
        """A freshly loaded video shows its first frame, not a black "…" until Play:
        pausing a stopped player makes the backend decode and deliver frame 0."""
        if (status == QMediaPlayer.MediaStatus.LoadedMedia
                and self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState):
            self._player.pause()

    def _on_media_error(self, _error, msg: str = "") -> None:
        self._placeholder.setText("Không phát được video (tệp hỏng).")
        self._stack.setCurrentWidget(self._placeholder)

    def _on_volume(self, v: int) -> None:
        if self._audio is not None:
            self._audio.setVolume(v / 100.0)

    def _on_speed(self, i: int) -> None:
        if self._player:
            self._player.setPlaybackRate(float(self._speed.itemData(i)))

    def _on_state(self, _state) -> None:
        playing = self.is_playing()
        self._play.setIcon(icon("Pause" if playing else "Play", "#ffffff", 15))
        self.playStateChanged.emit(playing)

    def _active_text(self, t: float) -> str:
        for c in self._cues:
            if c.start <= t < c.end:
                return c.vi
        return ""

    def _refresh_caption(self, t: float) -> None:
        self._canvas.set_caption(self._active_text(t))

    def _on_pos(self, ms: int) -> None:
        if not self._scrub.isSliderDown():
            self._scrub.setValue(ms)
        t = ms / 1000.0
        self._time.setText(f"{fmt_time(t, 2)} / {fmt_time(self._dur, 0)}")
        self._refresh_caption(t)
        self.positionChanged.emit(t)

    def _on_dur(self, ms: int) -> None:
        self._dur = ms / 1000.0
        self._scrub.setRange(0, ms)
        self._time.setText(f"{fmt_time(self.current_time(), 2)} / {fmt_time(self._dur, 0)}")
        self.durationChanged.emit(self._dur)

    def _on_scrub(self, ms: int) -> None:
        if self._player:
            self._player.setPosition(ms)
