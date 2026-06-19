"""Screen 3 — Render / Export: subtitle style + voice + audio mix, then render,
then download. Emits renderRequested(opts).
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets.video_panel import VideoPanel

FONTS = ["Be Vietnam Pro", "Noto Sans", "Arial"]


def _section(t: str) -> QLabel:
    lb = QLabel(t.upper())
    lb.setObjectName("section")
    return lb


class RenderView(QWidget):
    renderRequested = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self._out: str | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # left controls
        left = QWidget()
        left.setObjectName("rail")
        left.setFixedWidth(452)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(22, 22, 22, 16)
        ll.setSpacing(12)

        ll.addWidget(_section("Kiểu phụ đề"))
        self.font = QComboBox()
        self.font.addItems(FONTS)
        ll.addWidget(QLabel("Phông chữ"))
        ll.addWidget(self.font)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Cỡ chữ"))
        self.size_lbl = QLabel("18px")
        self.size_lbl.setObjectName("mono")
        srow.addStretch(1)
        srow.addWidget(self.size_lbl)
        ll.addLayout(srow)
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(12, 32)
        self.size.setValue(18)
        self.size.valueChanged.connect(lambda v: self.size_lbl.setText(f"{v}px"))
        ll.addWidget(self.size)
        self.cover = QCheckBox("  Che phụ đề tiếng Trung gốc (khung mờ)")
        self.cover.setChecked(True)
        ll.addWidget(self.cover)

        ll.addSpacing(6)
        ll.addWidget(_section("Giọng lồng tiếng"))
        self.voice = QComboBox()
        ll.addWidget(self.voice)
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("Tốc độ nói"))
        self.speed_lbl = QLabel("1.0×")
        self.speed_lbl.setObjectName("mono")
        vrow.addStretch(1)
        vrow.addWidget(self.speed_lbl)
        ll.addLayout(vrow)
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(70, 140)
        self.speed.setValue(100)
        self.speed.valueChanged.connect(lambda v: self.speed_lbl.setText(f"{v/100:.2f}×"))
        ll.addWidget(self.speed)

        ll.addSpacing(6)
        ll.addWidget(_section("Xử lý âm thanh"))
        self.audio_group = QButtonGroup(self)
        self.rb_replace = QRadioButton("  Thay giọng gốc")
        self.rb_mix = QRadioButton("  Trộn cùng giọng gốc")
        self.rb_duck = QRadioButton("  Giảm nền tự động (sau này)")
        self.rb_replace.setChecked(True)
        self.rb_duck.setEnabled(False)
        for rb in (self.rb_replace, self.rb_mix, self.rb_duck):
            self.audio_group.addButton(rb)
            ll.addWidget(rb)

        ll.addStretch(1)
        self.render_btn = QPushButton("⧉  Dựng video")
        self.render_btn.setObjectName("primary")
        self.render_btn.setFixedHeight(44)
        self.render_btn.clicked.connect(self._emit_render)
        ll.addWidget(self.render_btn)

        # progress + done (compact)
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
        ll.addWidget(self.status)
        ll.addWidget(self.bar)
        ll.addWidget(self.log)
        ll.addWidget(self.open_btn)

        # right preview
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 20, 20, 20)
        cap = QLabel("XEM TRƯỚC")
        cap.setObjectName("section")
        self.preview = VideoPanel()
        rl.addWidget(cap)
        rl.addWidget(self.preview, 1)

        root.addWidget(left)
        root.addWidget(right, 1)

    def set_voices(self, voices: list[str]) -> None:
        self.voice.clear()
        self.voice.addItems(voices or ["(mặc định)"])

    def set_preview(self, video_path: Path) -> None:
        self.preview.load(video_path)

    def _emit_render(self) -> None:
        speed = self.speed.value() / 100.0
        rate = f"{int(round((speed - 1) * 100)):+d}%"
        opts = {
            "voice": self.voice.currentText() if self.voice.count() else None,
            "rate": rate,
            "keep_audio": self.rb_mix.isChecked(),
            "cover_hardsubs": self.cover.isChecked(),
            "font": self.font.currentText(),
            "size": self.size.value(),
        }
        self.bar.setVisible(True)
        self.log.setVisible(True)
        self.render_btn.setEnabled(False)
        self.renderRequested.emit(opts)

    # progress API
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
