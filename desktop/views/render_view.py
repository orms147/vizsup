"""Screen 3 — Render / Export: full subtitle styling (font, size, colour, box,
position, bold…) with a 1-frame WYSIWYG preview, voice, and audio mix
(replace / mix / duck) with volume control. Then render → download.

Emits renderRequested(opts) and previewStyleRequested(opts).
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets.video_panel import VideoPanel

FONTS = ["Be Vietnam Pro", "Noto Sans", "Arial"]
# "Kiểu nền" → (border_style, box_alpha)
BOX_STYLES = {
    "Viền chữ (không nền)": (1, 255),
    "Hộp mờ": (4, 110),
    "Hộp đặc (che chữ Trung)": (4, 0),
}


def _section(t: str) -> QLabel:
    lb = QLabel(t.upper())
    lb.setObjectName("section")
    return lb


class _ColorButton(QPushButton):
    """A swatch button that opens a colour picker and remembers the hex."""

    def __init__(self, hex_str: str) -> None:
        super().__init__()
        self.hex = hex_str
        self.setFixedHeight(26)
        self._apply()
        self.clicked.connect(self._pick)

    def _apply(self) -> None:
        self.setStyleSheet(f"background:{self.hex}; border:1px solid #555; border-radius:5px;")
        self.setText(self.hex.upper())

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.hex), self, "Chọn màu")
        if c.isValid():
            self.hex = c.name()
            self._apply()


class RenderView(QWidget):
    renderRequested = Signal(dict)
    backRequested = Signal()
    previewStyleRequested = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self._out: str | None = None
        self._align = 2  # numpad alignment (bottom-centre)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- left controls (scrollable — many style options) ----
        left = QWidget()
        left.setObjectName("rail")
        left.setFixedWidth(452)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(452)
        scroll.setWidget(left)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(20, 18, 16, 16)
        ll.setSpacing(10)

        back = QPushButton("←  Quay lại sửa phụ đề")
        back.setObjectName("ghost")
        back.clicked.connect(self.backRequested)
        ll.addWidget(back)

        # --- subtitle style ---
        ll.addWidget(_section("Kiểu phụ đề"))
        self.preset = QComboBox()
        self.btn_save_preset = QPushButton("Lưu")
        self.btn_save_preset.setObjectName("ghost")
        self.btn_save_preset.setFixedWidth(46)
        self.btn_del_preset = QPushButton("Xóa")
        self.btn_del_preset.setObjectName("ghost")
        self.btn_del_preset.setFixedWidth(46)
        self.preset.activated.connect(self._on_preset)
        self.btn_save_preset.clicked.connect(self._save_preset)
        self.btn_del_preset.clicked.connect(self._delete_preset)
        ll.addWidget(QLabel("Mẫu kiểu"))
        prow = QHBoxLayout()
        prow.addWidget(self.preset, 1)
        prow.addWidget(self.btn_save_preset)
        prow.addWidget(self.btn_del_preset)
        ll.addLayout(prow)

        self.font = QComboBox()
        self.font.addItems(FONTS)
        ll.addLayout(self._row("Phông chữ", self.font))

        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(12, 40)
        self.size.setValue(20)
        self.size_lbl = QLabel("20px")
        self.size_lbl.setObjectName("mono")
        self.size.valueChanged.connect(lambda v: self.size_lbl.setText(f"{v}px"))
        ll.addLayout(self._slider_row("Cỡ chữ", self.size, self.size_lbl))

        self.c_text = _ColorButton("#ffffff")
        self.c_outline = _ColorButton("#000000")
        self.c_box = _ColorButton("#000000")
        ll.addLayout(self._row("Màu chữ", self.c_text))
        ll.addLayout(self._row("Màu viền", self.c_outline))

        self.outline_w = QSlider(Qt.Horizontal)
        self.outline_w.setRange(0, 6)
        self.outline_w.setValue(2)
        self.outline_lbl = QLabel("2")
        self.outline_lbl.setObjectName("mono")
        self.outline_w.valueChanged.connect(lambda v: self.outline_lbl.setText(str(v)))
        ll.addLayout(self._slider_row("Độ dày viền", self.outline_w, self.outline_lbl))

        self.box_style = QComboBox()
        self.box_style.addItems(list(BOX_STYLES))
        ll.addLayout(self._row("Kiểu nền", self.box_style))
        ll.addLayout(self._row("Màu nền", self.c_box))

        self.bold = QRadioButton("Đậm")
        self.italic = QRadioButton("Nghiêng")
        self.bold.setAutoExclusive(False)
        self.italic.setAutoExclusive(False)
        brow = QHBoxLayout()
        brow.addWidget(self.bold)
        brow.addWidget(self.italic)
        brow.addStretch(1)
        ll.addLayout(brow)

        # position grid (numpad alignment)
        ll.addWidget(QLabel("Vị trí"))
        ll.addLayout(self._align_grid())
        self.mv = QSlider(Qt.Horizontal)
        self.mv.setRange(0, 600)
        self.mv.setValue(60)
        self.mv_lbl = QLabel("60")
        self.mv_lbl.setObjectName("mono")
        self.mv.valueChanged.connect(lambda v: self.mv_lbl.setText(str(v)))
        ll.addLayout(self._slider_row("Lề (cách mép)", self.mv, self.mv_lbl))

        self.preview_btn = QPushButton("🖼  Xem thử kiểu trên video")
        self.preview_btn.setObjectName("ghost")
        self.preview_btn.clicked.connect(self._emit_preview)
        ll.addWidget(self.preview_btn)

        # --- voice ---
        ll.addSpacing(4)
        ll.addWidget(_section("Giọng lồng tiếng"))
        self.voice = QComboBox()
        ll.addLayout(self._row("Giọng", self.voice))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(70, 140)
        self.speed.setValue(100)
        self.speed_lbl = QLabel("1.00×")
        self.speed_lbl.setObjectName("mono")
        self.speed.valueChanged.connect(lambda v: self.speed_lbl.setText(f"{v/100:.2f}×"))
        ll.addLayout(self._slider_row("Tốc độ nói", self.speed, self.speed_lbl))

        # --- audio mix ---
        ll.addSpacing(4)
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

        self.orig_vol = QSlider(Qt.Horizontal)
        self.orig_vol.setRange(0, 150)
        self.orig_vol.setValue(100)
        self.orig_vol_lbl = QLabel("100%")
        self.orig_vol_lbl.setObjectName("mono")
        self.orig_vol.valueChanged.connect(lambda v: self.orig_vol_lbl.setText(f"{v}%"))
        ll.addLayout(self._slider_row("Âm lượng gốc", self.orig_vol, self.orig_vol_lbl))

        self.dub_vol = QSlider(Qt.Horizontal)
        self.dub_vol.setRange(50, 150)
        self.dub_vol.setValue(100)
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
        self.log.setFixedHeight(110)
        self.log.setVisible(False)
        self.open_btn = QPushButton("📂  Mở thư mục chứa video")
        self.open_btn.setObjectName("ghost")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self._open_folder)
        for w in (self.status, self.bar, self.log, self.open_btn):
            ll.addWidget(w)

        # ---- right preview (video or style image) ----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 20, 20, 20)
        rl.addWidget(_section("Xem trước"))
        self.right_stack = QStackedWidget()
        self.preview = VideoPanel()
        self.style_img = QLabel("Bấm “Xem thử kiểu” để xem kiểu phụ đề trên video")
        self.style_img.setAlignment(Qt.AlignCenter)
        self.style_img.setStyleSheet("background:#0c0c0e; border-radius:14px; color:#888;")
        self.right_stack.addWidget(self.preview)
        self.right_stack.addWidget(self.style_img)
        rl.addWidget(self.right_stack, 1)

        self._refresh_presets()
        root.addWidget(scroll)
        root.addWidget(right, 1)

    # --- layout helpers ------------------------------------------------------
    def _row(self, label: str, w: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(QLabel(label))
        box.addWidget(w)
        return box

    def _slider_row(self, label: str, slider: QSlider, value_lbl: QLabel) -> QVBoxLayout:
        head = QHBoxLayout()
        head.addWidget(QLabel(label))
        head.addStretch(1)
        head.addWidget(value_lbl)
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addLayout(head)
        box.addWidget(slider)
        return box

    def _align_grid(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(4)
        self.align_group = QButtonGroup(self)
        self._align_btns: dict[int, QPushButton] = {}
        # numpad layout: rows top(7,8,9) mid(4,5,6) bottom(1,2,3)
        layout = [(0, 7), (1, 8), (2, 9), (3, 4), (4, 5), (5, 6), (6, 1), (7, 2), (8, 3)]
        glyph = {7: "↖", 8: "↑", 9: "↗", 4: "←", 5: "•", 6: "→", 1: "↙", 2: "↓", 3: "↘"}
        for pos, num in layout:
            b = QPushButton(glyph[num])
            b.setCheckable(True)
            b.setFixedSize(40, 28)
            if num == self._align:
                b.setChecked(True)
            b.clicked.connect(lambda _=False, n=num: setattr(self, "_align", n))
            self.align_group.addButton(b)
            self._align_btns[num] = b
            grid.addWidget(b, pos // 3, pos % 3)
        return grid

    def _sync_audio_enabled(self) -> None:
        keep = not self.rb_replace.isChecked()
        self.orig_vol.setEnabled(keep)
        self.orig_vol_lbl.setEnabled(keep)

    # --- public API ----------------------------------------------------------
    def set_voices(self, voices: list[str]) -> None:
        self.voice.clear()
        self.voice.addItems(voices or ["(mặc định)"])

    def set_preview(self, video_path: Path, cues: list | None = None) -> None:
        self.preview.load(video_path)
        if cues is not None:
            self.preview.set_cues(cues)
        self.right_stack.setCurrentWidget(self.preview)

    def show_style_image(self, path: str) -> None:
        pm = QPixmap(str(path))
        if not pm.isNull():
            self.style_img.setPixmap(pm.scaled(self.style_img.size(), Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))
        self.right_stack.setCurrentWidget(self.style_img)

    # --- opts builders -------------------------------------------------------
    def _style(self) -> dict:
        border_style, box_alpha = BOX_STYLES[self.box_style.currentText()]
        return {
            "primary": self.c_text.hex,
            "outline": self.c_outline.hex,
            "box": self.c_box.hex,
            "box_alpha": box_alpha,
            "border_style": border_style,
            "outline_w": self.outline_w.value(),
            "shadow": 0,
            "bold": self.bold.isChecked(),
            "italic": self.italic.isChecked(),
            "alignment": self._align,
            "margin_v": self.mv.value(),
        }

    def _audio_mode(self) -> str:
        if self.rb_mix.isChecked():
            return "mix"
        if self.rb_duck.isChecked():
            return "duck"
        return "replace"

    def _emit_preview(self) -> None:
        self.previewStyleRequested.emit({"font": self.font.currentText(),
                                         "size": self.size.value(), "style": self._style()})

    def _emit_render(self) -> None:
        speed = self.speed.value() / 100.0
        opts = {
            "voice": self.voice.currentText() if self.voice.count() else None,
            "rate": f"{int(round((speed - 1) * 100)):+d}%",
            "audio_mode": self._audio_mode(),
            "orig_volume": self.orig_vol.value() / 100.0,
            "dub_volume": self.dub_vol.value() / 100.0,
            "font": self.font.currentText(),
            "size": self.size.value(),
            "style": self._style(),
        }
        self.bar.setVisible(True)
        self.log.setVisible(True)
        self.render_btn.setEnabled(False)
        self.renderRequested.emit(opts)

    # --- style presets -------------------------------------------------------
    def _presets_file(self) -> Path:
        from app.config import settings
        return Path(settings.vizsup_storage_dir) / "style_presets.json"

    def _load_presets(self) -> dict:
        import json
        p = self._presets_file()
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return d if isinstance(d, dict) else {}
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _refresh_presets(self, select: str | None = None) -> None:
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem("— Mẫu —")
        for name in sorted(self._load_presets()):
            self.preset.addItem(name)
        if select:
            i = self.preset.findText(select)
            if i >= 0:
                self.preset.setCurrentIndex(i)
        self.preset.blockSignals(False)

    def _on_preset(self, _i: int) -> None:
        data = self._load_presets().get(self.preset.currentText())
        if data:
            self._apply_style(data)

    def _save_preset(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        import json
        name, ok = QInputDialog.getText(self, "Lưu mẫu kiểu", "Tên mẫu:")
        if not ok or not name.strip():
            return
        name = name.strip()
        presets = self._load_presets()
        presets[name] = {"font": self.font.currentText(), "size": self.size.value(),
                         "style": self._style()}
        p = self._presets_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_presets(select=name)

    def _delete_preset(self) -> None:
        import json
        name = self.preset.currentText()
        presets = self._load_presets()
        if name in presets:
            del presets[name]
            self._presets_file().write_text(json.dumps(presets, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
            self._refresh_presets()

    def _apply_style(self, data: dict) -> None:
        s = data.get("style", {})
        if data.get("font"):
            i = self.font.findText(data["font"])
            if i >= 0:
                self.font.setCurrentIndex(i)
        if data.get("size"):
            self.size.setValue(int(data["size"]))
        for btn, key in ((self.c_text, "primary"), (self.c_outline, "outline"), (self.c_box, "box")):
            if s.get(key):
                btn.hex = s[key]
                btn._apply()
        self.outline_w.setValue(int(s.get("outline_w", self.outline_w.value())))
        self.bold.setChecked(bool(s.get("bold", False)))
        self.italic.setChecked(bool(s.get("italic", False)))
        self.mv.setValue(int(s.get("margin_v", self.mv.value())))
        bs = (int(s.get("border_style", 1)), int(s.get("box_alpha", 255)))
        for label, val in BOX_STYLES.items():
            if val == bs:
                j = self.box_style.findText(label)
                if j >= 0:
                    self.box_style.setCurrentIndex(j)
                break
        al = int(s.get("alignment", 2))
        self._align = al
        if al in self._align_btns:
            self._align_btns[al].setChecked(True)

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
