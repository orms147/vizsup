"""“Dịch một loạt video” — pick many videos of one page (a Douyin 合集, a Douyin creator page,
a Bilibili space / season / series) and queue them all to the edit gate (app/pipeline/
batch_import.py).

The list is read off the GUI thread (BgRunner: a Douyin page opens the embedded browser in its
own process, up to ~2 min); rows show the title, the length when the page told it and "đã có
trên máy" for a link the library already made a project from — the new ones start ticked. One
"Bộ phim / nhóm" for the whole batch: an existing series, a new name (its profile is created on
"Dịch N video"), each video's own 《…》, or none. ``result_items`` = the start page's queue items
(E-9); every video still stops at the edit gate — the owner approves each one."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
)

from app.pipeline import batch_import
from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import button, label
from desktop.widgets.dialogs import VzDialog
from desktop.widgets.recent_projects import duration_label

ROLE = Qt.UserRole
AUTO, NONE = "auto", "none"


class BatchImportDialog(VzDialog):
    """``base``: the start page's options (``InputView._collect_opts``); ``series_choice``: its
    "Bộ phim" pick; ``lister``: ``watch.list_videos`` unless a test gives a fake one."""

    def __init__(self, parent=None, *, url: str = "", base: dict | None = None,
                 series_choice: str = AUTO, lister=None, load_now: bool = True) -> None:
        super().__init__("Dịch một loạt video", parent, width=780)
        self._base = dict(base or {})
        self._lister = lister
        self._bg = BgRunner(self)
        self._gen = 0                 # a newer listing makes an older answer stale
        self._loading = False
        self._loaded_url = ""
        self._videos: list[dict] = []
        self._start_choice = series_choice or AUTO
        self._series_touched = False  # the owner's own pick is never replaced by a suggestion
        self.result_items: list[dict] = []
        self.body.addWidget(label(
            "Dán link 合集 Douyin (hoặc link một tập trong 合集), trang người đăng Douyin "
            "(douyin.com/user/…) hoặc Bilibili (space.bilibili.com/…, danh sách / tuyển tập). "
            "Chọn các video cần dịch: chúng chạy lần lượt (video cũ trước) tới bước duyệt, với "
            "tùy chọn ở màn Bắt đầu — mỗi video vẫn chờ bạn duyệt.", "secondary", wrap=True))

        urow = QHBoxLayout()
        urow.setSpacing(8)
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://www.douyin.com/collection/…")
        self.url.returnPressed.connect(self.reload)
        self.url.textChanged.connect(lambda _t: self._update())
        self.load_btn = button("Tải danh sách", "RefreshCw", small=True)
        self.load_btn.clicked.connect(self.reload)
        urow.addWidget(self.url, 1)
        urow.addWidget(self.load_btn)
        self.body.addLayout(urow)
        self.status = label("", "small", wrap=True)
        self.body.addWidget(self.status)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Video", "Thời lượng", "Trên máy"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(300)
        self.tree.setTextElideMode(Qt.ElideRight)
        self.tree.setStyleSheet(
            f"QTreeView{{background:{V2['input']};border:1px solid {V2['line_soft']};"
            f"border-radius:7px;}} QTreeView::item{{padding:4px 4px;}}")
        hd = self.tree.header()
        hd.setStretchLastSection(False)
        hd.setSectionResizeMode(0, QHeaderView.Stretch)
        hd.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hd.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(lambda *_a: self._update())
        self.body.addWidget(self.tree)
        row = QHBoxLayout()
        for text, state in (("Chọn tất cả", Qt.Checked), ("Bỏ chọn tất cả", Qt.Unchecked)):
            b = button(text, variant="ghost", small=True)
            b.clicked.connect(lambda _=False, st=state: self.set_all(st))
            row.addWidget(b)
        row.addStretch(1)
        self.count = label("", "small")
        row.addWidget(self.count)
        self.body.addLayout(row)

        srow = QHBoxLayout()
        srow.setSpacing(8)
        srow.addWidget(label("Bộ phim / nhóm", "section"))
        self.series = QComboBox()
        self.series.setEditable(True)
        self.series.setInsertPolicy(QComboBox.NoInsert)   # a typed name is a new series, not an item
        self.series.setCompleter(None)
        self.series.lineEdit().setPlaceholderText("chọn, hoặc gõ tên bộ phim / nhóm mới")
        self.series.setToolTip(
            "Cả loạt dùng chung một bộ phim: thuật ngữ, nhân vật + xưng hô, tóm tắt, kiểu phụ đề, "
            "cài đặt dựng và Tự động đăng. Gõ tên mới để tạo bộ phim (hay nhóm) cho loạt này.")
        self._fill_series()
        self.series.currentTextChanged.connect(lambda _t: self._update_series_note())
        self.series.activated.connect(lambda _i: setattr(self, "_series_touched", True))
        self.series.lineEdit().textEdited.connect(
            lambda _t: setattr(self, "_series_touched", True))
        srow.addWidget(self.series, 1)
        self.plan_btn = button("Tự động đăng…", "Settings2", variant="ghost", small=True,
                               tip="Nơi đăng, giờ đăng của bộ phim này (sau khi bạn duyệt từng video)")
        self.plan_btn.clicked.connect(self._edit_plan)
        srow.addWidget(self.plan_btn)
        self.body.addLayout(srow)
        self.series_note = label("", "small", wrap=True)
        self.body.addWidget(self.series_note)

        self.add_button("Hủy")
        self.go = self.add_button("Dịch 0 video", icon_name="ArrowRight", primary=True,
                                  accept=True)
        self._update_series_note()
        self._update()
        if url.strip() and load_now:
            self.reload()
        elif not url.strip():
            self._say("Dán link trang / 合集 rồi bấm Tải danh sách.")

    # --- the list ------------------------------------------------------------------------
    def reload(self) -> None:
        """Read the link's page in the background; a newer press wins over an older answer."""
        url = self.url.text().strip()
        if not url:
            self._say("Dán link trang / 合集 trước.", "warnText")
            return
        from app.pipeline import watch

        self._gen += 1
        # the lister is picked HERE, on the GUI thread — never looked up later by the thread
        gen, lister = self._gen, self._lister or watch.list_videos
        self._loading = True
        self._loaded_url = ""
        self._videos = []
        self.tree.clear()
        douyin = "douyin" in url.lower()
        self._say("Đang mở trang Douyin trong trình duyệt nhúng để đọc danh sách — có thể mất 1–2 "
                  "phút…" if douyin else "Đang đọc danh sách video…")
        self._update()
        self._bg.run(lambda: batch_import.load(url, lister=lister),
                     lambda res, g=gen, u=url: self._loaded(g, u, res),
                     lambda msg, g=gen, u=url: self._loaded(g, u, {"videos": [], "error": msg}))

    def _loaded(self, gen: int, url: str, res: dict) -> None:
        if gen != self._gen:
            return
        self._loading = False
        self._videos = list(res.get("videos") or [])
        self._loaded_url = url if self._videos else ""
        self.tree.blockSignals(True)
        self.tree.clear()
        for i, v in enumerate(self._videos):
            have = bool(v.get("have"))
            where = ("đã có trên máy · " + batch_import.STATES.get(v.get("state") or "", "")
                     ).rstrip(" ·") if have else "mới"
            dur = duration_label(float(v["duration"])) if v.get("duration") else "—"
            it = QTreeWidgetItem([v.get("title") or v.get("id") or v["url"], dur, where])
            it.setData(0, ROLE, i)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Unchecked if have else Qt.Checked)
            it.setToolTip(0, f"{v.get('title') or ''}\n{v['url']}".strip())
            if have:
                it.setToolTip(2, f"Đã có dự án {v['have']} làm từ link này — chọn để làm lại "
                                 f"thành một dự án mới.")
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        if res.get("error"):
            self._say("⚠ " + res["error"], "errText")
        else:
            self._say(self._summary(res))
            slug = batch_import.suggested_series(self._videos)
            if slug and self._start_choice == AUTO and not self._series_touched:
                self._select_series(slug)        # the 合集 / 《…》 of an existing series
        self._update()

    def _summary(self, res: dict) -> str:
        n = len(self._videos)
        have = sum(1 for v in self._videos if v.get("have"))
        bits = [f"{n} video" + (f" · {have} đã có trên máy (không tự chọn)" if have else "")]
        mix = res.get("mix") or {}
        if mix.get("name"):
            bits.append(f"合集 “{mix['name']}”")
        text = " · ".join(bits) + "."
        if res.get("kind") == "douyin_user":
            text += (" Trang người đăng Douyin chỉ hiện ~18–36 video mới nhất khi chưa đăng nhập "
                     "— dán link 合集 để có đủ các tập.")
        return text

    def set_all(self, state) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)

    def picked(self) -> list[dict]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                out.append(self._videos[it.data(0, ROLE)])
        return out

    def _ready(self) -> bool:
        """The list on screen is the link's (not loading, the link not changed since)."""
        return (not self._loading and bool(self._loaded_url)
                and self.url.text().strip() == self._loaded_url)

    def _update(self) -> None:
        n = len(self.picked()) if self._ready() else 0
        self.go.setText(f"Dịch {n} video")
        self.go.setEnabled(n > 0)
        self.load_btn.setEnabled(not self._loading)
        total = self.tree.topLevelItemCount()
        self.count.setText(f"Đã chọn {n}/{total}" if total and self._ready() else "")

    def _say(self, text: str, role: str = "small") -> None:
        self.status.setText(text)
        self.status.setObjectName(role)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    # --- the series ----------------------------------------------------------------------
    def _fill_series(self) -> None:
        from app.pipeline import series

        self.series.blockSignals(True)
        self.series.clear()
        self.series.addItem("Tự nhận theo tên 《…》 của từng video", AUTO)
        try:
            profiles = series.list_profiles()
        except Exception:  # noqa: BLE001 - a broken storage never blocks the dialog
            profiles = []
        for p in profiles:
            self.series.addItem(f"《{p.name}》", p.slug)
        self.series.addItem("Không dùng bộ phim", NONE)
        i = self.series.findData(self._start_choice)
        self.series.setCurrentIndex(i if i >= 0 else 0)
        self.series.blockSignals(False)

    def _select_series(self, key: str) -> None:
        i = self.series.findData(key)
        self.series.setCurrentIndex(i if i >= 0 else 0)
        self._update_series_note()

    def series_choice(self) -> str:
        """The picked item's key (auto | none | slug), or the typed text = a new name."""
        text = self.series.currentText().strip()
        i = self.series.findText(text)
        if i >= 0:
            return self.series.itemData(i)
        return text or AUTO

    def _update_series_note(self) -> None:
        from app.pipeline import autopilot, series

        key = self.series_choice()
        p = series.load(key) if key not in (AUTO, NONE) else None
        self.plan_btn.setEnabled(p is not None)
        if key == AUTO:
            text = ("Mỗi video tự nhận bộ phim theo tên 《…》 trong tiêu đề (không có thì dịch "
                    "riêng).")
        elif key == NONE:
            text = "Các video dịch riêng, không dùng bộ phim nào."
        elif p is not None:
            text = f"{series.summary(p)} · {autopilot.summary(p.slug)}"
        else:
            text = (f"Tạo bộ phim mới “{key}” cho cả loạt — thuật ngữ, tóm tắt, kiểu phụ đề, cài "
                    f"đặt dựng và Tự động đăng (bật ở Thư viện → Tự động đăng) dùng chung cho "
                    f"mọi video.")
        self.series_note.setText(text)

    def _edit_plan(self) -> None:
        from desktop.widgets.autopilot_dialogs import AutopilotDialog

        key = self.series_choice()
        if key in (AUTO, NONE):
            return
        dlg = AutopilotDialog(self, slug=key)
        if dlg.exec():
            dlg.save()
        self._update_series_note()

    # --- done ----------------------------------------------------------------------------
    def accept(self) -> None:
        """"Dịch N video": the queue items, the batch's series created when it is a new name."""
        picked = self.picked() if self._ready() else []
        if not picked:
            return
        try:
            slug = batch_import.series_slug(self.series_choice())
        except OSError as exc:
            self._say(f"⚠ Không tạo được bộ phim: {exc}", "errText")
            return
        self.result_items = batch_import.queue_items(self._base, picked, slug)
        super().accept()

    def done(self, r: int) -> None:  # noqa: D102 - accept / reject / close all end here
        self._bg.shutdown()           # a listing still running is dropped, never shown
        super().done(r)
