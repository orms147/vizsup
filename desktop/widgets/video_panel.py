"""Video player panel: QMediaPlayer + QVideoWidget with scrub + play/pause, and a
live caption bar UNDER the video showing the Vietnamese cue at the current time
(a bar, not an overlay — QVideoWidget's native surface can hide overlaid widgets,
so a dedicated strip is the reliable way to always see the subtitle).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
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

from desktop.cuemodel import fmt_time
from desktop.theme import C

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    HAVE_MM = True
except Exception:  # noqa: BLE001
    HAVE_MM = False


class VideoPanel(QFrame):
    positionChanged = Signal(float)   # seconds
    durationChanged = Signal(float)   # seconds

    def __init__(self) -> None:
        super().__init__()
        self._dur = 0.0
        self._cues: list = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # stage (video or placeholder)
        stage = QWidget()
        self._stack = QStackedLayout(stage)
        self._placeholder = QLabel("Chưa có video")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            f"background:{C['deep']};border:1px solid {C['line3']};border-radius:14px;color:{C['muted3']};"
        )
        self._stack.addWidget(self._placeholder)

        if HAVE_MM:
            self._video = QVideoWidget()
            self._video.setStyleSheet("background:#000;border-radius:14px;")
            self._player = QMediaPlayer()
            self._audio = QAudioOutput()
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self._video)
            self._player.positionChanged.connect(self._on_pos)
            self._player.durationChanged.connect(self._on_dur)
            self._player.errorOccurred.connect(self._on_media_error)
            self._stack.addWidget(self._video)
        else:
            self._player = None
        root.addWidget(stage, 1)

        # caption bar (current Vietnamese cue) — directly under the video
        self._sub = QLabel("")
        self._sub.setWordWrap(True)
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setMinimumHeight(46)
        self._sub.setStyleSheet(
            f"color:#fff; background:{C['deep']}; border:1px solid {C['line2']};"
            "border-radius:8px; padding:7px 12px; font-size:16px; font-weight:600;"
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
        root.addLayout(ctl)

    # --- public API ----------------------------------------------------------
    def load(self, path: Path | None) -> None:
        self._placeholder.setText("Chưa có video")
        if HAVE_MM and path and Path(path).exists() and self._probe_ok(path):
            from PySide6.QtCore import QUrl

            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._stack.setCurrentWidget(self._video)
        else:
            if path and Path(path).exists():
                self._placeholder.setText("Không mở được video (tệp hỏng hoặc tải dở).")
            self._stack.setCurrentWidget(self._placeholder)

    @staticmethod
    def _probe_ok(path: Path) -> bool:
        """Validate the file with ffprobe before handing it to QMediaPlayer —
        a corrupt/incomplete mp4 can otherwise hard-crash the ffmpeg backend."""
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

    def _on_media_error(self, _error, msg: str = "") -> None:
        self._placeholder.setText("Không phát được video (tệp hỏng).")
        self._stack.setCurrentWidget(self._placeholder)

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
