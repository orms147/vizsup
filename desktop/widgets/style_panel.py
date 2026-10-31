"""StylePanel — the subtitle look in the Screen-2 "Kiểu chữ" tab, in three groups:
**Chữ** (font, size, colour, opacity, bold/italic, outline), **Nền** (background kind,
colour, opacity, the frosted glass) and **Dòng & vị trí** (line mode, letter spacing,
margins — folded away by default), plus a cross-project preset library and the
"Áp dụng cho" scope. Labels sit left of their control so the tab fits without scrolling.

Colours open a wheel popup (desktop/widgets/color_picker.py); fonts are every installed
font with the Vietnamese letters plus the ones added with "Thêm font…" (desktop/fonts.py).

Emits ``styleChanged`` on any change (for the live preview) and ``previewRequested``
when the user asks to burn a WYSIWYG frame. ``get_style()`` returns a dict that
``app.pipeline.srt.write_ass`` understands.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QCompleter,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.fsutil import atomic_write_text
from app.pipeline import glass
from desktop.icons import icon
from desktop.theme import V2
from desktop.widgets.chrome import button, icon_button
from desktop.widgets.no_wheel import NoWheelComboBox, NoWheelSlider

FONTS = ["Be Vietnam Pro", "Noto Sans", "Arial"]      # fallback when Qt lists nothing
# "Kiểu nền" → (border_style, box_alpha): the alpha is where "Độ mờ nền" starts
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
TEXT_FADE_MAX = 90           # "Độ mờ chữ" stops here: a fully clear line is no subtitle
LABEL_W = 92

# Built-in "Mẫu kiểu": the fan-sub look the owner showed (2026-10-31) — white letters, a
# thick black outline + a little shadow, no box, the Chinese band blurred behind ("cover").
# Fonts are bundled (OFL, desktop/assets/fonts). "size" is the panel's px (×3 in the .ass).
_COMIC = {"primary": "#FFFFFF", "outline": "#000000", "box": "#000000", "box_alpha": 255,
          "text_alpha": 0, "border_style": 1, "shadow": 2, "italic": False, "alignment": 2,
          "margin_l": 60, "margin_r": 60, "margin_v": 60, "line_mode": "Tự động", "pos": None,
          "cover": "blur"}
BUILTIN_PRESETS = {
    "Truyện tranh · CHỮ IN HOA (Bangers)": {
        "font": "Bangers", "size": 34,
        "style": {**_COMIC, "outline_w": 7, "bold": False, "uppercase": True}},
    "Tròn đậm · viền đen (Coiny)": {
        "font": "Coiny", "size": 28,
        "style": {**_COMIC, "outline_w": 6, "bold": False, "uppercase": False}},
}


def pct_to_alpha(pct: int) -> int:
    """"Độ mờ" 0 % (solid) … 100 % (clear) → ASS alpha 0 … 255. Pure."""
    return max(0, min(255, round(int(pct) * 255 / 100)))


def alpha_to_pct(alpha: int) -> int:
    return max(0, min(100, round(int(alpha) * 100 / 255)))


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
    """A round swatch + hex; a click opens the colour wheel under it. The colour is
    applied live while picking (``changed`` each time)."""

    changed = Signal()

    def __init__(self, hex_str: str) -> None:
        super().__init__()
        self.hex = hex_str
        self.setFixedHeight(30)
        self.setMinimumWidth(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Bấm để chọn màu (vòng màu, mã hex, màu gần đây)")
        self._popup = None
        self._apply()
        self.clicked.connect(self._pick)

    def _apply(self) -> None:
        self.setStyleSheet(
            f"QPushButton{{text-align:left;padding-left:34px;background:{V2['input']};"
            f"border:1px solid {V2['field_line']};border-radius:7px;color:{V2['text']};}}"
            f"QPushButton:hover{{border-color:{V2['purple_bright']};}}")
        self.setText(self.hex.upper())
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        from PySide6.QtGui import QPainter, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(V2["line"]), 1))
        p.setBrush(QColor(self.hex))
        r = self.height() - 12
        p.drawEllipse(9, 6, r, r)
        p.end()

    def set_hex(self, hex_str: str) -> None:
        self.hex = hex_str
        self._apply()

    def _pick(self) -> None:
        from desktop.widgets.color_picker import ColorPopup

        pop = ColorPopup(self.hex, self.window())
        pop.colorChanged.connect(self._live)
        self._popup = pop
        pop.adjustSize()
        at = self.mapToGlobal(QPoint(0, self.height() + 4))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            at.setX(min(at.x(), screen.right() - pop.width()))
            if at.y() + pop.height() > screen.bottom():
                at.setY(self.mapToGlobal(QPoint(0, 0)).y() - pop.height() - 4)
        pop.move(at)
        pop.show()

    def _live(self, hex_str: str) -> None:
        if hex_str and hex_str.lower() != self.hex.lower():
            self.hex = hex_str
            self._apply()
            self.changed.emit()


class _Section(QWidget):
    """A titled group whose body folds away (▸ / ▾)."""

    def __init__(self, title: str, *, open_: bool = True) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.head = QToolButton()
        self.head.setText(title)
        self.head.setCheckable(True)
        self.head.setChecked(open_)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setStyleSheet(
            f"QToolButton{{border:none;background:transparent;color:{V2['text']};"
            f"font-weight:600;padding:2px 0;}}")
        self.head.toggled.connect(self._toggle)
        v.addWidget(self.head)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnMinimumWidth(0, LABEL_W)
        self.grid.setColumnStretch(1, 1)
        v.addWidget(self.body)
        self._toggle(open_)

    def _toggle(self, on: bool) -> None:
        self.body.setVisible(on)
        self.head.setIcon(icon("ChevronDown" if on else "ChevronRight", V2["secondary"], 14))

    def row(self, label: str, w: QWidget | QHBoxLayout, value: QLabel | None = None,
            tip: str = "") -> int:
        r = self.grid.rowCount()
        lab = QLabel(label)
        lab.setStyleSheet(f"color:{V2['secondary']};")
        lab.setWordWrap(True)
        if tip:
            lab.setToolTip(tip)
        self.grid.addWidget(lab, r, 0, Qt.AlignVCenter)
        if isinstance(w, QWidget):
            self.grid.addWidget(w, r, 1, 1, 1 if value is not None else 2)
        else:
            self.grid.addLayout(w, r, 1, 1, 1 if value is not None else 2)
        if value is not None:
            self.grid.addWidget(value, r, 2, Qt.AlignRight | Qt.AlignVCenter)
        return r

    def widgets_of(self, r: int) -> list[QWidget]:
        out = []
        for c in range(3):
            item = self.grid.itemAtPosition(r, c)
            if item is not None and item.widget() is not None:
                out.append(item.widget())
            elif item is not None and item.layout() is not None:
                lay = item.layout()
                out += [lay.itemAt(i).widget() for i in range(lay.count())
                        if lay.itemAt(i).widget() is not None]
        return out


class StylePanel(QScrollArea):
    styleChanged = Signal()
    previewRequested = Signal()
    scopeChanged = Signal()
    resetSelectedRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._shadow = 0               # no slider: kept from the applied preset / style
        self._cover = ""               # the cover a preset asks for ("blur")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.setWidget(inner)
        ll = QVBoxLayout(inner)
        ll.setContentsMargins(16, 12, 14, 14)
        ll.setSpacing(12)
        self._loading = False

        # Áp dụng cho (scope) + Mẫu kiểu (cross-project library)
        top = _Section("Áp dụng & mẫu")
        top.head.setVisible(False)
        self.scope = NoWheelComboBox()
        self.scope.addItems(["Toàn bộ", "Các câu đang chọn"])
        self.scope.currentIndexChanged.connect(self.scopeChanged)
        self.scope.currentIndexChanged.connect(self._sync_scope)
        top.row("Áp dụng cho", self.scope)
        self.scope_hint = QLabel("→ áp cho toàn bộ phụ đề")
        self.scope_hint.setWordWrap(True)
        self.scope_hint.setStyleSheet(f"color:{V2['muted']};font-size:11px;")
        top.grid.addWidget(self.scope_hint, top.grid.rowCount(), 1, 1, 2)
        self.btn_reset_cue = button("Bỏ kiểu riêng (câu đang chọn)", variant="ghost", small=True)
        self.btn_reset_cue.clicked.connect(self.resetSelectedRequested)
        top.grid.addWidget(self.btn_reset_cue, top.grid.rowCount(), 1, 1, 2, Qt.AlignLeft)
        self.preset = NoWheelComboBox()
        self.preset.setToolTip("Mẫu kiểu dùng chung mọi dự án")
        # long preset names must not widen the editor's side column (the list still shows
        # them in full when opened)
        self.preset.setMinimumContentsLength(4)
        self.preset.setSizeAdjustPolicy(NoWheelComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.btn_save_preset = icon_button("Plus", "Lưu kiểu hiện tại thành mẫu")
        self.btn_del_preset = icon_button("Trash2", "Xóa mẫu đang chọn")
        self.btn_reset = icon_button("RefreshCw", "Cài lại (về mẫu đang chọn, hoặc mặc định)")
        self.preset.activated.connect(self._on_preset)
        self.btn_save_preset.clicked.connect(self._save_preset)
        self.btn_del_preset.clicked.connect(self._delete_preset)
        self.btn_reset.clicked.connect(self._reset_to_applied)
        prow = QHBoxLayout()
        prow.setSpacing(4)
        prow.addWidget(self.preset, 1)
        for b in (self.btn_save_preset, self.btn_del_preset, self.btn_reset):
            prow.addWidget(b)
        top.row("Mẫu kiểu", prow, tip="Lưu / dùng lại một kiểu cho mọi dự án")
        ll.addWidget(top)

        # --- Chữ ------------------------------------------------------------------
        text = _Section("Chữ")
        self.text_section = text
        self.font = NoWheelComboBox()
        self.font.setEditable(True)
        self.font.setInsertPolicy(NoWheelComboBox.NoInsert)
        self.font.setMinimumContentsLength(8)
        self.font.setSizeAdjustPolicy(NoWheelComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font.lineEdit().setPlaceholderText("Tìm font…")
        self._family = "Be Vietnam Pro"
        self.font.currentIndexChanged.connect(self._on_font_index)
        self.font.lineEdit().editingFinished.connect(self._on_font_typed)
        self.btn_add_font = icon_button("Plus", "Thêm font từ máy (.ttf / .otf)…")
        self.btn_add_font.clicked.connect(self._add_fonts)
        frow = QHBoxLayout()
        frow.setSpacing(4)
        frow.addWidget(self.font, 1)
        frow.addWidget(self.btn_add_font)
        text.row("Phông chữ", frow, tip="Mọi font trên máy có đủ dấu tiếng Việt, và font bạn "
                                        "thêm bằng nút +")
        self._fill_fonts()
        self.size, v = self._slider(12, 40, 20, "px")
        text.row("Cỡ chữ", self.size, v)
        self.c_text = _ColorButton("#ffffff")
        text.row("Màu chữ", self.c_text)
        self.text_fade, v = self._slider(0, TEXT_FADE_MAX, 0, "%")
        self.text_fade.setToolTip("0% = chữ đặc · càng cao chữ càng trong (thấy video qua chữ)")
        text.row("Độ mờ chữ", self.text_fade, v, tip="0% = chữ đặc, cao hơn = chữ trong hơn")
        self.bold = QPushButton("B")
        self.italic = QPushButton("I")
        self.caps = QPushButton("AA")
        for b, tip in ((self.bold, "Chữ đậm"), (self.italic, "Chữ nghiêng"),
                       (self.caps, "CHỮ IN HOA — chỉ đổi chữ đốt lên video, phụ đề gốc giữ nguyên")):
            b.setCheckable(True)
            b.setFixedSize(32, 30)          # three fit the 1366 inspector (B · I · AA)
            b.setToolTip(tip)
            b.toggled.connect(self._changed)
        self.bold.setStyleSheet("QPushButton{font-weight:800;}")
        self.italic.setStyleSheet("QPushButton{font-style:italic;font-family:serif;}")
        brow = QHBoxLayout()
        brow.setSpacing(6)
        brow.addWidget(self.bold)
        brow.addWidget(self.italic)
        brow.addWidget(self.caps)
        brow.addStretch(1)
        text.row("Kiểu", brow)
        self.c_outline = _ColorButton("#000000")
        text.row("Màu viền", self.c_outline, tip="Viền quanh từng chữ")
        self.outline_w, v = self._slider(0, 8, 2, "")    # one digit: the 1366 inspector stays unclipped
        text.row("Độ dày viền", self.outline_w, v)
        ll.addWidget(text)

        # --- Nền ------------------------------------------------------------------
        bg = _Section("Nền")
        self.bg_section = bg
        self.box_style = NoWheelComboBox()
        self.box_style.addItems(list(BOX_STYLES))
        self.box_style.setItemData(
            list(BOX_STYLES).index(GLASS_STYLE),
            "Video phía sau dòng phụ đề được làm mờ mạnh và phủ trắng trong (kiểu kính iOS), "
            "chữ Trung in sẵn không còn đọc được; chữ Việt màu tối.", Qt.ToolTipRole)
        self._prev_box = self.box_style.currentText()
        bg.row("Kiểu nền", self.box_style)
        self.c_box = _ColorButton("#000000")
        self._r_box_color = bg.row("Màu nền", self.c_box)
        self.box_fade, v = self._slider(0, 100, alpha_to_pct(BOX_STYLES["Hộp mờ"][1]), "%")
        self.box_fade.setToolTip("0% = nền đặc (che hẳn) · càng cao nền càng trong")
        self._r_box_fade = bg.row("Độ mờ nền", self.box_fade, v,
                                  tip="0% = nền đặc, cao hơn = nền trong hơn")
        # the frosted glass' own look (global only — the render blurs one band)
        self.glass_tint, v = self._slider(*glass.TINT_RANGE, round(glass.TINT * 100), "%")
        self.glass_tint.setToolTip(
            "Lớp trắng phủ trên phần video đã làm mờ. Thấp = kính trong, thấy rõ màu video "
            f"phía sau; dưới {GLASS_LIGHT_TEXT_BELOW}% chữ tự đổi sang trắng có viền.")
        self._r_tint = bg.row("Độ trắng kính", self.glass_tint, v)
        self.glass_blur, v = self._slider(*glass.BLUR_RANGE, glass.BLUR_SIGMA, "")
        self.glass_blur.setToolTip(
            "Độ làm mờ video sau dòng phụ đề (mặc định "
            f"{glass.BLUR_SIGMA}). Từ {glass.BLUR_SAFE} trở lên chữ Trung in sẵn không còn "
            "đọc được.")
        self._r_blur = bg.row("Độ nhòe kính", self.glass_blur, v)
        self.glass_warn = QLabel(f"⚠ Dưới mức {glass.BLUR_SAFE}: chữ Trung có thể đọc được.")
        self.glass_warn.setWordWrap(True)
        self.glass_warn.setStyleSheet(f"color:{V2['amber']};font-size:11px;")
        bg.grid.addWidget(self.glass_warn, bg.grid.rowCount(), 0, 1, 3)
        # state flags kept for callers (visible ⇔ those controls shown): zero-size CHILDREN
        # — a parentless QWidget shown by setVisible(True) became an empty extra window
        self._glass_row = QWidget(inner)
        self._box_color_row = QWidget(inner)
        for flag in (self._glass_row, self._box_color_row):
            flag.setFixedSize(0, 0)
        ll.addWidget(bg)

        # --- Dòng & vị trí --------------------------------------------------------------
        pos = _Section("Dòng & vị trí", open_=False)
        self.pos_section = pos
        self.line_mode = NoWheelComboBox()
        self.line_mode.addItems(["Tự động", "1 dòng", "2 dòng"])
        pos.row("Chế độ dòng", self.line_mode)
        self.letter, v = self._slider(-20, 40, 0, "%")
        pos.row("Giãn chữ", self.letter, v)
        hint = QLabel("Kéo phụ đề trên video để đặt chỗ; chỉnh chính xác ở đây.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{V2['muted']};font-size:11px;")
        pos.grid.addWidget(hint, pos.grid.rowCount(), 0, 1, 3)
        self.m_left, v = self._slider(0, 800, 60, "")
        pos.row("Lề trái", self.m_left, v)
        self.m_right, v = self._slider(0, 800, 60, "")
        pos.row("Lề phải", self.m_right, v)
        self.m_v, v = self._slider(0, 900, 60, "")
        pos.row("Lề dọc", self.m_v, v)
        self.btn_reset_pos = button("Đặt lại vị trí", "RefreshCw", variant="ghost", small=True)
        self.btn_reset_pos.clicked.connect(self._reset_pos)
        pos.grid.addWidget(self.btn_reset_pos, pos.grid.rowCount(), 1, 1, 2, Qt.AlignLeft)
        ll.addWidget(pos)

        self.preview_btn = button("Xem thử kiểu", "Eye", variant="primary",
                                  tip="Dựng thử một khung hình đúng như video xuất")
        self.preview_btn.clicked.connect(self.previewRequested)
        ll.addWidget(self.preview_btn)
        ll.addStretch(1)

        for cb in (self.c_text, self.c_outline, self.c_box):
            cb.changed.connect(self._changed)
        self.line_mode.currentTextChanged.connect(self._changed)
        self.box_style.currentTextChanged.connect(self._on_box_style)
        self.glass_tint.valueChanged.connect(self._sync_tint_colors)
        self.glass_blur.valueChanged.connect(self._sync_glass_warn)
        self._prev_tint = self.glass_tint.value()

        self._pos = None       # free drag position (nx, ny); set by the video canvas
        self._applied = None   # last preset applied (for the ↻ reset button)
        self._refresh_presets()
        self._sync_box_color()
        self._sync_glass_warn()
        self._sync_scope()

    def _sync_scope(self, *_a) -> None:
        """The hint and "Bỏ kiểu riêng" only matter for the selected lines."""
        sel = self.scope.currentIndex() == 1
        self.scope_hint.setVisible(sel)
        self.btn_reset_cue.setVisible(sel)

    # --- layout helpers ------------------------------------------------------
    def _slider(self, lo: int, hi: int, val: int, unit: str) -> tuple[QSlider, QLabel]:
        vlbl = QLabel(f"{val}{unit}")
        vlbl.setObjectName("mono")
        vlbl.setMinimumWidth(40)
        vlbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        s = NoWheelSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setMinimumWidth(60)
        s.valueChanged.connect(lambda v: (vlbl.setText(f"{v}{unit}"), self._changed()))
        return s, vlbl

    # --- fonts ----------------------------------------------------------------
    def _fill_fonts(self, select: str | None = None) -> None:
        from desktop import fonts

        try:
            bundled, user, system = fonts.families()
        except Exception:  # noqa: BLE001 - a font scan must never break the editor
            bundled, user, system = FONTS[:1], [], FONTS[1:]
        want = select or self._family
        self.font.blockSignals(True)
        self.font.clear()
        for group in (bundled, user, system):
            if group and self.font.count():
                self.font.insertSeparator(self.font.count())
            for fam in group:
                self.font.addItem(fam)
                self.font.setItemData(self.font.count() - 1, QFont(fam, 11), Qt.FontRole)
                if fam in user:
                    self.font.setItemData(self.font.count() - 1, "Font bạn đã thêm",
                                          Qt.ToolTipRole)
        if self.font.findText(want) < 0:
            self.font.addItem(want)          # a preset's font that is not on this machine
        names = [self.font.itemText(i) for i in range(self.font.count()) if self.font.itemText(i)]
        comp = QCompleter(names, self.font)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.font.setCompleter(comp)
        self.font.setCurrentIndex(self.font.findText(want))
        self._family = want
        self.font.blockSignals(False)

    def _on_font_index(self, i: int) -> None:
        fam = self.font.itemText(i)
        if fam and fam != self._family:
            self._family = fam
            self._changed()

    def _on_font_typed(self) -> None:
        typed = self.font.currentText().strip()
        i = self.font.findText(typed, Qt.MatchFixedString)
        if i >= 0:
            self.font.setCurrentIndex(i)
            self._on_font_index(i)
        else:                                # not a font: back to the one in use
            self.font.setCurrentIndex(self.font.findText(self._family))

    def _add_fonts(self) -> None:
        from desktop import fonts

        paths, _ = QFileDialog.getOpenFileNames(self, "Thêm font", "",
                                                "Font (*.ttf *.otf *.ttc)")
        if not paths:
            return
        results = fonts.add_font_files(paths)
        good = [fam for fam, vi, err in results if not err and vi]
        self._fill_fonts(select=good[0] if good else None)
        if good:
            self._changed()
        bad = [f"• {fam}: thiếu dấu tiếng Việt — không dùng cho phụ đề được" if not err else
               f"• {fam}: {err}" for fam, vi, err in results if err or not vi]
        if bad:
            QMessageBox.information(self, "Thêm font",
                                    (f"Đã thêm {len(good)} font.\n\n" if good else "")
                                    + "\n".join(bad))

    # --- change / preview ----------------------------------------------------
    def _changed(self, *_a) -> None:
        self.styleChanged.emit()

    def _on_box_style(self, text: str) -> None:
        """A background kind picked by hand starts "Độ mờ nền" at its own level (Hộp mờ
        ~43 %, Hộp đặc 0 %); loading a saved style keeps the saved level."""
        if not self._loading and text in BOX_STYLES and BOX_STYLES[text][0] in (3, 4):
            self.box_fade.blockSignals(True)
            self.box_fade.setValue(alpha_to_pct(BOX_STYLES[text][1]))
            self.box_fade.blockSignals(False)
            self._set_value_label(self.box_fade, "%")
        self._sync_box_color()
        self._sync_glass_colors(text)
        self._changed()

    def _set_value_label(self, slider: QSlider, unit: str) -> None:
        for sec in (self.text_section, self.bg_section, self.pos_section):
            for r in range(sec.grid.rowCount()):
                item = sec.grid.itemAtPosition(r, 1)
                if item is not None and item.widget() is slider:
                    lab = sec.grid.itemAtPosition(r, 2)
                    if lab is not None and lab.widget() is not None:
                        lab.widget().setText(f"{slider.value()}{unit}")

    def _sync_box_color(self, *_a) -> None:
        text = self.box_style.currentText()
        boxed = BOX_STYLES[text][0] in (3, 4)
        is_glass = text == GLASS_STYLE
        bg = self.bg_section
        for r in (self._r_box_color, self._r_box_fade):
            for w in bg.widgets_of(r):
                w.setVisible(boxed)
        for r in (self._r_tint, self._r_blur):
            for w in bg.widgets_of(r):
                w.setVisible(is_glass)
        self._box_color_row.setVisible(boxed)
        self._glass_row.setVisible(is_glass)
        self._sync_glass_warn()

    def _sync_glass_warn(self, *_a) -> None:
        self.glass_warn.setVisible(self.box_style.currentText() == GLASS_STYLE
                                   and self.glass_blur.value() < glass.BLUR_SAFE)

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
        return self._family

    def get_size_px(self) -> int:
        return self.size.value()

    def get_scope(self) -> str:
        return "selected" if self.scope.currentIndex() == 1 else "all"

    def set_scope_hint(self, text: str) -> None:
        self.scope_hint.setText(text)

    def get_style(self) -> dict:
        border_style, box_alpha = BOX_STYLES[self.box_style.currentText()]
        if border_style in (3, 4):
            box_alpha = pct_to_alpha(self.box_fade.value())
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
            "text_alpha": pct_to_alpha(self.text_fade.value()),
            "border_style": border_style,
            "outline_w": self.outline_w.value(),
            "shadow": self._shadow,
            "bold": self.bold.isChecked(),
            "italic": self.italic.isChecked(),
            "uppercase": self.caps.isChecked(),
            **({"cover": self._cover} if self._cover else {}),
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
        if data.get("font") and data["font"] != self._family:
            self._fill_fonts(select=str(data["font"]))
        if data.get("size"):
            self.size.setValue(int(data["size"]))
        self.c_text.set_hex(s.get("primary", self.c_text.hex))
        self.c_outline.set_hex(s.get("outline", self.c_outline.hex))
        self.c_box.set_hex(s.get("box", self.c_box.hex))
        self.outline_w.setValue(int(s.get("outline_w", self.outline_w.value())))
        self.text_fade.setValue(min(TEXT_FADE_MAX, alpha_to_pct(int(s.get("text_alpha") or 0))))
        self.bold.setChecked(bool(s.get("bold", False)))
        self.italic.setChecked(bool(s.get("italic", False)))
        self.caps.setChecked(bool(s.get("uppercase", False)))
        self._shadow = int(s.get("shadow") or 0)          # kept from a preset (no slider)
        self._cover = str(s.get("cover") or "")           # the cover a preset asks for
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
            elif bs[0] in (3, 4):          # a box: "đặc" when opaque, else "mờ" + its level
                self.box_style.setCurrentText("Hộp đặc (che chữ Trung)" if bs[1] == 0
                                              else "Hộp mờ")
                self.box_fade.setValue(alpha_to_pct(bs[1]))
            else:
                self.box_style.setCurrentText("Viền chữ (không nền)")
        finally:
            self._loading = False
        self._pos = tuple(s["pos"]) if s.get("pos") else None
        self._prev_box = self.box_style.currentText()
        self._sync_box_color()
        self.blockSignals(False)
        self._changed()

    # --- presets (cross-project) ---------------------------------------------
    def _presets_file(self):
        from pathlib import Path

        from app.config import settings
        return Path(settings.vizsup_storage_dir) / "style_presets.json"

    def _load_presets(self) -> dict:
        """The built-in looks first, then the user's own (a user preset of the same name
        wins)."""
        mine: dict = {}
        p = self._presets_file()
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                mine = d if isinstance(d, dict) else {}
            except Exception:  # noqa: BLE001
                mine = {}
        return {**BUILTIN_PRESETS, **mine}

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
        presets = {k: v for k, v in self._load_presets().items() if k not in BUILTIN_PRESETS}
        presets[name] = {"font": self.get_font(), "size": self.get_size_px(), "style": self.get_style()}
        self._applied = presets[name]
        p = self._presets_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
        self._refresh_presets(select=name)

    def _delete_preset(self) -> None:
        name = self.preset.currentText()
        if name in BUILTIN_PRESETS:
            return                                  # the built-in looks can't be deleted
        presets = {k: v for k, v in self._load_presets().items() if k not in BUILTIN_PRESETS}
        if name in presets:
            del presets[name]
            atomic_write_text(self._presets_file(), json.dumps(presets, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
            self._refresh_presets()
