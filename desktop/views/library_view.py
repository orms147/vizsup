"""Thư viện — every project on this machine in one list: watch it, open it, see what
it weighs on disk and clean up (move to the Recycle Bin, or keep the subtitles and drop
only the heavy media).

A split long video is ONE row (its parts + the parent folder go together). Sizes and
thumbnails come from background passes (desktop/ui_thread.py BgRunner) — a folder can
hold gigabytes. Deleting never touches a project the main window is using: it hands
the view a guard (``set_guard``) that names why an item is busy."""
from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.icons import icon, pixmap
from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import badge, button, icon_button, icon_label, label
from desktop.widgets.dialogs import VzDialog
from desktop.widgets.player_dialog import PlayerDialog, open_external
from desktop.widgets.recent_projects import duration_label, short_title

THUMB_W, THUMB_H = 40, 56
W_CHECK, W_DUR, W_STATUS, W_DATE, W_SIZE, W_ACT = 20, 76, 128, 104, 88, 236

STATUS = {"rendered": ("Đã dựng", "ok"), "approved": ("Đã duyệt", "ok"),
          "compiled": ("Đã ghép", "ok"), "stale": ("Cần dựng lại", "warn"),
          "review": ("Chờ duyệt", "purple"), "unfinished": ("Dở dang", "warn")}

# the status chips over the list: key → (label, tooltip)
CHIPS = (("all", "Tất cả", "Mọi dự án"),
         ("unapproved", "Chưa duyệt", "Có phụ đề nhưng bạn chưa duyệt (hoặc đã sửa sau khi duyệt)"),
         ("approved", "Đã duyệt", "Đã duyệt, chưa dựng video"),
         ("rendered", "Đã dựng", "Video đã dựng xong (kể cả video ghép)"),
         ("stale", "Cần dựng lại", "Duyệt lại sau lần dựng cuối — video chưa khớp phụ đề"),
         ("unfinished", "Dở dang", "Chưa chạy xong tới bước phụ đề"))


# --- small pure helpers -------------------------------------------------------------

def fmt_size(n: int | None) -> str:
    """Bytes → "3,2 GB" / "850 MB" / "12 KB" (Vietnamese decimal comma)."""
    if n is None:
        return "…"
    n = int(n)
    if n < 1024:
        return f"{n} B"
    for unit, div in (("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3), ("TB", 1024 ** 4)):
        v = n / div
        if v < 1024 or unit == "TB":
            text = f"{v:.0f}" if (unit == "KB" or v >= 100) else f"{v:.1f}"
            return f"{text.replace('.', ',')} {unit}"
    return f"{n} B"


def date_label(mtime: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    lt, nt = time.localtime(mtime), time.localtime(now)
    if (lt.tm_year, lt.tm_yday) == (nt.tm_year, nt.tm_yday):
        return "Hôm nay " + time.strftime("%H:%M", lt)
    if 0 < now - mtime < 2 * 86400 and time.localtime(now - 86400).tm_yday == lt.tm_yday:
        return "Hôm qua " + time.strftime("%H:%M", lt)
    return time.strftime("%d/%m/%Y", lt)


def fold(text: str) -> str:
    """Lower-case, accents dropped ("Đà Lạt" → "da lat") — search typed without dấu."""
    t = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def item_status(item: dict, details: dict | None) -> str:
    st = item.get("status") or "unfinished"
    if st == "review" and details and details.get("approved"):
        return "approved"
    if st == "rendered" and details and details.get("stale"):
        return "stale"
    return st


def item_category(item: dict, details: dict | None) -> str:
    """Which status chip an item belongs to: unapproved | approved | rendered | stale |
    unfinished. Pure."""
    st = item_status(item, details)
    return {"review": "unapproved", "compiled": "rendered"}.get(st, st)


def shorts_under_parents(items: list[dict]) -> list[dict]:
    """A Short (made by «Cắt Short») listed right below its episode, whatever the sort; one whose
    episode is not in the list keeps its place. Stable. Pure."""
    ids = {it["id"] for it in items}
    kids: dict[str, list[dict]] = {}
    for it in items:
        parent = (it.get("short") or {}).get("parent")
        if parent and parent in ids and parent != it["id"]:
            kids.setdefault(parent, []).append(it)
    placed = {k["id"] for ks in kids.values() for k in ks}
    out: list[dict] = []
    for it in items:
        if it["id"] in placed:
            continue
        out.append(it)
        out.extend(kids.get(it["id"], []))
    return out


def episode_sort_key(item: dict) -> tuple:
    """By series (《name》), then episode number; unnumbered ones after the numbered of their
    series, newest first. Pure."""
    from app.pipeline import compilation

    title = item.get("title") or ""
    number = compilation.episode_number(title)
    return (compilation.series_key(title) or fold(title), number is None, number or 0,
            -(item.get("mtime") or 0.0))


# --- widgets ------------------------------------------------------------------------

class _ElideLabel(QLabel):
    """A label that shows as much of its text as fits, then "…" (full text in tooltip)."""

    def __init__(self, text: str, role: str = "") -> None:
        super().__init__()
        if role:
            self.setObjectName(role)
        self._full = text
        self.setToolTip(text)
        self.setMinimumWidth(60)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, self.width()))


def _thumb_pixmap(path: str | None, playable: bool) -> QPixmap | None:
    """A cropped thumbnail with a small play disc when the project has a video."""
    if not path:
        return None
    pm = QPixmap(path)
    if pm.isNull():
        return None
    pm = pm.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    pm = pm.copy(max(0, (pm.width() - THUMB_W) // 2), max(0, (pm.height() - THUMB_H) // 2),
                 THUMB_W, THUMB_H)
    if playable:
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 17, 22, 190))
        r = QRectF((THUMB_W - 20) / 2, (THUMB_H - 20) / 2, 20, 20)
        p.drawEllipse(r)
        p.drawPixmap(int(r.x()) + 5, int(r.y()) + 5, pixmap("Play", "#ffffff", 10))
        p.end()
    return pm


