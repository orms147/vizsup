"""Screen 3 — Render / Export: voice + audio mix (replace / mix / duck) with
volume control, then render → download. Subtitle styling now lives on Screen 2.

Emits renderRequested(opts). The subtitle style/font/size are injected by
MainWindow from the editor when the render starts.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets.video_panel import VideoPanel


def _section(t: str) -> QLabel:
    lb = QLabel(t.upper())
    lb.setObjectName("section")
    return lb


class RenderView(QWidget):
    renderRequested = Signal(dict)
    backRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._out: str | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QWidget()
        left.setObjectName("rail")
        left.setFixedWidth(380)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(22, 20, 18, 16)
        ll.setSpacing(11)

        back = QPushButton("←  Quay lại sửa phụ đề")
        back.setObjectName("ghost")
        back.clicked.connect(self.backRequested)
        ll.addWidget(back)

        # --- voice ---
        ll.addWidget(_section("Giọng lồng tiếng"))
        self.voice = QComboBox()
        ll.addLayout(self._row("Giọng", self.voice))
        self.speed = QSlider_h(70, 140, 100)
        self.speed_lbl = QLabel("1.00×")
        self.speed_lbl.setObjectName("mono")
        self.speed.valueChanged.connect(lambda v: self.speed_lbl.setText(f"{v/100:.2f}×"))
        ll.addLayout(self._slider_row("Tốc độ nói", self.speed, self.speed_lbl))

        # --- audio mix ---
        ll.addSpacing(6)
        ll.addWidget(_section("Xử lý âm thanh"))
        self.audio_group = QButtonGroup(self)
        self.rb_replace = QRadioButton("  Thay giọng gốc")
        self.rb_mix = QRadioButton("  Trộn cùng giọng gốc")
        self.rb_duck = QRadioButton("  Giảm nền tự động (duck)")
        self.rb_replace.setChecked(True)
        for rb in (self.rb_replace, self.rb_mix, self.rb_duck):
            self.audio_group.addButton(rb)
            ll.addWidget(rb)
        self.rb_replace.toggled.connect(self._sync_audio_enabled)

        self.orig_vol = QSlider_h(0, 150, 100)
        self.orig_vol_lbl = QLabel("100%")
        self.orig_vol_lbl.setObjectName("mono")
        self.orig_vol.valueChanged.connect(lambda v: self.orig_vol_lbl.setText(f"{v}%"))
        ll.addLayout(self._slider_row("Âm lượng gốc", self.orig_vol, self.orig_vol_lbl))

        self.dub_vol = QSlider_h(50, 150, 100)
        self.dub_vol_lbl = QLabel("100%")
        self.dub_vol_lbl.setObjectName("mono")
        self.dub_vol.valueChanged.connect(lambda v: self.dub_vol_lbl.setText(f"{v}%"))
        ll.addLayout(self._slider_row("Âm lượng lồng tiếng", self.dub_vol, self.dub_vol_lbl))
        self._sync_audio_enabled()

        ll.addStretch(1)
        self.render_btn = QPushButton("⧉  Dựng video")
        self.render_btn.setObjectName("primary")
        self.render_btn.setFixedHeight(44)
        self.render_btn.clicked.connect(self._emit_render)
        ll.addWidget(self.render_btn)

        self.status = QLabel("Sẵn sàng dựng")
        self.status.setObjectName("muted")
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.log = QPlainTextEdit()
        self.log.setObjectName("mono")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        self.log.setVisible(False)
        self.open_btn = QPushButton("📂  Mở thư mục chứa video")
        self.open_btn.setObjectName("ghost")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self._open_folder)
        for w in (self.status, self.bar, self.log, self.open_btn):
            ll.addWidget(w)

        # right preview
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 20, 20, 20)
        rl.addWidget(_section("Xem trước"))
        self.preview = VideoPanel()
        rl.addWidget(self.preview, 1)

        root.addWidget(left)
        root.addWidget(right, 1)

    # --- layout helpers ------------------------------------------------------
    def _row(self, label: str, w: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(QLabel(label))
        box.addWidget(w)
        return box

    def _slider_row(self, label: str, slider, value_lbl: QLabel) -> QVBoxLayout:
        head = QHBoxLayout()
        head.addWidget(QLabel(label))
        head.addStretch(1)
        head.addWidget(value_lbl)
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addLayout(head)
        box.addWidget(slider)
        return box

    def _sync_audio_enabled(self) -> None:
        keep = not self.rb_replace.isChecked()
        self.orig_vol.setEnabled(keep)
        self.orig_vol_lbl.setEnabled(keep)

    def _audio_mode(self) -> str:
        if self.rb_mix.isChecked():
            return "mix"
        if self.rb_duck.isChecked():
            return "duck"
        return "replace"

    # --- public API ----------------------------------------------------------
    def set_voices(self, voices: list[str]) -> None:
        self.voice.clear()
        self.voice.addItems(voices or ["(mặc định)"])

    def set_preview(self, video_path: Path, cues: list | None = None) -> None:
        self.preview.load(video_path)
        if cues is not None:
            self.preview.set_cues(cues)

    def _emit_render(self) -> None:
        speed = self.speed.value() / 100.0
        opts = {
            "voice": self.voice.currentText() if self.voice.count() else None,
            "rate": f"{int(round((speed - 1) * 100)):+d}%",
            "audio_mode": self._audio_mode(),
            "orig_volume": self.orig_vol.value() / 100.0,
            "dub_volume": self.dub_vol.value() / 100.0,
        }
        self.bar.setVisible(True)
        self.log.setVisible(True)
        self.render_btn.setEnabled(False)
        self.renderRequested.emit(opts)

    # --- progress API --------------------------------------------------------
    def set_progress(self, frac: float, msg: str) -> None:
        self.bar.setValue(int(frac * 100))
        self.status.setText(msg)

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def show_done(self, path: str) -> None:
        self._out = path
        self.status.setText(f"✓ Hoàn tất: {Path(path).name}")
        self.open_btn.setVisible(True)
        self.render_btn.setEnabled(True)
        self.set_preview(Path(path))

    def show_error(self, msg: str) -> None:
        self.status.setText(f"Lỗi: {msg}")
        self.append_log(f"✕ {msg}")
        self.render_btn.setEnabled(True)

    def _open_folder(self) -> None:
        if self._out and os.path.exists(self._out):
            os.startfile(os.path.dirname(self._out))  # noqa: S606 (Windows)


def QSlider_h(lo: int, hi: int, val: int):
    from PySide6.QtWidgets import QSlider
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    return s
