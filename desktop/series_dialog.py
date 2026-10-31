"""Series editor ("Bộ phim"): what one series keeps from episode to episode, as plain
tables — no JSON. Tabs: summary (brief), characters (zh / vi / gender / xưng hô note),
glossary (zh → vi), pronunciation (written → spoken), saved settings (forget the stored
subtitle style / render settings so the next episode learns them again), and "Theo dõi"
(the creator pages checked every N hours for new episodes — run to the edit gate, never
approved; "Kiểm tra ngay" is a dry look on a background thread).

The conversion between the tables and the profile is pure (``rows_to_map``,
``rows_to_characters``, ``apply_rows``, ``parse_sources``, ``apply_watch``,
``format_preview``) and tested without a window; the dialog only shows them. All file I/O
goes through ``app.pipeline.series`` / ``app.pipeline.watch``.
"""
from __future__ import annotations

import re
import time

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import paths  # noqa: F401  (puts backend on sys.path)
from app.pipeline import series  # noqa: E402

GENDERS = (("", "—"), ("nam", "Nam"), ("nữ", "Nữ"))
_URL = re.compile(r"https?://[^\s，,]+")


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


def parse_sources(text: str) -> list[str]:
    """The links in the "Trang theo dõi" box, in order, once each — a pasted share text
    ("长按复制… https://v.douyin.com/… 打开抖音") gives its link; other words are ignored."""
    out: list[str] = []
    for u in _URL.findall(text or ""):
        u = u.rstrip(".,;)）】」")
        if u not in out:
            out.append(u)
    return out


def apply_watch(p: series.Profile, *, on: bool, sources: list[str], every_h: int) -> series.Profile:
    """The profile with its watch settings replaced; other autopilot keys are kept."""
    p.autopilot = series.normalize_autopilot({**p.autopilot, "on": on, "sources": sources,
                                              "every_h": every_h})
    return p


def _when(t) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(t)) if t else ""


def format_preview(res: dict, *, name: str, cap: int) -> str:
    """What a dry check found, for the owner: each page (videos read / its error), then the
    episodes the next check would fetch (at most ``cap`` now, the rest later)."""
    lines = []
    for s in res.get("sources") or []:
        lines.append(f"✗ {s['url']}: {s['error']}" if s.get("error")
                     else f"✓ {s['url']}: đọc được {s['n']} video")
    new = res.get("new") or []
    last = res.get("last")
    if not new:
        lines.append(f"Không có tập mới của 《{name}》"
                     + (f" (thư viện đã có tới tập {last})." if last is not None else "."))
        return "\n".join(lines)
    lines.append(f"Sẽ lấy {min(len(new), cap)} tập của 《{name}》 (tải → phụ đề → dịch, dừng ở "
                 f"bước duyệt):")
    for i, v in enumerate(new):
        when = _when(v.get("published"))
        later = "  — lần kiểm tra sau" if i >= cap else ""
        lines.append(f"  • Tập {v['episode']}: {v.get('title', '')[:60]}"
                     + (f" ({when})" if when else "") + later)
    return "\n".join(lines)


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
        self._bg = None                       # BgRunner, made on the first background call
        tabs.addTab(self._watch_tab(), "Theo dõi")
        self.tabs = tabs

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

    # --- "Theo dõi tập mới" ---------------------------------------------------------------
    def _watch_tab(self) -> QWidget:
        from app.pipeline import watch

        ap = series.autopilot_of(self.profile)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        self.watch_on = QCheckBox("Tự lấy tập mới của bộ phim này")
        self.watch_on.setChecked(ap["on"])
        v.addWidget(self.watch_on)
        hint = QLabel(f"vizsup xem các trang dưới đây theo lịch. Video có 《{self.profile.name}》 "
                      "và số tập sau tập mới nhất đã có sẽ được tải, làm phụ đề và dịch tới bước "
                      "duyệt — không tự duyệt, không tự dựng. Mỗi lần lấy tối đa "
                      f"{ap['max_per_check']} tập.")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addWidget(QLabel("Trang theo dõi (mỗi dòng một link):"))
        self.watch_sources = QPlainTextEdit("\n".join(ap["sources"]))
        self.watch_sources.setPlaceholderText("https://www.douyin.com/user/…\n"
                                              "https://space.bilibili.com/…")
        self.watch_sources.setMaximumHeight(90)
        v.addWidget(self.watch_sources)
        row = QHBoxLayout()
        row.addWidget(QLabel("Kiểm tra mỗi"))
        self.watch_every = QSpinBox()
        self.watch_every.setRange(1, 168)
        self.watch_every.setSuffix(" giờ")
        self.watch_every.setValue(ap["every_h"])
        row.addWidget(self.watch_every)
        row.addStretch(1)
        v.addLayout(row)
        last = watch.last_checked(self.profile.slug)
        self.watch_last = QLabel("Lần kiểm tra trước: " + (_when(last) if last else "chưa có"))
        v.addWidget(self.watch_last)
        row = QHBoxLayout()
        self.watch_check = QPushButton("Kiểm tra ngay")
        self.watch_check.setToolTip("Chỉ xem trang sẽ lấy những tập nào — chưa tải gì.")
        self.watch_check.clicked.connect(self._check_now)
        row.addWidget(self.watch_check)
        self.watch_status = QLabel("")
        self.watch_status.setWordWrap(True)
        row.addWidget(self.watch_status, 1)
        v.addLayout(row)
        self.watch_result = QPlainTextEdit()
        self.watch_result.setReadOnly(True)
        self.watch_result.setVisible(False)
        v.addWidget(self.watch_result, 1)
        v.addStretch(0)
        return w

    def _runner(self):
        if self._bg is None:
            from desktop.ui_thread import BgRunner

            self._bg = BgRunner(self)
        return self._bg

    def _check_now(self) -> None:
        """A dry look with the links as typed (not saved yet) — on a daemon thread; the
        widgets are only touched back on the GUI thread (BgRunner)."""
        from app.pipeline import watch

        sources = parse_sources(self.watch_sources.toPlainText())
        if not sources:
            self.watch_status.setText("Chưa có link trang nào.")
            return
        self.watch_check.setEnabled(False)
        self.watch_status.setText("Đang đọc trang… (Douyin mất khoảng 10–30 giây mỗi trang)")
        prof = series.Profile(slug=self.profile.slug, name=self.profile.name,
                              match=self.profile.match)
        self._runner().run(lambda: watch.find_new(prof, sources), self._checked,
                           self._check_failed)

    def _checked(self, res: dict) -> None:
        self.watch_check.setEnabled(True)
        cap = series.autopilot_of(self.profile)["max_per_check"]
        self.watch_status.setText("Xong — chưa tải gì.")
        self.watch_result.setPlainText(format_preview(res, name=self.profile.name, cap=cap))
        self.watch_result.setVisible(True)

    def _check_failed(self, msg: str) -> None:
        self.watch_check.setEnabled(True)
        self.watch_status.setText(f"✗ {msg}")

    def done(self, r: int) -> None:  # noqa: D401 - QDialog API: drop results of running checks
        if self._bg is not None:
            self._bg.shutdown()
        super().done(r)

    def result_profile(self) -> series.Profile:
        p = apply_rows(self.profile, brief=self.brief.toPlainText(),
                       characters=self.chars.cells(), glossary=self.gloss.cells(),
                       pronounce=self.pron.cells())
        return apply_watch(p, on=self.watch_on.isChecked(),
                           sources=parse_sources(self.watch_sources.toPlainText()),
                           every_h=self.watch_every.value())

    def _save(self) -> None:
        series.save(self.result_profile())
        self.accept()
