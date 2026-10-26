"""Cue list (v2): the "40 câu" panel — a QListView over the editor's cues, painted by
a delegate as 2–3 line rows (index · time · issue / VI / ZH) instead of a wide table.

Rows are never edited inline: the inspector on the right owns editing. The
"Cần xem lại" filter and F8 use the same set (flagged and not yet "Đã xem"), see
``pending_rows``.
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QListView,
    QPushButton,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from app.pipeline import review
from desktop import cuemodel
from desktop.cuemodel import EditorCue, fmt_time
from desktop.icons import pixmap
from desktop.theme import V2, cjk_font, fonts
from desktop.widgets.chrome import badge, button, icon_button, label

PAD_X, PAD_Y = 12, 10
IDX_W = 34          # index column
GAP = 4


class CueListModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self.cues: list[EditorCue] = []
        self.issues: list[list[str]] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.cues)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.cues)):
            return None
        if role == Qt.DisplayRole:
            return self.cues[index.row()].vi
        return None

    def reset(self, cues: list[EditorCue], issues: list[list[str]]) -> None:
        self.beginResetModel()
        self.cues, self.issues = cues, issues
        self.endResetModel()

    def issues_at(self, row: int) -> list[str]:
        return self.issues[row] if 0 <= row < len(self.issues) else []

    def changed(self, row: int) -> None:
        if 0 <= row < len(self.cues):
            ix = self.index(row)
            self.dataChanged.emit(ix, ix)


class CueDelegate(QStyledItemDelegate):
    def __init__(self, view: QListView) -> None:
        super().__init__(view)
        self._view = view
        sans, mono = fonts()
        self.f_idx = QFont(mono)
        self.f_idx.setPixelSize(11)
        self.f_time = QFont(mono)
        self.f_time.setPixelSize(11)
        self.f_issue = QFont(sans)
        self.f_issue.setPixelSize(11)
        self.f_vi = QFont(sans)
        self.f_vi.setPixelSize(13)
        self.f_zh = QFont(cjk_font())
        self.f_zh.setPixelSize(12)

    def _text_w(self) -> int:
        return max(80, self._view.viewport().width() - PAD_X * 2 - IDX_W)

    def _wrap_h(self, font: QFont, text: str, w: int) -> int:
        if not text:
            return 0
        return QFontMetrics(font).boundingRect(QRect(0, 0, w, 10000),
                                               Qt.TextWordWrap, text).height()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        c = index.model().cues[index.row()]
        w = self._text_w()
        h = PAD_Y + QFontMetrics(self.f_time).height() + GAP + 2
        h += max(QFontMetrics(self.f_vi).height(), self._wrap_h(self.f_vi, c.vi, w))
        if c.zh:
            h += GAP + self._wrap_h(self.f_zh, c.zh, w)
        return QSize(w, h + PAD_Y + 1)

    def paint(self, p: QPainter, option, index) -> None:
        model: CueListModel = index.model()
        row = index.row()
        c = model.cues[row]
        r = option.rect
        sel = bool(option.state & QStyle.State_Selected)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if sel:
            p.fillRect(r, QColor(V2["purple_soft"]))
            p.fillRect(QRect(r.left(), r.top(), 2, r.height()), QColor(V2["purple"]))
        elif option.state & QStyle.State_MouseOver:
            p.fillRect(r, QColor(V2["raised"]))
        p.setPen(QPen(QColor(V2["line_soft"]), 1))
        p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())

        x0 = r.left() + PAD_X
        tx = x0 + IDX_W
        tw = r.right() - PAD_X - tx
        y = r.top() + PAD_Y
        fm_t = QFontMetrics(self.f_time)

        p.setFont(self.f_idx)
        p.setPen(QColor(V2["purple_bright"] if sel else V2["muted"]))
        p.drawText(QRect(x0, y, IDX_W, fm_t.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{row + 1:02d}")

        p.setFont(self.f_time)
        p.setPen(QColor(V2["secondary"]))
        tstr = f"{fmt_time(c.start, 2)} — {fmt_time(c.end, 2)}"
        p.drawText(QRect(tx, y, tw, fm_t.height()), Qt.AlignLeft | Qt.AlignVCenter, tstr)
        right = r.right() - PAD_X

        # right side of the time row: issue text (amber) · reviewed ✓ · lock
        iss = model.issues_at(row)
        reviewed = cuemodel.is_reviewed(c)
        if c.locked:
            pm = pixmap("LockKeyhole", V2["muted"], 12)
            p.drawPixmap(right - 12, y + (fm_t.height() - 12) // 2, pm)
            right -= 18
        if reviewed:
            pm = pixmap("Check", V2["green"], 12)
            p.drawPixmap(right - 12, y + (fm_t.height() - 12) // 2, pm)
            right -= 16
        if iss:
            p.setFont(self.f_issue)
            p.setPen(QColor(V2["muted"] if reviewed else V2["amber"]))
            txt = QFontMetrics(self.f_issue).elidedText(
                review.LABELS.get(iss[0], iss[0]) + (f" +{len(iss) - 1}" if len(iss) > 1 else ""),
                Qt.ElideRight, max(40, right - tx - fm_t.horizontalAdvance(tstr) - 12))
            p.drawText(QRect(tx, y, right - tx, fm_t.height()), Qt.AlignRight | Qt.AlignVCenter, txt)

        y += fm_t.height() + GAP + 2
        p.setFont(self.f_vi)
        vh = max(QFontMetrics(self.f_vi).height(), self._wrap_h(self.f_vi, c.vi, tw))
        if c.vi:
            p.setPen(QColor(V2["text"]))
            p.drawText(QRect(tx, y, tw, vh), Qt.TextWordWrap | Qt.AlignLeft, c.vi)
        else:
            p.setPen(QColor(V2["dim"]))
            p.drawText(QRect(tx, y, tw, vh), Qt.AlignLeft, "(trống)")
        y += vh
        if c.zh:
            y += GAP
            p.setFont(self.f_zh)
            p.setPen(QColor(V2["muted"]))
            p.drawText(QRect(tx, y, tw, r.bottom() - y), Qt.TextWordWrap | Qt.AlignLeft, c.zh)
        p.restore()


class _ListView(QListView):
    """Enter opens editing; Tab moves between cues instead of leaving the list."""

    editRequested = Signal()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        k = ev.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.editRequested.emit()
            return
        if k in (Qt.Key_Tab, Qt.Key_Backtab):
            step = -1 if (k == Qt.Key_Backtab or ev.modifiers() & Qt.ShiftModifier) else 1
            row = self.currentIndex().row()
            n = self.model().rowCount()
            nxt = row + step
            while 0 <= nxt < n and self.isRowHidden(nxt):
                nxt += step
            if 0 <= nxt < n:
                self.setCurrentIndex(self.model().index(nxt))
            return
        super().keyPressEvent(ev)

    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802 - keep Tab for rows
        return False

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self.doItemsLayout()             # wrapped row heights depend on the width


class CueList(QFrame):
    """The "N câu" panel: head (count · Tách · Gộp · search · more), filter row
    (Tất cả / Cần xem lại N · Theo video), the list, footer (Câu i / N · Đã xem ·
    Xem lại F8)."""

    rowSelected = Signal(int)
    editRequested = Signal()
    splitRequested = Signal()
    mergeRequested = Signal()
    findRequested = Signal()
    addRequested = Signal()
    deleteRequested = Signal()
    snapAllRequested = Signal()
    retimeRequested = Signal()
    bulkShortenRequested = Signal()
    aiSuggestionsRequested = Signal()
    tidyRequested = Signal()
    reviewedRequested = Signal()
    nextPendingRequested = Signal()
    followToggled = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._review_only = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = QFrame()
        head.setObjectName("panelHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(14, 7, 8, 7)
        h.setSpacing(4)
        self.count = label("0 câu", "h3")
        h.addWidget(self.count)
        h.addStretch(1)
        for text, ic, key, sig, tip in (
            ("Tách", "Scissors", "S", self.splitRequested, "Tách câu tại vị trí phát (S)"),
            ("Gộp", "Merge", "M", self.mergeRequested, "Gộp với câu sau (M)"),
        ):
            b = button(text, ic, variant="ghost", tip=tip)
            b.clicked.connect(sig)
            h.addWidget(b)
            h.addWidget(label(key, "small"))
            h.addSpacing(6)
        self.find_btn = icon_button("Search", "Tìm / thay (Ctrl+F)")
        self.find_btn.clicked.connect(self.findRequested)
        h.addWidget(self.find_btn)
        self.more_btn = icon_button("MoreHorizontal", "Thêm thao tác")
        self._build_more_menu()
        h.addWidget(self.more_btn)
        v.addWidget(head)

        frow = QHBoxLayout()
        frow.setContentsMargins(10, 8, 10, 8)
        frow.setSpacing(6)
        seg = QFrame()
        seg.setObjectName("segmented")
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(3, 3, 3, 3)
        sh.setSpacing(2)
        self.f_all = QPushButton("Tất cả")
        self.f_review = QPushButton("Cần xem lại")
        for b in (self.f_all, self.f_review):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            sh.addWidget(b)
        self.f_all.setChecked(True)
        self.pending_badge = badge("0", "warn")
        sh.addWidget(self.pending_badge)
        sh.addSpacing(4)
        frow.addWidget(seg)
        frow.addStretch(1)
        self.follow = button("Theo video", "Focus", variant="ghost", checkable=True, small=True,
                             tip="Tự chọn và cuộn tới câu đang phát")
        self.follow.setChecked(True)
        self.follow.toggled.connect(self.followToggled)
        frow.addWidget(self.follow)
        self.f_all.clicked.connect(lambda: self.set_review_only(False))
        self.f_review.clicked.connect(lambda: self.set_review_only(True))
        v.addLayout(frow)

        # list | "all reviewed" empty state
        body = QWidget()
        self._stack = QStackedLayout(body)
        self.model = CueListModel()
        self.view = _ListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(CueDelegate(self.view))
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)  # Shift/Ctrl-click → style scope
        self.view.setUniformItemSizes(False)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setMouseTracking(True)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setStyleSheet(f"QListView{{background:{V2['panel']};border:none;}}")
        self.view.selectionModel().currentRowChanged.connect(
            lambda cur, _prev: self.rowSelected.emit(cur.row()) if cur.isValid() else None)
        self.view.selectionModel().currentRowChanged.connect(lambda *_: self._update_pos())
        self.view.editRequested.connect(self.editRequested)
        self._stack.addWidget(self.view)
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        msg = label("Bạn đã xem hết các câu cần chú ý", "secondary")
        msg.setAlignment(Qt.AlignCenter)
        ev.addWidget(msg)
        show_all = button("Xem tất cả", variant="ghost")
        show_all.clicked.connect(lambda: self.set_review_only(False))
        ev.addWidget(show_all, 0, Qt.AlignHCenter)
        ev.addStretch(1)
        self._stack.addWidget(empty)
        v.addWidget(body, 1)

        foot = QFrame()
        foot.setObjectName("panelHead")
        foot.setStyleSheet(f"QFrame#panelHead{{border-bottom:none;border-top:1px solid "
                           f"{V2['line_soft']};}}")
        fh = QHBoxLayout(foot)
        fh.setContentsMargins(12, 6, 8, 6)
        fh.setSpacing(6)
        self.pos_lbl = label("Câu — / 0", "small")
        fh.addWidget(self.pos_lbl)
        fh.addStretch(1)
        self.reviewed_btn = button("Đã xem", "Check", variant="ghost",
                                   tip="Đánh dấu đã kiểm tra rồi tới câu còn chờ")
        self.reviewed_btn.clicked.connect(self.reviewedRequested)
        fh.addWidget(self.reviewed_btn)
        nxt = button("Xem lại", "ChevronRight", variant="ghost", tip="Tới câu cần xem lại (F8)")
        nxt.clicked.connect(self.nextPendingRequested)
        fh.addWidget(nxt)
        fh.addWidget(label("F8", "small"))
        v.addWidget(foot)

    def _build_more_menu(self) -> None:
        from PySide6.QtWidgets import QMenu

        m = QMenu(self)
        m.addAction("Thêm câu sau câu này", self.addRequested.emit)
        m.addAction("Xóa câu này", self.deleteRequested.emit)
        m.addSeparator()
        m.addAction("Tự canh tất cả câu theo giọng nói   Shift+A", self.snapAllRequested.emit)
        m.addAction("Đo lại thời gian từ âm thanh (giữ nguyên chữ)…", self.retimeRequested.emit)
        m.addSeparator()
        m.addAction("Rút gọn hàng loạt các câu đọc không kịp…", self.bulkShortenRequested.emit)
        m.addAction("Xem tất cả gợi ý của AI soát…", self.aiSuggestionsRequested.emit)
        m.addAction("Gộp câu quá ngắn & gỡ chồng lấn", self.tidyRequested.emit)
        self.more_btn.setMenu(m)
        self.more_btn.setPopupMode(self.more_btn.ToolButtonPopupMode.InstantPopup)

    # --- data ---------------------------------------------------------------------
    def load(self, cues: list[EditorCue], issues: list[list[str]] | None = None) -> None:
        cur = self.currentRow()
        self.model.reset(cues, issues if issues is not None else [[] for _ in cues])
        self.count.setText(f"{len(cues)} câu")
        self._apply_filter()
        if 0 <= cur < len(cues):
            self.selectRow(cur)
        self._update_pos()

    def set_issues(self, issues: list[list[str]]) -> None:
        self.model.issues = issues
        self._update_badge()
        self.view.viewport().update()

    def refresh_row(self, row: int) -> None:
        self.model.changed(row)
        self._update_badge()

    def refresh_all(self) -> None:
        self.view.doItemsLayout()
        self.view.viewport().update()
        self._update_badge()

    # --- selection ----------------------------------------------------------------
    def currentRow(self) -> int:  # noqa: N802 - mirrors the QTableWidget API it replaced
        ix = self.view.currentIndex()
        return ix.row() if ix.isValid() else -1

    def selectRow(self, row: int, *, center: bool = True) -> None:  # noqa: N802
        if not (0 <= row < self.model.rowCount()):
            return
        if self.view.isRowHidden(row):
            self.set_review_only(False)
        ix = self.model.index(row)
        if self.view.currentIndex() != ix:
            self.view.setCurrentIndex(ix)
        self.view.scrollTo(ix, QAbstractItemView.PositionAtCenter if center
                           else QAbstractItemView.EnsureVisible)
        self._update_pos()

    def selected_rows(self) -> list[int]:
        return sorted(ix.row() for ix in self.view.selectionModel().selectedRows())

    def _update_pos(self) -> None:
        r = self.currentRow()
        n = self.model.rowCount()
        self.pos_lbl.setText(f"Câu {r + 1} / {n}" if r >= 0 else f"Câu — / {n}")

    # --- review filter ------------------------------------------------------------
    def pending_rows(self) -> list[int]:
        """Flagged and not yet marked "Đã xem" — the filter and F8 share this."""
        return [i for i, c in enumerate(self.model.cues)
                if self.model.issues_at(i) and not cuemodel.is_reviewed(c)]

    def next_pending(self, after: int) -> int:
        rows = self.pending_rows()
        for r in rows:
            if r > after:
                return r
        return rows[0] if rows else -1

    def review_only(self) -> bool:
        return self._review_only

    def set_review_only(self, on: bool) -> None:
        self._review_only = on
        self.f_all.setChecked(not on)
        self.f_review.setChecked(on)
        self._apply_filter()

    def _apply_filter(self) -> None:
        pending = set(self.pending_rows()) if self._review_only else None
        for i in range(self.model.rowCount()):
            self.view.setRowHidden(i, pending is not None and i not in pending)
        empty = pending is not None and not pending
        self._stack.setCurrentIndex(1 if empty else 0)
        self._update_badge()
        r = self.currentRow()
        if pending and (r < 0 or r not in pending):
            self.selectRow(min(pending))

    def reapply_filter(self) -> None:
        if self._review_only:
            self._apply_filter()
        else:
            self._update_badge()

    def _update_badge(self) -> None:
        n = len(self.pending_rows())
        self.pending_badge.setText(str(n))
        self.pending_badge.setVisible(n > 0)

    def follow_enabled(self) -> bool:
        return self.follow.isChecked()
