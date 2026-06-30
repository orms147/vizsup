"""Video player panel. The video is rendered via QVideoSink → QImage into a
VideoCanvas (not a QVideoWidget) so the Vietnamese subtitle can be drawn ON the
frame as a draggable/resizable box. Play/pause, scrub, seek, volume preserved.
A caption bar under the video is kept as a redundant always-visible readout.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.cuemodel import fmt_time
from desktop.theme import C
from desktop.widgets.video_canvas import VideoCanvas

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

    HAVE_MM = True
except Exception:  # noqa: BLE001
    HAVE_MM = False


class VideoPanel(QFrame):
    positionChanged = Signal(float)          # seconds
    durationChanged = Signal(float)          # seconds
    marginsChanged = Signal(int, int, int)   # subtitle box dragged → margin_l/r/v
    dragCommitted = Signal()                 # drag released → re-burn an exact frame

    def __init__(self) -> None:
        super().__init__()
        self._dur = 0.0
        self._cues: list = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        stage = QWidget()
        self._stack = QStackedLayout(stage)
        self._placeholder = QLabel("Chưa có video")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            f"background:{C['deep']};border:1px solid {C['line3']};border-radius:14px;color:{C['muted3']};"
        )
        self._stack.addWidget(self._placeholder)

        self._canvas = VideoCanvas()
        self._canvas.marginsChanged.connect(self.marginsChanged)
        self._canvas.dragCommitted.connect(self.dragCommitted)
        self._stack.addWidget(self._canvas)

        if HAVE_MM:
            self._player = QMediaPlayer()
            self._audio = QAudioOutput()
            self._player.setAudioOutput(self._audio)
            self._sink = QVideoSink()
            self._player.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._on_frame)
            self._player.positionChanged.connect(self._on_pos)
            self._player.durationChanged.connect(self._on_dur)
            self._player.errorOccurred.connect(self._on_media_error)
        else:
            self._player = None
            self._audio = None
        root.addWidget(stage, 1)

        # caption bar (current Vietnamese cue) — redundant always-visible readout
        self._sub = QLabel("")
        self._sub.setWordWrap(True)
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setMinimumHeight(40)
        self._sub.setStyleSheet(
            f"color:#fff; background:{C['deep']}; border:1px solid {C['line2']};"
            "border-radius:8px; padding:6px 12px; font-size:15px; font-weight:600;"
        )
        root.addWidget(self._sub)

        # controls
        ctl = QHBoxLayout()
        ctl.setSpacing(9)
        self._play = QPushButton("▶")
        self._play.setObjectName("ghost")
        self._play.setFixedSize(32, 30)
        self._play.clicked.connect(self.toggle)
        self._time = QLabel("00:00.0 / 00:00.0")
        self._time.setObjectName("mono")
        self._scrub = QSlider(Qt.Horizontal)
        self._scrub.setRange(0, 0)
        self._scrub.sliderMoved.connect(self._on_scrub)
        ctl.addWidget(self._play)
        ctl.addWidget(self._time)
        ctl.addWidget(self._scrub, 1)
        self._vol = QSlider(Qt.Horizontal)
        self._vol.setFixedWidth(84)
        self._vol.setRange(0, 100)
        self._vol.setValue(100)
        self._vol.setToolTip("Âm lượng xem trước")
        self._vol.valueChanged.connect(self._on_volume)
        ctl.addWidget(QLabel("🔊"))
        ctl.addWidget(self._vol)
        root.addLayout(ctl)

    # --- public API ----------------------------------------------------------
    def load(self, path: Path | None) -> None:
        self._placeholder.setText("Chưa có video")
        ok = bool(HAVE_MM and self._player and path and Path(path).exists() and self._probe_ok(path))
        store.debug(f"VideoPanel.load path={path} ok={ok}")
        if ok:
            from PySide6.QtCore import QUrl

            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._stack.setCurrentWidget(self._canvas)
        else:
            if path and Path(path).exists():
                self._placeholder.setText("Không mở được video (tệp hỏng hoặc tải dở).")
            self._stack.setCurrentWidget(self._placeholder)

    def set_style(self, style: dict, size_px: int, font: str) -> None:
        self._canvas.set_style(style, size_px, font)

    def set_burned_overlay(self, path: str) -> None:
        self._canvas.set_burned_overlay(QImage(str(path)))

    def current_time(self) -> float:
        return (self._player.position() / 1000.0) if self._player else 0.0

    @staticmethod
    def _probe_ok(path: Path) -> bool:
        """Validate the file with ffprobe before handing it to QMediaPlayer."""
        import subprocess

        try:
            from app.ffmpegutil import ffprobe_bin

            r = subprocess.run(
                [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            return r.returncode == 0 and r.stdout.strip() not in ("", "N/A")
        except Exception:  # noqa: BLE001
            return False

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
        if max(nw, nh) > 960:
            img = img.scaled(960, 960, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._canvas.set_frame(img, nw, nh)

    def _on_media_error(self, _error, msg: str = "") -> None:
        self._placeholder.setText("Không phát được video (tệp hỏng).")
        self._stack.setCurrentWidget(self._placeholder)

    def _on_volume(self, v: int) -> None:
        if self._audio is not None:
            self._audio.setVolume(v / 100.0)

    def set_cues(self, cues: list) -> None:
        self._cues = cues or []

    def toggle(self) -> None:
        if not self._player:
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            self._play.setText("▶")
        else:
            self._player.play()
            self._play.setText("❚❚")

    def seek_seconds(self, s: float) -> None:
        if self._player:
            self._player.setPosition(int(max(0.0, s) * 1000))
        self._refresh_caption(max(0.0, s))

    # --- internal ------------------------------------------------------------
    def _active_text(self, t: float) -> str:
        for c in self._cues:
            if c.start <= t < c.end:
                return c.vi
        return ""

    def _refresh_caption(self, t: float) -> None:
        txt = self._active_text(t)
        if txt != self._sub.text():
            self._sub.setText(txt)
        self._canvas.set_caption(txt)

    def _on_pos(self, ms: int) -> None:
        if not self._scrub.isSliderDown():
            self._scrub.setValue(ms)
        t = ms / 1000.0
        self._time.setText(f"{fmt_time(t)} / {fmt_time(self._dur)}")
        self._refresh_caption(t)
        self.positionChanged.emit(t)

    def _on_dur(self, ms: int) -> None:
        self._dur = ms / 1000.0
        self._scrub.setRange(0, ms)
        self.durationChanged.emit(self._dur)

    def _on_scrub(self, ms: int) -> None:
        if self._player:
            self._player.setPosition(ms)
