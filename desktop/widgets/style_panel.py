"""StylePanel — all subtitle-style controls (font, size, colours, box, bold/italic,
letter-spacing, line mode, margins) + a cross-project preset library + an
"Áp dụng cho" scope selector. Lives in the Screen-2 "Kiểu chữ" tab.

Emits ``styleChanged`` on any change (for the live preview) and ``previewRequested``
when the user asks to burn a WYSIWYG frame. ``get_style()`` returns a dict that
``app.pipeline.srt.write_ass`` understands.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

FONTS = ["Be Vietnam Pro", "Noto Sans", "Arial"]
# "Kiểu nền" → (border_style, box_alpha)
BOX_STYLES = {
    "Viền chữ (không nền)": (1, 255),
    "Hộp mờ": (4, 110),
    "Hộp đặc (che chữ Trung)": (4, 0),
}


class _ColorButton(QPushButton):
    """A swatch button that opens a colour picker and remembers the hex."""

    changed = Signal()

    def __init__(self, hex_str: str) -> None:
        super().__init__()
        self.hex = hex_str
        self.setFixedHeight(26)
        self._apply()
        self.clicked.connect(self._pick)

    def _apply(self) -> None:
        h = self.hex.lstrip("#")
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            r, g, b = 255, 255, 255
        fg = "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"
        self.setStyleSheet(
            f"QPushButton {{ background:{self.hex}; color:{fg}; "
            "border:1px solid #555; border-radius:5px; }")
        self.setText(self.hex.upper())

    def set_hex(self, hex_str: str) -> None:
        self.hex = hex_str
        self._apply()

    def _pick(self) -> None:
        # parent to the window (not this button) + clear stylesheet, else the
        # swatch background cascades and tints the whole dialog.
        dlg = QColorDialog(QColor(self.hex), self.window())
        dlg.setStyleSheet("")
        if dlg.exec():
            c = dlg.currentColor()
            if c.isValid():
                self.hex = c.name()
                self._apply()
                self.changed.emit()


class StylePanel(QScrollArea):
    styleChanged = Signal()
    previewRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.setWidget(inner)
        ll = QVBoxLayout(inner)
        ll.setContentsMargins(16, 14, 14, 14)
        ll.setSpacing(10)

        # Áp dụng cho (scope)
        self.scope = QComboBox()
        self.scope.addItems(["Toàn bộ", "Các câu đang chọn"])
        ll.addLayout(self._row("Áp dụng cho", self.scope))

        # Mẫu kiểu (cross-project library)
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
        ll.addWidget(QLabel("Mẫu kiểu (dùng chung mọi dự án)"))
        prow = QHBoxLayout()
        prow.addWidget(self.preset, 1)
        prow.addWidget(self.btn_save_preset)
        prow.addWidget(self.btn_del_preset)
        ll.addLayout(prow)

        # font + size
        self.font = QComboBox()
        self.font.addItems(FONTS)
        self.font.currentTextChanged.connect(self._changed)
        ll.addLayout(self._row("Phông chữ", self.font))
        self.size = self._slider(ll, "Cỡ chữ", 12, 40, 20, "px")

        # colours
        self.c_text = _ColorButton("#ffffff")
        self.c_outline = _ColorButton("#000000")
        self.c_box = _ColorButton("#000000")
        for cb in (self.c_text, self.c_outline, self.c_box):
            cb.changed.connect(self._changed)
        ll.addLayout(self._row("Màu chữ", self.c_text))
        ll.addLayout(self._row("Màu viền", self.c_outline))

        self.outline_w = self._slider(ll, "Độ dày viền", 0, 6, 2, "")

        self.box_style = QComboBox()
        self.box_style.addItems(list(BOX_STYLES))
        self.box_style.currentTextChanged.connect(self._changed)
        ll.addLayout(self._row("Kiểu nền", self.box_style))
        ll.addLayout(self._row("Màu nền", self.c_box))

        self.bold = QRadioButton("Đậm")
        self.italic = QRadioButton("Nghiêng")
        self.bold.setAutoExclusive(False)
        self.italic.setAutoExclusive(False)
        self.bold.toggled.connect(self._changed)
        self.italic.toggled.connect(self._changed)
        brow = QHBoxLayout()
        brow.addWidget(self.bold)
        brow.addWidget(self.italic)
        brow.addStretch(1)
        ll.addLayout(brow)

        # letter spacing + line mode (fit to 1-2 lines without shrinking)
        self.letter = self._slider(ll, "Giãn chữ", -20, 40, 0, "%")
        self.line_mode = QComboBox()
        self.line_mode.addItems(["Tự động", "1 dòng", "2 dòng"])
        self.line_mode.currentTextChanged.connect(self._changed)
        ll.addLayout(self._row("Chế độ dòng", self.line_mode))

        # margins (precise complement to dragging the box on the video)
        ll.addWidget(QLabel("Lề (kéo phụ đề trên video, hoặc chỉnh chính xác ở đây)"))
        self.m_left = self._slider(ll, "Lề trái", 0, 800, 60, "")
        self.m_right = self._slider(ll, "Lề phải", 0, 800, 60, "")
        self.m_v = self._slider(ll, "Lề dọc", 0, 900, 60, "")
        self.btn_reset_pos = QPushButton("↺  Đặt lại vị trí")
        self.btn_reset_pos.setObjectName("ghost")
        self.btn_reset_pos.clicked.connect(self._reset_pos)
        ll.addWidget(self.btn_reset_pos)

        self.preview_btn = QPushButton("🖼  Xem thử kiểu trên video")
        self.preview_btn.setObjectName("ghost")
        self.preview_btn.clicked.connect(self.previewRequested)
        ll.addWidget(self.preview_btn)
        ll.addStretch(1)

        self._pos = None  # free drag position (nx, ny); set by the video canvas
        self._refresh_presets()

    # --- layout helpers ------------------------------------------------------
    def _row(self, label: str, w: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(QLabel(label))
        box.addWidget(w)
        return box

    def _slider(self, parent: QVBoxLayout, label: str, lo: int, hi: int, val: int, unit: str) -> QSlider:
        head = QHBoxLayout()
        head.addWidget(QLabel(label))
        head.addStretch(1)
        vlbl = QLabel(f"{val}{unit}")
        vlbl.setObjectName("mono")
        head.addWidget(vlbl)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.valueChanged.connect(lambda v: (vlbl.setText(f"{v}{unit}"), self._changed()))
        parent.addLayout(head)
        parent.addWidget(s)
        return s

    # --- change / preview ----------------------------------------------------
    def _changed(self, *_a) -> None:
        self.styleChanged.emit()

    def _reset_pos(self) -> None:
        self._pos = None
        self.m_left.setValue(60)
        self.m_right.setValue(60)
        self.m_v.setValue(60)
        self._changed()

    def set_drag_position(self, nx: float, ny: float) -> None:
        """Called by the video canvas when the subtitle is dragged."""
        self._pos = (nx, ny)
        self._changed()

    # --- public API ----------------------------------------------------------
    def get_font(self) -> str:
        return self.font.currentText()

    def get_size_px(self) -> int:
        return self.size.value()

    def get_scope(self) -> str:
        return "selected" if self.scope.currentIndex() == 1 else "all"

    def get_style(self) -> dict:
        border_style, box_alpha = BOX_STYLES[self.box_style.currentText()]
        ass_size = self.size.value() * 3  # UI px → .ass units (1080 canvas)
        fsp = round(ass_size * self.letter.value() / 100.0)
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
            "alignment": 2,
            "margin_l": self.m_left.value(),
            "margin_r": self.m_right.value(),
            "margin_v": self.m_v.value(),
            "letter_spacing": fsp,
            "line_mode": self.line_mode.currentText(),
            "pos": list(self._pos) if self._pos else None,
        }

    def set_from(self, data: dict) -> None:
        """Apply a saved style/preset to the controls (no signal storms)."""
        self.blockSignals(True)
        s = data.get("style", data) or {}
        if data.get("font"):
            i = self.font.findText(data["font"])
            if i >= 0:
                self.font.setCurrentIndex(i)
        if data.get("size"):
            self.size.setValue(int(data["size"]))
        self.c_text.set_hex(s.get("primary", self.c_text.hex))
        self.c_outline.set_hex(s.get("outline", self.c_outline.hex))
        self.c_box.set_hex(s.get("box", self.c_box.hex))
        self.outline_w.setValue(int(s.get("outline_w", self.outline_w.value())))
        self.bold.setChecked(bool(s.get("bold", False)))
        self.italic.setChecked(bool(s.get("italic", False)))
        self.m_left.setValue(int(s.get("margin_l", 60)))
        self.m_right.setValue(int(s.get("margin_r", 60)))
        self.m_v.setValue(int(s.get("margin_v", 60)))
        lm = s.get("line_mode", "Tự động")
        j = self.line_mode.findText(lm)
        if j >= 0:
            self.line_mode.setCurrentIndex(j)
        bs = (int(s.get("border_style", 1)), int(s.get("box_alpha", 255)))
        for label, val in BOX_STYLES.items():
            if val == bs:
                k = self.box_style.findText(label)
                if k >= 0:
                    self.box_style.setCurrentIndex(k)
                break
        self._pos = tuple(s["pos"]) if s.get("pos") else None
        self.blockSignals(False)
        self._changed()

    # --- presets (cross-project) ---------------------------------------------
    def _presets_file(self):
        from pathlib import Path

        from app.config import settings
        return Path(settings.vizsup_storage_dir) / "style_presets.json"

    def _load_presets(self) -> dict:
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
            self.set_from(data)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Lưu mẫu kiểu", "Tên mẫu:")
        if not ok or not name.strip():
            return
        name = name.strip()
        presets = self._load_presets()
        presets[name] = {"font": self.get_font(), "size": self.get_size_px(), "style": self.get_style()}
        p = self._presets_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_presets(select=name)

    def _delete_preset(self) -> None:
        name = self.preset.currentText()
        presets = self._load_presets()
        if name in presets:
            del presets[name]
            self._presets_file().write_text(json.dumps(presets, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
            self._refresh_presets()
