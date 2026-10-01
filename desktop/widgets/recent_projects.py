""""Tiếp tục dự án" (v2 start screen aside): recent projects with a thumbnail, a
status badge (Chờ bạn duyệt / Đã duyệt / Đã dựng), a detail line and a ··· menu.
Rows fill in their thumbnail + review counts from a background summary pass."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from desktop import store
from desktop.cuemodel import fmt_time
from desktop.icons import pixmap
from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import badge, button, icon_button, icon_label, label

THUMB_W, THUMB_H = 50, 72


def _when(mtime: float) -> str:
    age = time.time() - mtime
    if age < 15 * 60:
        return "Vừa mở"
    lt, now = time.localtime(mtime), time.localtime()
    if lt.tm_yday == now.tm_yday and lt.tm_year == now.tm_year:
        return "Hôm nay"
    if age < 2 * 86400 and (now.tm_yday - lt.tm_yday) % 365 == 1:
        return "Hôm qua"
    return time.strftime("%d/%m", lt)


def short_title(raw: str) -> str:
    head = (raw or "").split("#", 1)[0].strip()
    return head or (raw or "").strip()


class _Row(QFrame):
    openRequested = Signal(str)
    deleteRequested = Signal(str)
    folderRequested = Signal(str)

    def __init__(self, info: dict) -> None:
        super().__init__()
        self.setObjectName("recentRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame#recentRow{{background:transparent;border:none;border-bottom:1px solid "
            f"{V2['line_soft']};border-radius:0;}}"
            f"QFrame#recentRow:hover{{background:{V2['panel']};}}")
        self._id = info["id"]
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 12, 0, 14)
        h.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setPixmap(pixmap("FileVideo", V2["muted"], 18))
        self.thumb.setStyleSheet(f"background:{V2['raised']};border-radius:5px;")
        h.addWidget(self.thumb, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        title = label(short_title(info.get("title") or info["id"]), "h3")
        title.setToolTip(info.get("title") or "")
        title.setMaximumWidth(210)
        col.addWidget(title)
        if info.get("has_output"):
            self.badge = badge("Đã dựng", "ok")
        else:
            self.badge = badge("Chờ bạn duyệt", "purple")
        col.addWidget(self.badge, 0, Qt.AlignLeft)
        self.sub = label("MP4" if info.get("has_output") else "Phụ đề tiếng Việt", "small")
        col.addWidget(self.sub)
        col.addWidget(label(_when(info.get("mtime") or time.time()), "small"))
        h.addLayout(col, 1)
        more = icon_button("MoreHorizontal", "Thao tác dự án")
        m = QMenu(more)
        m.addAction("Mở dự án", lambda: self.openRequested.emit(self._id))
        m.addAction("Mở thư mục", lambda: self.folderRequested.emit(self._id))
        m.addSeparator()
        m.addAction("Xóa dự án…", lambda: self.deleteRequested.emit(self._id))
        more.setMenu(m)
        more.setPopupMode(more.ToolButtonPopupMode.InstantPopup)
        h.addWidget(more, 0, Qt.AlignTop)
        self._has_output = bool(info.get("has_output"))

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.openRequested.emit(self._id)
        super().mouseReleaseEvent(ev)

    def apply_summary(self, s: dict) -> None:
        if s.get("thumb"):
            pm = QPixmap(s["thumb"])
            if not pm.isNull():
                pm = pm.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
                x = max(0, (pm.width() - THUMB_W) // 2)
                y = max(0, (pm.height() - THUMB_H) // 2)
                self.thumb.setPixmap(pm.copy(x, y, THUMB_W, THUMB_H))
        dur = fmt_time(s["duration"], 0) if s.get("duration") else ""
        if self._has_output:
            self.sub.setText(" · ".join(p for p in ("MP4", dur) if p))
            return
        if s.get("approved"):
            self.badge.setText("Đã duyệt")
            self.badge.setObjectName("badgeOk")
            self.badge.style().unpolish(self.badge)
            self.badge.style().polish(self.badge)
        n, pend = s.get("cues", 0), s.get("pending", 0)
        first = f"{pend} câu cần xem lại" if pend else f"{n} câu"
        self.sub.setText(" · ".join(p for p in (first, dur) if p))


class RecentProjects(QWidget):
    openRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._bg = BgRunner(self)
        self._rows: dict[str, _Row] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.addWidget(label("Tiếp tục dự án", "h2"))
        head.addStretch(1)
        self.count = label("", "small")
        head.addWidget(self.count)
        v.addLayout(head)
        v.addSpacing(10)
        self._list = QVBoxLayout()
        self._list.setSpacing(0)
        v.addLayout(self._list)
        self._empty = QWidget()
        ev = QVBoxLayout(self._empty)
        ev.setContentsMargins(0, 18, 0, 18)
        ev.addWidget(icon_label("FolderOpen", V2["muted"], 20), 0, Qt.AlignLeft)
        ev.addWidget(label("Dự án đầu tiên của bạn sẽ nằm ở đây.", "secondary", wrap=True))
        ev.addWidget(label("Cứ đóng ứng dụng. Lần sau mở lại để tiếp tục từ bước đã xong.",
                           "small", wrap=True))
        v.addWidget(self._empty)
        v.addSpacing(18)
        folder = button("Mở thư mục dự án", "FolderOpen", variant="ghost", small=True)
        folder.clicked.connect(lambda: _open_folder(store._storage()))
        v.addWidget(folder, 0, Qt.AlignLeft)
        v.addSpacing(8)
        v.addWidget(label("Video, phụ đề và bản dựng được lưu cùng nhau. Mở lại để tiếp tục, "
                          "không cần chạy từ đầu.", "small", wrap=True))
        v.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        while self._list.count():
            w = self._list.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._rows.clear()
        jobs = store.list_jobs(limit=6)
        self.count.setText(f"{len(jobs)} dự án")
        self._empty.setVisible(not jobs)
        for j in jobs:
            row = _Row(j)
            row.openRequested.connect(self.openRequested)
            row.deleteRequested.connect(self.deleteRequested)
            row.folderRequested.connect(lambda jid: _open_folder(store._storage() / jid))
            self._rows[j["id"]] = row
            self._list.addWidget(row)
        ids = [j["id"] for j in jobs]
        if ids:
            self._bg.run(lambda: [store.project_summary(i) for i in ids], self._apply,
                         lambda _m: None)

    def _apply(self, summaries: list[dict]) -> None:
        for s in summaries:
            row = self._rows.get(s["id"])
            if row is not None:
                row.apply_summary(s)

    def shutdown(self) -> None:
        self._bg.shutdown()


def _open_folder(path) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
