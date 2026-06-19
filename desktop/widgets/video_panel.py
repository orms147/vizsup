"""Video player panel: QMediaPlayer + QVideoWidget with scrub + play/pause.
Degrades to a placeholder if QtMultimedia is unavailable or no file is loaded.
Keeps the video's aspect ratio, so portrait (9:16) and landscape (16:9) both fit.
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
            self._stack.addWidget(self._video)
        else:
            self._player = None
        root.addWidget(stage, 1)

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
        if HAVE_MM and path and Path(path).exists():
            from PySide6.QtCore import QUrl

            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._stack.setCurrentWidget(self._video)
        else:
            self._stack.setCurrentWidget(self._placeholder)

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

    # --- internal ------------------------------------------------------------
    def _on_pos(self, ms: int) -> None:
        if not self._scrub.isSliderDown():
            self._scrub.setValue(ms)
        self._time.setText(f"{fmt_time(ms / 1000)} / {fmt_time(self._dur)}")
        self.positionChanged.emit(ms / 1000.0)

    def _on_dur(self, ms: int) -> None:
        self._dur = ms / 1000.0
        self._scrub.setRange(0, ms)
        self.durationChanged.emit(self._dur)

    def _on_scrub(self, ms: int) -> None:
        if self._player:
            self._player.setPosition(ms)
