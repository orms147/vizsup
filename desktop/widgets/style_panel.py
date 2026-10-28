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
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QColorDialog,
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

from app.pipeline import glass
from desktop.theme import V2
from desktop.widgets.chrome import button, icon_button
from desktop.widgets.no_wheel import NoWheelComboBox, NoWheelSlider
from app.fsutil import atomic_write_text

FONTS = ["Be Vietnam Pro", "Noto Sans", "Arial"]
# "Kiểu nền" → (border_style, box_alpha)
BOX_STYLES = {
    "Viền chữ (không nền)": (1, 255),
    "Hộp mờ": (4, 110),
    "Hộp đặc (che chữ Trung)": (4, 0),
    "Kính mờ trắng (che chữ Trung)": (1, 255),   # + "glass": the render blurs + tints
}
GLASS_STYLE = "Kính mờ trắng (che chữ Trung)"
GLASS_TEXT, GLASS_OUTLINE_W = "#1c1c1e", 0     # dark text reads on the white glass
PLAIN_TEXT, PLAIN_OUTLINE_W = "#ffffff", 2     # back to white + outline when leaving it
# below this white tint the glass is about as dark as the video: dark text stops reading,
# so the text turns white + outline (and back to dark above it)
GLASS_LIGHT_TEXT_BELOW = 25