class _Row(QFrame):
    """One project: ☐ · thumb · title + files · duration · status · date · size · actions."""

    toggled = Signal(str, bool)
    openRequested = Signal(str)
    watchRequested = Signal(str)
    folderRequested = Signal(str)
    deleteRequested = Signal(str)
    cleanRequested = Signal(str)
    externalRequested = Signal(str)
    chaptersRequested = Signal(str)
    shortRequested = Signal(str)           # «Cắt Short…» on this approved episode
    fastApproveRequested = Signal(str)     # «Duyệt nhanh…» (autopilot) on an unapproved episode

    def __init__(self, item: dict, busy: str | None) -> None:
        super().__init__()
        self.item = item
        self.busy = busy
        iid = item["id"]
        self.setObjectName("libRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame#libRow{{background:transparent;border:none;border-bottom:1px solid "
            f"{V2['line_soft']};border-left:3px solid transparent;border-radius:0;}}"
            f"QFrame#libRow:hover{{background:{V2['raised']};}}"
            f"QFrame#libRow[cur=\"true\"]{{border-left-color:{V2['purple_bright']};"
            f"background:{V2['raised']};}}"
            f"QFrame#libRow[picked=\"true\"]{{background:{V2['purple_soft']};}}")
        self._playable = bool(item.get("has_output") or item.get("has_preview")
                              or item.get("has_source"))
        self.setToolTip("Bấm để xem video" if self._playable else "Chưa có video trên máy")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 9, 14, 9)
        h.setSpacing(14)
        self.check = QCheckBox()
        self.check.setFixedWidth(W_CHECK)
        self.check.setEnabled(busy is None)
        self.check.setToolTip(f"Không chọn được — {busy}" if busy else "Chọn để xóa")
        self.check.toggled.connect(self._mark)
        self.check.toggled.connect(lambda on: self.toggled.emit(iid, on))
        h.addWidget(self.check)
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setPixmap(pixmap("FileVideo", V2["muted"], 16))
        self.thumb.setStyleSheet(f"background:{V2['input']};border-radius:5px;")
        h.addWidget(self.thumb)

        col = QVBoxLayout()
        col.setSpacing(5)
        title = item.get("title") or iid
        if title == iid and item.get("url"):
            title = str(item["url"])                 # a run that failed before the download
        self.title = _ElideLabel(short_title(title), "h3")
        self.title.setToolTip(title)
        col.addWidget(self.title)
        # the chips may be cut at the right on a narrow window — never the action buttons
        # (their widths would push the row past the list and hide "···")
        chip_box = QWidget()
        chip_box.setObjectName("bare")
        chip_box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        chips = QHBoxLayout(chip_box)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(5)
        if busy:
            chips.addWidget(badge(busy[:1].upper() + busy[1:], "purple"))
        if item.get("short"):
            sh = item["short"]
            chips.addWidget(badge("Short" + (f" · tập {sh['episode']}" if sh.get("episode") else ""),
                                  "purple"))
        if item.get("kind") == "group":
            chips.addWidget(badge(f"Video dài · {item.get('total') or len(item['parts'])} phần"))
        compiled = item.get("kind") == "compilation"
        if compiled:
            chips.addWidget(badge(f"Video ghép · {item.get('episodes') or '?'} tập", "purple"))
        files = [("has_source", "Video gốc"), ("has_output", "Bản dựng"),
                 ("has_preview", "Xem nhanh")]
        shown = [name for key, name in files if item.get(key)]
        for name in shown if not compiled else []:
            chips.addWidget(badge(name))
        if not shown:
            chips.addWidget(label("Chưa có video trên máy", "small"))
        plat = {"douyin": "Douyin", "bilibili": "Bilibili", "local": "Tệp trên máy"}.get(
            item.get("platform") or "", "")
        if plat:
            chips.addSpacing(4)
            chips.addWidget(label(plat, "small"))
        chips.addStretch(1)
        for k in range(chips.count()):
            # a narrow column cuts whole chips off at the right; squeezing them to
            # their minimum showed "Video ç" / "Bản dự" (2026-10-29)
            w = chips.itemAt(k).widget()
            if w is not None:
                w.setMinimumWidth(w.sizeHint().width())
        col.addWidget(chip_box)
        h.addLayout(col, 1)

        dur = label(duration_label(item["duration"]) if item.get("duration") else "—", "mono")
        dur.setFixedWidth(W_DUR)
        h.addWidget(dur)
        self.dur_lbl = dur
        st = QWidget()
        st.setObjectName("bare")
        st.setFixedWidth(W_STATUS)
        sl = QHBoxLayout(st)
        sl.setContentsMargins(0, 0, 0, 0)
        self.status = badge("")
        sl.addWidget(self.status)
        sl.addStretch(1)
        h.addWidget(st)
        self.set_status(item_status(item, None))
        when = label(date_label(item.get("mtime") or 0), "small")
        when.setFixedWidth(W_DATE)
        h.addWidget(when)
        self.size_lbl = label("…", "monoText")
        self.size_lbl.setFixedWidth(W_SIZE)
        self.size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(self.size_lbl)

        acts = QWidget()
        acts.setObjectName("bare")
        acts.setFixedWidth(W_ACT)
        al = QHBoxLayout(acts)
        al.setContentsMargins(8, 0, 0, 0)
        al.setSpacing(4)
        self.open_btn = button("Thêm tập" if compiled else "Sửa", small=True,   # "Mở" read as "play"
                               tip="Ghép video này với các tập mới thành một video dài hơn"
                               if compiled else "Mở để sửa phụ đề / dựng video (Enter)")
        self.open_btn.clicked.connect(lambda: self.openRequested.emit(iid))
        if compiled:
            self.open_btn.setMinimumWidth(84)    # the small-button hint cut "Thêm tập" at 150 %
        self.watch_btn = button("Xem", "Play", small=True, icon_size=13,
                                tip="Xem video trong ứng dụng")
        self.watch_btn.setEnabled(self._playable)
        self.watch_btn.clicked.connect(lambda: self.watchRequested.emit(iid))
        folder = icon_button("FolderOpen", "Mở thư mục dự án")
        folder.clicked.connect(lambda: self.folderRequested.emit(iid))
        more = icon_button("MoreHorizontal", "Thêm thao tác")
        m = QMenu(more)
        ext = m.addAction("Xem bằng trình phát của máy",
                          lambda: self.externalRequested.emit(iid))
        ext.setEnabled(self._playable)
        if compiled:
            ch = m.addAction("Sao chép mục lục chương (dán vào mô tả YouTube)",
                             lambda: self.chaptersRequested.emit(iid))
            ch.setEnabled(bool(item.get("chapters")))
        self.short_act = None
        self.fast_act = None
        if item.get("kind") == "job":
            self.fast_act = m.addAction("Duyệt nhanh (rồi tự dựng, tự đăng)…",
                                        lambda: self.fastApproveRequested.emit(iid))
            self.fast_act.setEnabled(False)          # until the details say "not approved"
            self.short_act = m.addAction("Cắt Short (30–60 giây, 9:16)…",
                                         lambda: self.shortRequested.emit(iid))
            self._set_short_enabled(None)
        m.addSeparator()
        self.clean_act = m.addAction("Chỉ xóa file nặng…", lambda: self.cleanRequested.emit(iid))
        self.delete_act = m.addAction("Xóa dự án (vào Thùng rác)…",
                                      lambda: self.deleteRequested.emit(iid))
        for a in (self.clean_act, self.delete_act):
            a.setEnabled(busy is None)
        more.setMenu(m)
        more.setPopupMode(more.ToolButtonPopupMode.InstantPopup)
        al.addWidget(self.open_btn)
        al.addWidget(self.watch_btn)
        al.addStretch(1)
        al.addWidget(folder)
        al.addWidget(more)
        h.addWidget(acts)

    def _set_fast_enabled(self, d: dict | None) -> None:
        a = self.fast_act
        if a is not None:
            ok = bool(d) and bool(self.item.get("has_vi")) and not d.get("approved") \
                and not self.item.get("short")
            a.setEnabled(ok)
            a.setToolTip("" if ok else "Chỉ cho tập đã có phụ đề và chưa duyệt.")

    def _set_short_enabled(self, d: dict | None) -> None:
        """«Cắt Short» needs an APPROVED episode with its source video; a Short is not cut again."""
        a = self.short_act
        if a is None:
            return
        if self.item.get("short"):
            why = "Đây đã là một Short — cắt từ tập gốc."
        elif not self.item.get("has_source"):
            why = "Đã dọn video gốc — mở tập để tải lại rồi cắt."
        elif d is None:
            why = "Đang kiểm tra…"
        elif not d.get("approved"):
            why = "Duyệt phụ đề của tập này trước (mở tập → Duyệt và dựng)."
        else:
            why = ""
        a.setEnabled(not why)
        a.setToolTip(why or "Gợi ý 1–3 đoạn 30–60 giây, bạn chọn rồi tạo dự án Short riêng")

    def _mark(self, on: bool) -> None:
        self.setProperty("picked", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_cursor_row(self, on: bool) -> None:
        """The keyboard cursor (↑/↓ in the list) — Enter / Space / Delete act on this row."""
        self.setProperty("cur", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_status(self, st: str) -> None:
        text, kind = STATUS.get(st, STATUS["unfinished"])
        self.status.setText(text)
        self.status.setObjectName({"ok": "badgeOk", "purple": "badgePurple",
                                   "warn": "badgeWarn"}[kind])
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def apply_details(self, d: dict) -> None:
        self.size_lbl.setText(fmt_size(d.get("size")))
        if d.get("duration") and not self.item.get("duration"):
            self.dur_lbl.setText(duration_label(d["duration"]))     # measured (local files)
        heavy = d.get("heavy") or 0
        self.size_lbl.setToolTip(f"File nặng xóa được: {fmt_size(heavy)}" if heavy else
                             "Không còn file nặng nào để dọn")
        self.set_status(item_status(self.item, d))
        self._set_short_enabled(d)
        self._set_fast_enabled(d)

    def apply_thumb(self, path: str | None) -> None:
        pm = _thumb_pixmap(path, self._playable)
        if pm is not None:
            self.thumb.setPixmap(pm)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()) \
                and self._playable:
            self.watchRequested.emit(self.item["id"])
        super().mouseReleaseEvent(ev)


class TrashConfirm(VzDialog):
    """What goes to the Recycle Bin, how big, and that it can be restored."""

    def __init__(self, parent, plan: list[dict], *, heavy_only: bool) -> None:
        n = len(plan)
        total = sum(p["bytes"] for p in plan)
        title = (f"Dọn file nặng của {n} dự án?" if heavy_only else
                 "Xóa dự án này?" if n == 1 else f"Xóa {n} dự án?")
        super().__init__(title, parent, width=520)
        if heavy_only:
            self.body.addWidget(label(
                "Xóa video gốc (tải lại được từ link khi mở lại), bản xem nhanh, giọng lồng và "
                "âm thanh đã dựng (tts, dub), nhạc tách (stems, bg.wav). Giữ lại phụ đề, cài "
                "đặt và bản dựng output.mp4 — mở lại là dựng lại được.", "secondary", wrap=True))
        else:
            self.body.addWidget(label(
                "Toàn bộ thư mục dự án: video, phụ đề, giọng lồng và bản dựng.", "secondary",
                wrap=True))
        box = QFrame()
        box.setObjectName("inset")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(6)
        for p in plan[:8]:
            row = QHBoxLayout()
            row.setSpacing(12)
            t = _ElideLabel(short_title(p["item"].get("title") or p["item"]["id"]))
            row.addWidget(t, 1)
            row.addWidget(label(fmt_size(p["bytes"]), "monoText"))
            bl.addLayout(row)
        if n > 8:
            bl.addWidget(label(f"… và {n - 8} dự án khác", "small"))
        self.body.addWidget(box)
        tot = QHBoxLayout()
        tot.addWidget(label("Giải phóng" if heavy_only else "Tổng", "secondary"))
        tot.addStretch(1)
        tot.addWidget(label(fmt_size(total), "h3"))
        self.body.addLayout(tot)
        note = QHBoxLayout()
        note.setSpacing(8)
        note.addWidget(icon_label("Trash2", V2["muted"], 14), 0, Qt.AlignTop)
        note.addWidget(label("Chuyển vào Thùng rác — khôi phục được; dọn Thùng rác để lấy lại "
                             "dung lượng.", "small", wrap=True), 1)
        self.body.addLayout(note)
        self.add_button("Hủy")
        ok = self.add_button("Dọn file nặng" if heavy_only else "Chuyển vào Thùng rác",
                             icon_name="Trash2", accept=True)
        ok.setObjectName("danger")
        ok.setIcon(icon("Trash2", V2["red"], 15))
        ok.setDefault(False)


# --- the page -----------------------------------------------------------------------

class LibraryView(QWidget):
    openRequested = Signal(str)            # a job id → main window's _resume (recent card path)
    groupResumeRequested = Signal(str)     # a split video with no part ready yet
    closed = Signal()                      # "Quay lại"
    changed = Signal()                     # something went to the Recycle Bin
    message = Signal(str, str)             # status bar text, kind
    compileRequested = Signal(dict)        # CompileDialog.spec() → main window runs the join
    fastApproveRequested = Signal(str)     # «Duyệt nhanh» → the main window approves + autopilot
    compileCancelRequested = Signal()
    batchRequested = Signal(dict)          # BatchDialog.spec() → main window queues the renders
    batchCancelRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._bg = BgRunner(self)
        self._gen = 0
        self._items: list[dict] = []
        self._rows: dict[str, _Row] = {}
        self._details: dict[str, dict] = {}
        self._thumbs: dict[str, str | None] = {}
        self._checked: set[str] = set()
        self._guard: Callable[[dict], str | None] = lambda _it: None
        self._sort = "new"
        self._chip = "all"                    # the status chip filtering the list
        self._cur: str | None = None          # the keyboard cursor row (↑/↓, Enter, Space, Delete)
        self.setFocusPolicy(Qt.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        outer = QHBoxLayout()
        outer.addStretch(1)
        page = QWidget()
        page.setMaximumWidth(1320)
        outer.addWidget(page, 12)
        outer.addStretch(1)
        root.addLayout(outer, 1)
        pv = QVBoxLayout(page)
        pv.setContentsMargins(24, 18, 24, 14)
        pv.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(label("Thư viện", "h1"))
        head.addWidget(label("Mọi dự án trên máy — xem lại, mở tiếp hoặc dọn cho nhẹ ổ đĩa",
                             "secondary"))
        head.addStretch(1)
        self.compose_btn = button("Ghép video", "Merge", small=True,
                                  tip="Nối nhiều tập đã dựng thành một video dài (mỗi tập một "
                                      "chương)")
        self.compose_btn.clicked.connect(lambda: self.compose(self.selected()))
        head.addWidget(self.compose_btn)
        self.auto_btn = button("Tự động đăng", "Clock3", small=True,
                               tip="Lịch tự dựng + tự đăng cho từng bộ phim, và hàng đăng")
        self.auto_btn.clicked.connect(self.open_autopilot)
        head.addWidget(self.auto_btn)
        self.batch_btn = button("Dựng hàng loạt", "MonitorPlay", small=True,
                                tip="Dựng lần lượt các video đã duyệt, chạy nền")
        self.batch_btn.clicked.connect(lambda: self.open_batch(self.selected()))
        head.addWidget(self.batch_btn)
        folder = button("Mở thư mục dự án", "FolderOpen", variant="ghost", small=True)
        folder.clicked.connect(lambda: open_external(store._storage()))
        head.addWidget(folder)
        back = button("Quay lại", "ArrowLeft", variant="ghost")
        back.clicked.connect(self.closed)
        head.addWidget(back)
        pv.addLayout(head)

        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm theo tên video…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedSize(340, 36)
        self.search.setStyleSheet("QLineEdit{padding-left:34px;}")
        glass = icon_label("Search", V2["muted"], 15)
        glass.setParent(self.search)                 # a leading action sat off-centre
        glass.move(12, 10)
        self.search.textChanged.connect(self._apply_filter)
        tools.addWidget(self.search)
        seg = QFrame()
        seg.setObjectName("segmented")
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(3, 3, 3, 3)
        sh.setSpacing(2)
        self._sort_group = QButtonGroup(self)
        self.sort_btns: dict[str, QPushButton] = {}
        for key, text in (("new", "Mới nhất"), ("ep", "Theo tập"), ("size", "Nặng nhất"),
                          ("name", "Tên A–Z")):
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_sort(k))
            self._sort_group.addButton(b)
            self.sort_btns[key] = b
            sh.addWidget(b)
        self.sort_btns["new"].setChecked(True)
        tools.addWidget(seg)
        tools.addStretch(1)
        tools.addWidget(icon_label("HardDrive", V2["secondary"], 15))
        self.total = label("", "secondary")
        tools.addWidget(self.total)
        pv.addLayout(tools)
        pv.addLayout(self._build_chips())
        pv.addWidget(self._build_compile_bar())

        panel = QFrame()
        panel.setObjectName("panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        selbar = QFrame()
        selbar.setObjectName("panelHead")
        sb = QHBoxLayout(selbar)
        sb.setContentsMargins(14, 8, 12, 8)
        sb.setSpacing(10)
        self.check_all = QCheckBox("Chọn tất cả")
        self.check_all.clicked.connect(self._on_check_all)
        sb.addWidget(self.check_all)
        self.sel_label = label("", "small")
        sb.addWidget(self.sel_label)
        sb.addStretch(1)
        self.clean_btn = button("Chỉ xóa file nặng", "HardDrive", variant="ghost", small=True,
                                tip="Giữ phụ đề và cài đặt; xóa video gốc, bản xem nhanh, "
                                    "giọng lồng, nhạc tách")
        self.clean_btn.clicked.connect(lambda: self.delete_items(self.selected(), heavy_only=True))
        self.compile_btn = button("Ghép thành 1 video", "Merge", variant="ghost", small=True,
                                  tip="Nối các video đã chọn (đã dựng) thành một video dài")
        self.compile_btn.clicked.connect(lambda: self.compose(self.selected()))
        sb.addWidget(self.compile_btn)
        sb.addWidget(self.clean_btn)
        self.delete_btn = button("Xóa dự án đã chọn", "Trash2", variant="danger", small=True)
        self.delete_btn.clicked.connect(lambda: self.delete_items(self.selected()))
        sb.addWidget(self.delete_btn)
        pl.addWidget(selbar)

        cols = QHBoxLayout()
        cols.setContentsMargins(14, 7, 14, 7)
        cols.setSpacing(14)
        cols.addSpacing(W_CHECK + 14 + THUMB_W)
        for text, w, align in (("Video", 0, Qt.AlignLeft), ("Thời lượng", W_DUR, Qt.AlignLeft),
                               ("Trạng thái", W_STATUS, Qt.AlignLeft),
                               ("Cập nhật", W_DATE, Qt.AlignLeft),
                               ("Dung lượng", W_SIZE, Qt.AlignRight), ("", W_ACT, Qt.AlignLeft)):
            c = label(text, "section")
            c.setAlignment(align | Qt.AlignVCenter)
            if w:
                c.setFixedWidth(w)
                cols.addWidget(c)
            else:
                cols.addWidget(c, 1)
        colw = QFrame()
        colw.setStyleSheet(f"QFrame{{border-bottom:1px solid {V2['line_soft']};}}"
                           f"QLabel{{border:none;}}")
        colw.setLayout(cols)
        pl.addWidget(colw)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("bare")
        self._list = QVBoxLayout(holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        self._list.addStretch(1)
        scroll.setWidget(holder)
        self._scroll = scroll
        pl.addWidget(scroll, 1)
        self.empty = label("", "secondary")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setContentsMargins(0, 40, 0, 40)
        pl.addWidget(self.empty)
        pv.addWidget(panel, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addWidget(icon_label("Trash2", V2["muted"], 13))
        foot.addWidget(label("Xóa = chuyển vào Thùng rác của Windows (khôi phục được). Dọn Thùng "
                             "rác để lấy lại dung lượng. Dự án đang mở hoặc đang chạy không xóa "
                             "được.", "small"), 1)
        pv.addLayout(foot)
        pv.addWidget(label("↑ ↓ chọn dự án · Enter mở · Space xem · Delete xóa · Ctrl+A chọn tất cả "
                           "· Ctrl+F tìm · Esc quay lại", "small"))
        self._update_selection()

    # --- status chips -----------------------------------------------------------------
    def _build_chips(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._chip_group = QButtonGroup(self)
        self.chip_btns: dict[str, QPushButton] = {}
        for key, text, tip in CHIPS:
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton#chip{{background:transparent;border:1px solid {V2['line']};"
                f"border-radius:13px;padding:3px 12px;color:{V2['secondary']};min-height:0;}}"
                f"QPushButton#chip:hover{{border-color:{V2['purple_line']};}}"
                f"QPushButton#chip:checked{{background:{V2['purple_soft']};"
                f"border-color:{V2['purple_line']};color:{V2['purple_bright']};}}"
                f"QPushButton#chip:disabled{{color:{V2['dim']};border-color:{V2['line_soft']};}}")
            b.clicked.connect(lambda _=False, k=key: self.set_chip(k))
            self._chip_group.addButton(b)
            self.chip_btns[key] = b
            row.addWidget(b)
        self.chip_btns["all"].setChecked(True)
        row.addStretch(1)
        self._update_chips()
        return row

    def chip(self) -> str:
        return self._chip

    def set_chip(self, key: str) -> None:
        if key not in self.chip_btns:
            return
        self._chip = key
        self.chip_btns[key].setChecked(True)
        self._apply_filter()

    def _category(self, it: dict) -> str:
        return item_category(it, self._details.get(it["id"]))

    def chip_counts(self) -> dict[str, int]:
        counts = {key: 0 for key, _t, _tip in CHIPS}
        for it in self._items:
            counts["all"] += 1
            counts[self._category(it)] = counts.get(self._category(it), 0) + 1
        return counts

    def _update_chips(self) -> None:
        counts = self.chip_counts()
        for key, text, _tip in CHIPS:
            b = self.chip_btns[key]
            n = counts.get(key, 0)
            b.setText(f"{text} {n}" if key != "all" or n else text)
            b.setEnabled(key == "all" or n > 0 or key == self._chip)
            if key == "unfinished":
                b.setVisible(n > 0 or key == self._chip)

    # --- data -------------------------------------------------------------------------
    def set_guard(self, fn: Callable[[dict], str | None]) -> None:
        """``fn(item)`` → why the item can't be deleted now ("đang mở" …) or None."""
        self._guard = fn

    def refresh(self) -> None:
        self._gen += 1
        gen = self._gen
        self._items = store.library_items()
        ids = {it["id"] for it in self._items}
        self._checked &= ids
        self._details = {k: v for k, v in self._details.items() if k in ids}
        for row in self._rows.values():
            self._list.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        if self._cur not in ids:
            self._cur = None
        for it in self._items:
            busy = self._guard(it)
            if busy:
                self._checked.discard(it["id"])
            row = _Row(it, busy)
            row.check.setChecked(it["id"] in self._checked)
            row.toggled.connect(self._on_toggled)
            row.openRequested.connect(self._open)
            row.watchRequested.connect(self._watch)
            row.folderRequested.connect(self._folder)
            row.externalRequested.connect(self._external)
            row.chaptersRequested.connect(self._copy_chapters)
            row.shortRequested.connect(self._cut_short)
            row.fastApproveRequested.connect(self.fastApproveRequested)
            row.deleteRequested.connect(lambda iid: self.delete_items([self._item(iid)]))
            row.cleanRequested.connect(
                lambda iid: self.delete_items([self._item(iid)], heavy_only=True))
            if it["id"] in self._details:
                row.apply_details(self._details[it["id"]])
            if self._thumbs.get(it["id"]):
                row.apply_thumb(self._thumbs[it["id"]])
            self._rows[it["id"]] = row
        self._relayout()
        self._update_total()
        self._update_selection()
        self._update_chips()
        if self._cur in self._rows:
            self._rows[self._cur].set_cursor_row(True)
        items = list(self._items)
        if items:
            self._bg.run(lambda: store.library_details(items),
                         lambda r, g=gen: self._apply_details(g, r), lambda _m: None)
            self._bg.run(lambda: store.library_thumbs(items),
                         lambda r, g=gen: self._apply_thumbs(g, r), lambda _m: None)

    def _apply_details(self, gen: int, result: dict) -> None:
        self._details.update(result)
        if gen != self._gen:
            return
        for iid, d in result.items():
            if iid in self._rows:
                self._rows[iid].apply_details(d)
        if self._sort == "size":
            self._relayout()
        self._update_total()
        self._update_chips()
        if self._chip != "all":
            self._apply_filter()          # a project can move between chips once its details land
        self._update_selection()

    def _apply_thumbs(self, gen: int, result: dict) -> None:
        self._thumbs.update(result)
        if gen != self._gen:
            return
        for iid, path in result.items():
            if iid in self._rows:
                self._rows[iid].apply_thumb(path)

    def _item(self, iid: str) -> dict:
        return next(it for it in self._items if it["id"] == iid)

    def items(self) -> list[dict]:
        return list(self._items)

    # --- order / filter ---------------------------------------------------------------
    def set_sort(self, key: str) -> None:
        self._sort = key
        self.sort_btns[key].setChecked(True)
        self._relayout()

    def _ordered(self) -> list[dict]:
        items = list(self._items)                         # already newest first
        if self._sort == "size":
            items.sort(key=lambda it: (self._details.get(it["id"]) or {}).get("size") or 0,
                       reverse=True)
        elif self._sort == "name":
            items.sort(key=lambda it: fold(short_title(it.get("title") or it["id"])))
        elif self._sort == "ep":
            items.sort(key=episode_sort_key)
        return shorts_under_parents(items)

    def order(self) -> list[str]:
        """Item ids top to bottom as shown (filtered rows included, hidden)."""
        return [self._list.itemAt(i).widget().item["id"] for i in range(self._list.count())
                if self._list.itemAt(i).widget() is not None]

    def _relayout(self) -> None:
        for row in self._rows.values():
            self._list.removeWidget(row)
        for k, it in enumerate(self._ordered()):
            self._list.insertWidget(k, self._rows[it["id"]])
        self._apply_filter()

    def _matches(self, it: dict, q: str) -> bool:
        if not q:
            return True
        hay = fold(" ".join(str(x) for x in (it.get("title"), it["id"], it.get("url")) if x))
        return all(w in hay for w in q.split())

    def _apply_filter(self, *_a) -> None:
        q = fold(self.search.text().strip())
        shown = 0
        for it in self._items:
            ok = self._matches(it, q) and (self._chip == "all"
                                           or self._category(it) == self._chip)
            self._rows[it["id"]].setVisible(ok)
            shown += ok
        self.empty.setVisible(shown == 0)
        if not self._items:
            self.empty.setText("Chưa có dự án nào — dán link ở màn hình Bắt đầu.")
        elif q:
            self.empty.setText(f"Không có dự án nào khớp “{self.search.text().strip()}”.")
        else:
            self.empty.setText("Không có dự án nào ở trạng thái này.")
        self._update_selection()

    def visible_ids(self) -> list[str]:
        return [iid for iid in self.order() if not self._rows[iid].isHidden()]

    # --- selection --------------------------------------------------------------------
    def _on_toggled(self, iid: str, on: bool) -> None:
        if on:
            self._checked.add(iid)
        else:
            self._checked.discard(iid)
        self._update_selection()

    def _on_check_all(self, on: bool) -> None:
        for iid in self.visible_ids():
            row = self._rows[iid]
            if row.check.isEnabled():
                row.check.setChecked(on)
        self._update_selection()

    def selected(self) -> list[dict]:
        """The ticked rows that are SHOWING: a chip or the search can hide a ticked row, and
        Xóa / Chỉ xóa file nặng / Ghép / Dựng hàng loạt must not act on what the user can't see."""
        return [it for it in self._items if it["id"] in self._checked
                and not self._rows[it["id"]].isHidden()]

    def hidden_checked(self) -> int:
        return sum(1 for it in self._items if it["id"] in self._checked
                   and self._rows[it["id"]].isHidden())

    def _update_selection(self) -> None:
        sel = self.selected()
        n = len(sel)
        size = sum((self._details.get(it["id"]) or {}).get("size") or 0 for it in sel)
        heavy = sum((self._details.get(it["id"]) or {}).get("heavy") or 0 for it in sel)
        known = all(it["id"] in self._details for it in sel)
        hid = self.hidden_checked()
        tail = f" · {hid} đã tick đang bị bộ lọc ẩn (không bị xóa)" if hid else ""
        self.sel_label.setText(
            ("" if not n else f"Đã chọn {n} · {fmt_size(size) if known else '…'}") + tail)
        self.delete_btn.setEnabled(n > 0)
        self.compile_btn.setEnabled(n > 0)
        self.compile_btn.setText(f"Ghép {n} video thành 1" if n > 1 else "Ghép thành 1 video")
        self.delete_btn.setText(f"Xóa {n} dự án đã chọn" if n else "Xóa dự án đã chọn")
        self.clean_btn.setEnabled(n > 0 and (heavy > 0 or not known))
        self.clean_btn.setText(f"Chỉ xóa file nặng · {fmt_size(heavy)}" if n and known and heavy
                               else "Chỉ xóa file nặng")
        vis = [i for i in self.visible_ids() if self._rows[i].check.isEnabled()]
        self.check_all.setChecked(bool(vis) and all(i in self._checked for i in vis))

    def _update_total(self) -> None:
        n = len(self._items)
        if not n:
            self.total.setText("Chưa có dự án")
            return
        if all(it["id"] in self._details for it in self._items):
            size = sum(self._details[it["id"]]["size"] for it in self._items)
            self.total.setText(f"Tổng: {fmt_size(size)} · {n} dự án")
        else:
            self.total.setText(f"Tổng: đang tính… · {n} dự án")

    # --- keyboard ---------------------------------------------------------------------
    def cursor_id(self) -> str | None:
        return self._cur

    def set_cursor(self, iid: str | None) -> None:
        if self._cur in self._rows:
            self._rows[self._cur].set_cursor_row(False)
        self._cur = iid if iid in self._rows else None
        if self._cur is not None:
            row = self._rows[self._cur]
            row.set_cursor_row(True)
            self._scroll.ensureWidgetVisible(row)

    def move_cursor(self, step: int) -> None:
        ids = self.visible_ids()
        if not ids:
            return
        at = ids.index(self._cur) if self._cur in ids else (-1 if step > 0 else len(ids))
        self.set_cursor(ids[max(0, min(len(ids) - 1, at + step))])

    def _cursor_or_first(self) -> str | None:
        ids = self.visible_ids()
        return self._cur if self._cur in ids else (ids[0] if ids else None)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        """Open / watch / delete without the mouse. Space, Delete and Ctrl+A stay text
        editing while the search box has the focus; ↑/↓ and Enter work from there too
        (type a name, ↓, Enter)."""
        k, mods = ev.key(), ev.modifiers()
        typing = isinstance(QApplication.focusWidget(), QLineEdit)
        if k in (Qt.Key_Up, Qt.Key_Down) and not mods:
            self.move_cursor(-1 if k == Qt.Key_Up else 1)
        elif k in (Qt.Key_Return, Qt.Key_Enter) and not mods:
            iid = self._cursor_or_first()
            if iid is None:
                return
            self.set_cursor(iid)
            self._open(iid)
        elif k == Qt.Key_Space and not mods and not typing:
            iid = self._cursor_or_first()
            if iid is not None:
                self.set_cursor(iid)
                self._watch(iid)
        elif k == Qt.Key_Delete and not mods and not typing:
            items = self.selected() or ([self._item(self._cur)] if self._cur else [])
            self.delete_items(items)
        elif k == Qt.Key_A and mods == Qt.ControlModifier and not typing:
            self.check_all.setChecked(True)
            self._on_check_all(True)
        elif k == Qt.Key_F and mods == Qt.ControlModifier:
            self.search.setFocus()
            self.search.selectAll()
        else:
            super().keyPressEvent(ev)
            return
        ev.accept()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        fw = QApplication.focusWidget()
        if fw is None or not self.isAncestorOf(fw):
            self.setFocus(Qt.OtherFocusReason)

    # --- «Cắt Short» (app/pipeline/clipshort.py) ---------------------------------------------
    def open_autopilot(self, slug: str | None = None) -> None:
        from desktop.widgets.autopilot_dialogs import AutopilotDialog

        dlg = AutopilotDialog(self.window(), slug=slug)
        if self._run_dialog(dlg):
            dlg.save()
            self.message.emit("Đã lưu lịch tự động đăng.", "ok")

    def _cut_short(self, iid: str) -> None:
        from app.models import Job
        from app.pipeline import clipshort
        from app.pipeline.srt import parse_srt
        from desktop.widgets.short_dialog import ShortDialog

        it = self._item(iid)
        work = Path(it["dirs"][0])
        job = Job(id=work.name, work_dir=work)
        try:
            cues = parse_srt(job.vi_srt)
        except OSError:
            cues = []
        title = it.get("title") or iid
        dlg = ShortDialog(self.window(), short_title(title), cues, it.get("duration") or 0.0,
                          clipshort.propose(cues))
        dlg.aiRequested.connect(lambda: self._short_ai(dlg, cues, title))
        if not self._run_dialog(dlg):
            return
        spec = dlg.spec()
        if spec is None:
            return
        self.message.emit("Đang cắt Short…", "busy")
        self._bg.run(
            lambda: clipshort.make(job, spec["start"], spec["end"], spec["hook"], work.parent),
            lambda child: self._short_made(child),
            lambda msg: self._short_failed(msg))

    def _short_ai(self, dlg, cues, title: str) -> None:
        from app.pipeline import clipshort

        dlg.set_busy(True, "Đang hỏi AI (một lệnh gọi rẻ)…")

        def done(cands):
            if dlg.isVisible():
                dlg.set_busy(False, f"AI gợi ý {len(cands)} đoạn — bấm vào để xem.")
                dlg.set_candidates(cands)

        def fail(msg):
            if dlg.isVisible():
                dlg.set_busy(False)
                dlg.set_note(str(msg), warn=True)

        self._bg.run(lambda: clipshort.ask_ai(cues, title), done, fail)

    def _short_made(self, child) -> None:
        self.refresh()
        self.changed.emit()
        self.message.emit("Đã tạo Short — duyệt phụ đề rồi dựng.", "ok")
        self.openRequested.emit(child.id)          # straight into the editor: it needs its own approval

    def _short_failed(self, msg: str) -> None:
        self.message.emit("Chưa cắt được Short.", "warn")
        QMessageBox.warning(self.window(), "Chưa cắt được Short", str(msg))

    # --- actions ----------------------------------------------------------------------
    def _open(self, iid: str) -> None:
        it = self._item(iid)
        if it.get("kind") == "compilation":
            self.compose([it])
            return
        if it.get("kind") != "group":
            self.openRequested.emit(iid)
            return
        parts = it.get("parts") or []
        nxt = (next((p for p in parts if p["has_vi"] and not p["has_output"]), None)
               or next((p for p in parts if p["has_vi"]), None))
        if nxt is not None:
            self.openRequested.emit(nxt["id"])
        else:
            self.groupResumeRequested.emit(iid)

    def _watch(self, iid: str) -> None:
        it = self._item(iid)
        videos = store.videos_of(it)
        if not videos:
            self.message.emit("Dự án này chưa có video nào trên máy.", "warn")
            return
        PlayerDialog(self.window(), it.get("title") or iid, videos).exec()

    def _external(self, iid: str) -> None:
        videos = store.videos_of(self._item(iid))
        if videos:
            open_external(videos[0][1])

    def _folder(self, iid: str) -> None:
        it = self._item(iid)
        dirs = [Path(d) for d in it.get("dirs") or []]
        target = dirs[-1] if it.get("kind") == "group" and dirs else (dirs[0] if dirs else None)
        open_external(target or store._storage())

    def _confirm(self, plan: list[dict], heavy_only: bool) -> bool:
        return bool(TrashConfirm(self.window(), plan, heavy_only=heavy_only).exec())

    def _inform(self, title: str, text: str) -> None:
        QMessageBox.information(self.window(), title, text)

    def plan(self, items: list[dict], *, heavy_only: bool = False) -> list[dict]:
        """[{item, paths, bytes}] — what deleting ``items`` would move to the bin."""
        out = []
        for it in items:
            paths = store.heavy_paths(it) if heavy_only else \
                [Path(d) for d in it.get("dirs") or [] if Path(d).exists()]
            if paths:
                out.append({"item": it, "paths": paths,
                            "bytes": sum(store.dir_size(p) for p in paths)})
        return out

    def delete_items(self, items: list[dict], *, heavy_only: bool = False) -> bool:
        """Confirm, then move to the Recycle Bin. Refuses (and moves nothing) when any
        item is in use. Returns True when something was moved."""
        items = [it for it in items if it]
        if not items:
            return False
        busy = [(it, why) for it in items if (why := self._guard(it))]
        if busy:
            lines = "\n".join(f"• {short_title(it.get('title') or it['id'])} — {why}"
                              for it, why in busy[:8])
            self._inform("Chưa xóa được", "Dự án đang được dùng nên chưa xóa:\n" + lines +
                         "\n\nDự án đang mở: mở dự án khác trước. Đang chạy: đợi xong rồi xóa.")
            return False
        plan = self.plan(items, heavy_only=heavy_only)
        if not plan:
            self._inform("Không có gì để xóa", "Không còn file nặng nào để dọn." if heavy_only
                         else "Thư mục dự án đã không còn.")
            self.refresh()
            return False
        if not self._confirm(plan, heavy_only):
            return False
        from desktop.trash import TrashError

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            store.trash_paths([p for entry in plan for p in entry["paths"]])
        except (TrashError, ValueError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self.window(), "Chưa xóa được", str(exc))
            self.refresh()
            self.changed.emit()
            return False
        QApplication.restoreOverrideCursor()
        total = fmt_size(sum(e["bytes"] for e in plan))
        what = (f"Đã dọn file nặng của {len(plan)} dự án ({total})" if heavy_only else
                f"Đã chuyển {len(plan)} dự án ({total}) vào Thùng rác")
        for entry in plan:
            self._checked.discard(entry["item"]["id"])
            self._details.pop(entry["item"]["id"], None)
        self.refresh()
        self.changed.emit()
        self.message.emit(what, "ok")
        return True

    # --- Ghép tập (app/pipeline/compilation.py) ------------------------------------------
    def _build_compile_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("inset")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 10, 12, 10)
        h.setSpacing(12)
        h.addWidget(icon_label("Merge", V2["purple_bright"], 16))
        col = QVBoxLayout()
        col.setSpacing(5)
        self.cb_title = label("", "h3")
        col.addWidget(self.cb_title)
        self.cb_bar = QProgressBar()
        self.cb_bar.setRange(0, 1000)
        self.cb_bar.setTextVisible(False)
        col.addWidget(self.cb_bar)
        self.cb_msg = label("", "small")
        col.addWidget(self.cb_msg)
        h.addLayout(col, 1)
        self.cb_watch = button("Xem", "Play", small=True)
        self.cb_watch.clicked.connect(self._watch_compiled)
        self.cb_folder = icon_button("FolderOpen", "Mở thư mục video ghép")
        self.cb_folder.clicked.connect(
            lambda: self._cb_path is not None and open_external(self._cb_path.parent))
        self.cb_cancel = button("Hủy", variant="ghost", small=True)
        self.cb_cancel.clicked.connect(self._cancel_compile)
        self.cb_close = icon_button("X", "Ẩn")
        self.cb_close.clicked.connect(lambda: bar.setVisible(False))
        for w in (self.cb_watch, self.cb_folder, self.cb_cancel, self.cb_close):
            h.addWidget(w)
        self._cb_path: Path | None = None
        self._cb_running = False
        self._cb_kind = "compile"          # the bar shows a join ("compile") or a "batch"
        self.batch_queued: Callable[[], set[str]] = lambda: set()   # set by the main window
        self.compile_bar = bar
        bar.setVisible(False)
        return bar

    def compose(self, items: list[dict]) -> bool:
        """Open the "Ghép thành một video" dialog with these items (may be none); emits
        compileRequested with its spec when the user confirms."""
        from app.pipeline import compilation

        from desktop.widgets.compile_dialog import CompileDialog

        if self._cb_running:
            self.message.emit("Đang ghép / dựng hàng loạt — đợi xong rồi ghép.", "warn")
            return False
        sources = compilation.order(store.compile_sources([it["id"] for it in items if it]))
        dlg = CompileDialog(self.window(), sources, candidates=store.compile_candidates)
        if not self._run_dialog(dlg):
            return False
        self.compileRequested.emit(dlg.spec())
        return True

    def _run_dialog(self, dlg) -> bool:
        return bool(dlg.exec())

    def open_batch(self, items: list[dict]) -> bool:
        """"Dựng hàng loạt": pick approved projects + whose settings to copy; emits
        batchRequested. Projects already queued are shown but can't be picked again."""
        from desktop.widgets.batch_dialog import BatchDialog

        if self._cb_running and self._cb_kind == "compile":
            self.message.emit("Đang ghép video — đợi xong rồi dựng hàng loạt.", "warn")
            return False
        ready, waiting = store.batch_candidates()
        pre = {it["id"] for it in items} | {p["id"] for it in items for p in it.get("parts") or []}
        dlg = BatchDialog(self.window(), ready, store.batch_templates(), preselect=pre,
                          waiting=waiting, queued=self.batch_queued())
        if not self._run_dialog(dlg):
            return False
        spec = dlg.spec()
        if not spec["ids"]:
            return False
        self.batchRequested.emit(spec)
        return True

    def batch_started(self, total: int) -> None:
        self._task_started("batch", "Dựng hàng loạt", f"{total} video chờ dựng…")

    def set_batch_progress(self, frac: float, msg: str) -> None:
        if self._cb_kind == "batch":
            self.set_compile_progress(frac, msg)

    def batch_finished(self, msg: str) -> None:
        if self._cb_kind == "batch":
            self.compile_finished(None, msg)
            self.refresh()

    def _task_started(self, kind: str, title: str, msg: str) -> None:
        self._cb_kind = kind
        self._cb_path = None
        self._cb_running = True
        self.cb_title.setText(title)
        self.cb_msg.setText(msg)
        self.cb_bar.setValue(0)
        self.cb_bar.setVisible(True)
        self.cb_cancel.setVisible(True)
        self.cb_cancel.setEnabled(True)
        for w in (self.cb_watch, self.cb_folder, self.cb_close):
            w.setVisible(False)
        self.compile_bar.setVisible(True)
        self.compose_btn.setEnabled(False)

    def compile_started(self, title: str, count: int) -> None:
        self._cb_kind = "compile"
        self._cb_path = None
        self._cb_running = True
        self.cb_title.setText(title)
        self.cb_msg.setText(f"Đang chuẩn bị ghép {count} video…")
        self.cb_bar.setValue(0)
        self.cb_bar.setVisible(True)
        self.cb_cancel.setVisible(True)
        self.cb_cancel.setEnabled(True)
        for w in (self.cb_watch, self.cb_folder, self.cb_close):
            w.setVisible(False)
        self.compile_bar.setVisible(True)
        self.compose_btn.setEnabled(False)

    def set_compile_progress(self, frac: float, msg: str) -> None:
        if frac >= 0:                      # < 0: only a message
            self.cb_bar.setValue(int(min(1.0, frac) * 1000))
        if msg:
            self.cb_msg.setText(msg)

    def compile_finished(self, path: str | None, msg: str) -> None:
        """Done (path) or failed/cancelled (None): the bar stays with the outcome."""
        self._cb_running = False
        self._cb_path = Path(path) if path else None
        self.cb_bar.setVisible(False)
        self.cb_cancel.setVisible(False)
        self.cb_msg.setText(msg)
        self.cb_watch.setVisible(bool(path))
        self.cb_folder.setVisible(bool(path))
        self.cb_close.setVisible(True)
        self.compose_btn.setEnabled(True)
        self.compile_bar.setVisible(True)
        if path:
            self.refresh()

    def _watch_compiled(self) -> None:
        if self._cb_path is not None and self._cb_path.exists():
            PlayerDialog(self.window(), self.cb_title.text(),
                         [("Video ghép", self._cb_path)]).exec()

    def _cancel_compile(self) -> None:
        self.cb_cancel.setEnabled(False)
        self.cb_msg.setText("Đang hủy…")
        (self.batchCancelRequested if self._cb_kind == "batch" else
         self.compileCancelRequested).emit()

    def _copy_chapters(self, iid: str) -> None:
        from app.pipeline import compilation

        it = self._item(iid)
        p = Path(it["dirs"][0]) / compilation.CHAPTERS_TXT
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            self.message.emit("Không thấy mục lục chương của video này.", "warn")
            return
        QApplication.clipboard().setText(text)
        self.message.emit(f"Đã sao chép {len(text.splitlines())} chương — dán vào mô tả video.",
                          "ok")

    def shutdown(self) -> None:
        self._bg.shutdown()
