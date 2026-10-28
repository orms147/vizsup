"""Series editor ("Bộ phim"): what one series keeps from episode to episode, as plain
tables — no JSON. Tabs: summary (brief), characters (zh / vi / gender / xưng hô note),
glossary (zh → vi), pronunciation (written → spoken), saved settings (forget the stored
subtitle style / render settings so the next episode learns them again).

The conversion between the tables and the profile is pure (``rows_to_map``,
``rows_to_characters``, ``apply_rows``) and tested without a window; the dialog only
shows them. All file I/O goes through ``app.pipeline.series``.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import paths  # noqa: F401  (puts backend on sys.path)
from app.pipeline import series  # noqa: E402

GENDERS = (("", "—"), ("nam", "Nam"), ("nữ", "Nữ"))


# --- pure conversion (tested without a window) -------------------------------------------

def rows_to_map(rows: list[tuple[str, str]]) -> dict[str, str]:
    """Two-column rows → dict; rows with an empty key are dropped, a repeated key keeps
    the LAST row (what the user sees at the bottom wins), keys/values are stripped."""
    out: dict[str, str] = {}
    for k, v in rows:
        k = (k or "").strip()
        if k:
            out[k] = (v or "").strip()
    return out


def rows_to_characters(rows: list[tuple[str, str, str, str]]) -> list[dict]:
    """(zh, vi, gender, note) rows → characters; a row needs a Chinese name; the first
    row of a repeated name wins (order is the user's)."""
    out: list[dict] = []
    seen: set[str] = set()
    for zh, vi, gender, note in rows:
        zh = (zh or "").strip()
        if not zh or zh in seen:
            continue
        seen.add(zh)
        out.append({"zh": zh, "vi": (vi or "").strip(), "gender": (gender or "").strip(),
                    "note": (note or "").strip()})
    return out


def apply_rows(p: series.Profile, *, brief: str, characters: list[tuple[str, str, str, str]],
               glossary: list[tuple[str, str]], pronounce: list[tuple[str, str]]) -> series.Profile:
    """The profile with its four editable parts replaced (style / render are untouched)."""
    p.brief = (brief or "").strip()
    p.characters = rows_to_characters(characters)
    p.glossary = {k: v for k, v in rows_to_map(glossary).items() if v}
    p.pronounce = rows_to_map(pronounce)
    return p


# --- the dialog ----------------------------------------------------------------------------

class _Table(QWidget):
    """A table with "Thêm dòng" / "Xóa dòng" under it."""

    def __init__(self, headers: list[str], hint: str = "", parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        if hint:
            lbl = QLabel(hint)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Thêm dòng")
        rm = QPushButton("Xóa dòng")
        add.clicked.connect(lambda: self.add_row())
        rm.clicked.connect(self.remove_row)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)

    def add_row(self, *cells: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c in range(self.table.columnCount()):
            self.table.setItem(r, c, QTableWidgetItem(cells[c] if c < len(cells) else ""))

    def remove_row(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def cells(self) -> list[tuple[str, ...]]:
        out = []
        for r in range(self.table.rowCount()):
            out.append(tuple((self.table.item(r, c).text() if self.table.item(r, c) else "")
                             for c in range(self.table.columnCount())))
        return out


class _Characters(_Table):
    """Characters: the gender column is a combo (nam / nữ / unknown)."""

    def __init__(self, parent=None) -> None:
        super().__init__(["Tiếng Trung", "Tên tiếng Việt", "Giới tính", "Xưng hô / ghi chú"],
                         "Tên nhân vật dịch cố định ở mọi tập; ghi chú xưng hô cho bộ dịch "
                         "(vd. “gọi Tiểu Vũ là em, xưng anh”).", parent)

    def add_row(self, zh: str = "", vi: str = "", gender: str = "", note: str = "") -> None:
        super().add_row(zh, vi, "", note)
        r = self.table.rowCount() - 1
        combo = QComboBox()
        for key, text in GENDERS:
            combo.addItem(text, key)
        combo.setCurrentIndex(max(0, combo.findData(gender)))
        self.table.setCellWidget(r, 2, combo)

    def cells(self) -> list[tuple[str, ...]]:
        out = []
        for r in range(self.table.rowCount()):
            def txt(c: int) -> str:
                it = self.table.item(r, c)
                return it.text() if it else ""
            combo = self.table.cellWidget(r, 2)
            out.append((txt(0), txt(1), combo.currentData() if combo else "", txt(3)))
        return out


class SeriesDialog(QDialog):
    def __init__(self, profile: series.Profile, parent=None) -> None:
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle(f"Bộ phim 《{profile.name}》")
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        self.brief = QPlainTextEdit(profile.brief)
        self.brief.setPlaceholderText(
            "Vài dòng cho bộ dịch đọc ở MỌI tập: thể loại, bối cảnh, giọng điệu, ai gọi ai là gì…")
        w = QWidget()
        bl = QVBoxLayout(w)
        bl.setContentsMargins(0, 8, 0, 0)
        bl.addWidget(self.brief)
        tabs.addTab(w, "Tóm tắt")

        self.chars = _Characters()
        for c in profile.characters:
            self.chars.add_row(str(c.get("zh", "")), str(c.get("vi", "")),
                               str(c.get("gender", "")), str(c.get("note", "")))
        tabs.addTab(self.chars, "Nhân vật")

        self.gloss = _Table(["Tiếng Trung", "Tiếng Việt"],
                            "Thuật ngữ dùng chung cả bộ phim. Bảng của riêng từng video (nút "
                            "Thuật ngữ ở màn Sửa) vẫn ghi đè.")
        for k, v in sorted(profile.glossary.items()):
            self.gloss.add_row(k, v)
        tabs.addTab(self.gloss, "Thuật ngữ")

        self.pron = _Table(["Viết là", "Đọc là"],
                           "Chỉ đổi cách GIỌNG ĐỌC, không đổi phụ đề. Vd. “zombie” → “dôm bi”. "
                           "Từ viết hoa toàn bộ (AI, CEO) chỉ khớp đúng chữ hoa.")
        for k, v in sorted(profile.pronounce.items()):
            self.pron.add_row(k, v)
        tabs.addTab(self.pron, "Cách đọc")

        self.saved = QWidget()
        sl = QVBoxLayout(self.saved)
        sl.setContentsMargins(0, 8, 0, 0)
        self.style_lbl = QLabel()
        self.render_lbl = QLabel()
        for lbl in (self.style_lbl, self.render_lbl):
            lbl.setWordWrap(True)
        self.forget_style = QPushButton("Quên kiểu phụ đề đã lưu")
        self.forget_render = QPushButton("Quên cài đặt dựng đã lưu")
        self.forget_style.clicked.connect(lambda: self._forget("style"))
        self.forget_render.clicked.connect(lambda: self._forget("render"))
        for wdg in (self.style_lbl, self.forget_style, self.render_lbl, self.forget_render):
            sl.addWidget(wdg)
        sl.addWidget(QLabel("Tập đầu tiên được duyệt / dựng sẽ lưu lại kiểu và cài đặt; các tập "
                            "sau tự dùng. Quên đi để tập kế tiếp lưu lại bản mới."))
        sl.addStretch(1)
        tabs.addTab(self.saved, "Đã lưu")
        self._refresh_saved()

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Lưu")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _refresh_saved(self) -> None:
        p = self.profile
        self.style_lbl.setText("Kiểu phụ đề: " + ("đã lưu" if p.style else "chưa có"))
        self.render_lbl.setText("Cài đặt dựng (giọng, tốc độ, chế độ âm thanh, watermark…): "
                                + ("đã lưu" if p.render else "chưa có"))
        self.forget_style.setEnabled(bool(p.style))
        self.forget_render.setEnabled(bool(p.render))

    def _forget(self, which: str) -> None:
        setattr(self.profile, which, {})
        self._refresh_saved()

    def result_profile(self) -> series.Profile:
        return apply_rows(self.profile, brief=self.brief.toPlainText(),
                          characters=self.chars.cells(), glossary=self.gloss.cells(),
                          pronounce=self.pron.cells())

    def _save(self) -> None:
        series.save(self.result_profile())
        self.accept()