def estimate_line_count(text: str, *, size_px: int, font: str, margin_l: int = 60,
                        margin_r: int = 60, bold: bool = False, letter_spacing: int = 0) -> int:
    """Approximate how many rows the line wraps to under the style + box width,
    measured in 1920-wide ASS space (size in .ass units = ui_px*3). Honours \\n."""
    if not (text or "").strip():
        return 0
    f = QFont(font)
    f.setPixelSize(max(8, int(size_px) * 3))
    f.setBold(bool(bold))
    if letter_spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, float(letter_spacing))
    fm = QFontMetrics(f)
    wrap_w = max(60, 1920 - int(margin_l) - int(margin_r))
    ls = fm.lineSpacing() or fm.height() or 1
    total = 0
    for para in text.split("\n"):
        br = fm.boundingRect(0, 0, wrap_w, 100000, int(Qt.TextWordWrap), para or " ")
        total += max(1, round(br.height() / ls))
    return total


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
    scopeChanged = Signal()
    resetSelectedRequested = Signal()

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
        self.scope = NoWheelComboBox()
        self.scope.addItems(["Toàn bộ", "Các câu đang chọn"])
        self.scope.currentIndexChanged.connect(self.scopeChanged)
        ll.addLayout(self._row("Áp dụng cho", self.scope))
        self.scope_hint = QLabel("→ áp cho toàn bộ phụ đề")
        self.scope_hint.setWordWrap(True)
        self.scope_hint.setStyleSheet("color:#8b8b9a;font-size:11px;")
        ll.addWidget(self.scope_hint)
        self.btn_reset_cue = button("Bỏ kiểu riêng (câu đang chọn)", variant="ghost", small=True)
        self.btn_reset_cue.clicked.connect(self.resetSelectedRequested)
        ll.addWidget(self.btn_reset_cue)

        # Mẫu kiểu (cross-project library)
        self.preset = NoWheelComboBox()
        self.btn_save_preset = icon_button("Plus", "Lưu kiểu hiện tại thành mẫu")
        self.btn_del_preset = icon_button("Trash2", "Xóa mẫu đang chọn")
        self.btn_reset = icon_button("RefreshCw", "Cài lại (về mẫu đang chọn, hoặc mặc định)")
        self.preset.activated.connect(self._on_preset)
        self.btn_save_preset.clicked.connect(self._save_preset)
        self.btn_del_preset.clicked.connect(self._delete_preset)
        self.btn_reset.clicked.connect(self._reset_to_applied)
        ll.addWidget(QLabel("Mẫu kiểu (dùng chung mọi dự án)"))
        prow = QHBoxLayout()
        prow.addWidget(self.preset, 1)
        prow.addWidget(self.btn_save_preset)
        prow.addWidget(self.btn_del_preset)
        prow.addWidget(self.btn_reset)
        ll.addLayout(prow)

        # font + size
        self.font = NoWheelComboBox()
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
        ll.addLayout(self._row("Màu viền (rìa chữ)", self.c_outline))

        self.outline_w = self._slider(ll, "Độ dày viền (rìa chữ)", 0, 6, 2, "")

        self.box_style = NoWheelComboBox()
        self.box_style.addItems(list(BOX_STYLES))
        self.box_style.currentTextChanged.connect(self._changed)
        self.box_style.currentTextChanged.connect(self._sync_box_color)
        self._prev_box = self.box_style.currentText()
        self._loading = False
        self.box_style.currentTextChanged.connect(self._sync_glass_colors)
        self.box_style.setItemData(
            list(BOX_STYLES).index(GLASS_STYLE),
            "Video phía sau dòng phụ đề được làm mờ mạnh và phủ trắng trong (kiểu kính iOS), "
            "chữ Trung in sẵn không còn đọc được; chữ Việt màu tối.", Qt.ToolTipRole)
        ll.addLayout(self._row("Kiểu nền", self.box_style))
        # "Màu nền" only matters for the box styles → hide it for "Viền chữ"
        self._box_color_row = QWidget()
        _bc = QVBoxLayout(self._box_color_row)
        _bc.setContentsMargins(0, 0, 0, 0)
        _bc.setSpacing(3)
        _bc.addWidget(QLabel("Màu nền (hộp)"))
        _bc.addWidget(self.c_box)
        ll.addWidget(self._box_color_row)
        # the frosted glass' own look (global only — the render blurs one band)
        self._glass_row = QWidget()
        _gl = QVBoxLayout(self._glass_row)
        _gl.setContentsMargins(0, 0, 0, 0)
        _gl.setSpacing(3)
        self.glass_tint = self._slider(_gl, "Độ trắng của nền", *glass.TINT_RANGE,
                                       round(glass.TINT * 100), "%")
        self.glass_tint.setToolTip(
            "Lớp trắng phủ trên phần video đã làm mờ. Thấp = kính trong, thấy rõ màu video "
            f"phía sau; dưới {GLASS_LIGHT_TEXT_BELOW}% chữ tự đổi sang trắng có viền.")
        self.glass_blur = self._slider(_gl, "Độ nhòe phía sau", *glass.BLUR_RANGE,
                                       glass.BLUR_SIGMA, "")
        self.glass_blur.setToolTip(
            "Độ làm mờ video sau dòng phụ đề (mặc định "
            f"{glass.BLUR_SIGMA}). Từ {glass.BLUR_SAFE} trở lên chữ Trung in sẵn không còn "
            "đọc được.")
        self.glass_warn = QLabel(f"⚠ Dưới mức {glass.BLUR_SAFE}: chữ Trung có thể đọc được.")
        self.glass_warn.setWordWrap(True)
        self.glass_warn.setStyleSheet(f"color:{V2['amber']};font-size:11px;")
        _gl.addWidget(self.glass_warn)
        self._prev_tint = self.glass_tint.value()
        self.glass_tint.valueChanged.connect(self._sync_tint_colors)
        self.glass_blur.valueChanged.connect(self._sync_glass_warn)
        ll.addWidget(self._glass_row)

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
        self.line_mode = NoWheelComboBox()
        self.line_mode.addItems(["Tự động", "1 dòng", "2 dòng"])
        self.line_mode.currentTextChanged.connect(self._changed)
        ll.addLayout(self._row("Chế độ dòng", self.line_mode))

        # margins (precise complement to dragging the box on the video)
        ll.addWidget(QLabel("Lề (kéo phụ đề trên video, hoặc chỉnh chính xác ở đây)"))
        self.m_left = self._slider(ll, "Lề trái", 0, 800, 60, "")
        self.m_right = self._slider(ll, "Lề phải", 0, 800, 60, "")
        self.m_v = self._slider(ll, "Lề dọc", 0, 900, 60, "")
        self.btn_reset_pos = button("Đặt lại vị trí", "RefreshCw", variant="ghost", small=True)
        self.btn_reset_pos.clicked.connect(self._reset_pos)
        ll.addWidget(self.btn_reset_pos)

        self.preview_btn = button("Xem thử kiểu", "Eye", variant="primary",
                                  tip="Dựng thử một khung hình đúng như video xuất")
        self.preview_btn.clicked.connect(self.previewRequested)
        ll.addWidget(self.preview_btn)
        ll.addStretch(1)

        self._pos = None       # free drag position (nx, ny); set by the video canvas
        self._applied = None   # last preset applied (for the ↻ reset button)
        self._refresh_presets()
        self._sync_box_color()
        self._sync_glass_warn()

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
        s = NoWheelSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.valueChanged.connect(lambda v: (vlbl.setText(f"{v}{unit}"), self._changed()))
        parent.addLayout(head)
        parent.addWidget(s)
        return s

    # --- change / preview ----------------------------------------------------
    def _changed(self, *_a) -> None:
        self.styleChanged.emit()

    def _sync_box_color(self, *_a) -> None:
        border_style, _ = BOX_STYLES[self.box_style.currentText()]
        self._box_color_row.setVisible(border_style in (3, 4))
        self._glass_row.setVisible(self.box_style.currentText() == GLASS_STYLE)

    def _sync_glass_warn(self, *_a) -> None:
        self.glass_warn.setVisible(self.glass_blur.value() < glass.BLUR_SAFE)

    def _sync_tint_colors(self, value: int) -> None:
        """Crossing the low-tint line flips the text: a nearly clear glass is as dark
        as the video (white + outline reads), a whiter one wants the dark text. Only
        a crossing recolours, so the user's own colour sticks; loading never does."""
        prev, self._prev_tint = self._prev_tint, value
        if self._loading or (prev < GLASS_LIGHT_TEXT_BELOW) == (value < GLASS_LIGHT_TEXT_BELOW):
            return
        light = QColor(self.c_text.hex).lightness() > 140
        if value < GLASS_LIGHT_TEXT_BELOW and not light:
            self.c_text.set_hex(PLAIN_TEXT)
            self.c_outline.set_hex("#000000")
            self.outline_w.setValue(PLAIN_OUTLINE_W)
        elif value >= GLASS_LIGHT_TEXT_BELOW and light:
            self.c_text.set_hex(GLASS_TEXT)
            self.outline_w.setValue(GLASS_OUTLINE_W)
        self._changed()

    def _sync_glass_colors(self, text: str) -> None:
        """Picking the white glass turns light text dark (white on white can't be read);
        leaving it turns dark text white again. Loading a saved style never recolours."""
        prev, self._prev_box = self._prev_box, text
        if self._loading or (prev == GLASS_STYLE) == (text == GLASS_STYLE):
            return
        light = QColor(self.c_text.hex).lightness() > 140
        if text == GLASS_STYLE and light:
            self.c_text.set_hex(GLASS_TEXT)
            self.outline_w.setValue(GLASS_OUTLINE_W)
        elif text != GLASS_STYLE and not light:
            self.c_text.set_hex(PLAIN_TEXT)
            self.outline_w.setValue(PLAIN_OUTLINE_W)
        self._changed()

    def _reset_pos(self) -> None:
        self._pos = None
        self.m_left.setValue(60)
        self.m_right.setValue(60)
        self.m_v.setValue(60)
        self._changed()

    def set_drag_position(self, nx: float, ny: float) -> None:
        """Called by the video canvas when the subtitle body is dragged."""
        self._pos = (nx, ny)
        self._changed()

    def set_drag_margins(self, left: int, right: int, vertical: int) -> None:
        """Called by the video canvas when the box edges are dragged — set the 3
        margin sliders and emit styleChanged exactly once (blockSignals on self
        suppresses styleChanged while the value labels still update)."""
        self.blockSignals(True)
        self.m_left.setValue(int(left))
        self.m_right.setValue(int(right))
        self.m_v.setValue(int(vertical))
        self.blockSignals(False)
        self._changed()

    def clear_drag_position(self) -> None:
        self._pos = None
        self._changed()

    # --- public API ----------------------------------------------------------
    def get_font(self) -> str:
        return self.font.currentText()

    def get_size_px(self) -> int:
        return self.size.value()

    def get_scope(self) -> str:
        return "selected" if self.scope.currentIndex() == 1 else "all"

    def set_scope_hint(self, text: str) -> None:
        self.scope_hint.setText(text)

    def get_style(self) -> dict:
        border_style, box_alpha = BOX_STYLES[self.box_style.currentText()]
        ass_size = self.size.value() * 3  # UI px → .ass units (1080 canvas)
        fsp = round(ass_size * self.letter.value() / 100.0)
        extra = {"glass": True, "glass_tint": self.glass_tint.value(),
                 "glass_blur": self.glass_blur.value()} \
            if self.box_style.currentText() == GLASS_STYLE else {}
        return {
            **extra,
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
        self._loading = True               # a saved style keeps its own colours
        try:
            lk = glass.look(s)             # missing (older style.json) → today's defaults
            self.glass_tint.setValue(round(lk["tint"] * 100))
            self.glass_blur.setValue(round(lk["blur"]))
            self._prev_tint = self.glass_tint.value()
            if s.get("glass"):
                self.box_style.setCurrentText(GLASS_STYLE)
            else:
                for label, val in BOX_STYLES.items():
                    if val == bs and label != GLASS_STYLE:
                        k = self.box_style.findText(label)
                        if k >= 0:
                            self.box_style.setCurrentIndex(k)
                        break
        finally:
            self._loading = False
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

    _FACTORY = {
        "font": "Be Vietnam Pro", "size": 20,
        "style": {"primary": "#ffffff", "outline": "#000000", "box": "#000000",
                  "box_alpha": 255, "border_style": 1, "outline_w": 2, "bold": False,
                  "italic": False, "margin_l": 60, "margin_r": 60, "margin_v": 60,
                  "line_mode": "Tự động", "pos": None},
    }

    def _on_preset(self, _i: int) -> None:
        data = self._load_presets().get(self.preset.currentText())
        if data:
            self._applied = data
            self.set_from(data)

    def _reset_to_applied(self) -> None:
        """↻ — revert controls to the applied preset, or the factory default."""
        self.set_from(self._applied or self._FACTORY)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Lưu mẫu kiểu", "Tên mẫu:")
        if not ok or not name.strip():
            return
        name = name.strip()
        presets = self._load_presets()
        presets[name] = {"font": self.get_font(), "size": self.get_size_px(), "style": self.get_style()}
        self._applied = presets[name]
        p = self._presets_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_presets(select=name)

    def _delete_preset(self) -> None:
        name = self.preset.currentText()
        presets = self._load_presets()
        if name in presets:
            del presets[name]
            atomic_write_text(self._presets_file(), json.dumps(presets, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
            self._refresh_presets()
