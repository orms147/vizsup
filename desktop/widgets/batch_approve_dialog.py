"""«Duyệt hàng loạt» (Thư viện) — every project waiting at the approval gate on one screen:
left the waiting videos (title, length, how many lines still need a look), right the picked
one's flagged lines like «Duyệt nhanh» (autopilot_dialogs.flag_table) with «Sửa trong editor»
and «Duyệt video này»; one more button approves only the videos with nothing left to look at,
after a confirmation that lists them.

The gate stays the owner's click: nothing is approved without a click here (Enter presses no
approve button), and every approval goes through ``approve(id)`` — the main window's one path
shared with «Duyệt nhanh» (MainWindow._approve_listed: a running project is refused, the open
editor saved first, then the approval + the after-approve step). An approved row leaves the
list; a refused one stays, with the reason under the list."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.pipeline.compilation import clean_title
from desktop import store
from desktop.widgets.autopilot_dialogs import fill_flags, flag_table
from desktop.widgets.chrome import button, label
from desktop.widgets.dialogs import VzDialog
from desktop.widgets.recent_projects import duration_label, short_title

ROLE = Qt.UserRole


def display_title(row: dict) -> str:
    """"Chuyến Xe Tận Thế · Tập 9" for a named series' episode, else the title before its
    hashtags — what the library row shows."""
    return store.friendly_title(row["id"], row["title"]) or short_title(row["title"])


def is_clean(row: dict) -> bool:
    """Nothing left to look at: its flags were read, and there are none. Pure."""
    return row.get("flagged") == []


def flag_count(row: dict) -> str:
    flagged = row.get("flagged")
    return "?" if flagged is None else f"{len(flagged)} câu" if flagged else "Không"


def _ordered(rows: list[dict]) -> list[dict]:
    """A series together, by episode number — the library's «Theo tập» order."""
    from desktop.views.library_view import episode_sort_key

    return sorted(rows, key=episode_sort_key)


class CleanConfirm(VzDialog):
    """What «Duyệt các video không còn câu cần xem» approves — the list, and what approving
    starts (Tự động đăng) — before anything is approved."""

    def __init__(self, parent, rows: list[dict]) -> None:
        n = len(rows)
        super().__init__(f"Duyệt {n} video?", parent, width=540)
        self.body.addWidget(label(
            "Các video dưới đây không còn câu nào được đánh dấu cần xem. Duyệt là khóa bản phụ "
            "đề hiện tại để lồng tiếng — sửa sau khi duyệt sẽ phải duyệt lại.", "secondary",
            wrap=True))
        box = QFrame()
        box.setObjectName("inset")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(6)
        for r in rows[:10]:
            line = QHBoxLayout()
            line.setSpacing(12)
            line.addWidget(label(clean_title(r.get("label") or display_title(r), 64)), 1)
            line.addWidget(label(duration_label(r["duration"]) if r.get("duration") else "",
                                 "monoText"))
            bl.addLayout(line)
        if n > 10:
            bl.addWidget(label(f"… và {n - 10} video khác", "small"))
        self.body.addWidget(box)
        auto = sum(1 for r in rows if r.get("auto"))
        if auto:
            self.body.addWidget(label(
                f"{auto} video thuộc bộ phim bật Tự động đăng: duyệt xong app tự dựng, tự cắt "
                "Short (nếu bật) rồi lên lịch đăng.", "small", wrap=True))
        self.add_button("Hủy")
        self.ok_btn = self.add_button(f"Duyệt {n} video", icon_name="ShieldCheck", primary=True,
                                      accept=True)
        self.ok_btn.setDefault(False)          # a bulk approval is a click, never a stray Enter


class BatchApproveDialog(VzDialog):
    """``rows``: store.gate_waiting(flags=True), the running ones already left out.
    ``approve(id)`` → None when that project is approved now, else why not (shown; the row
    stays). After exec: ``approved`` (ids, in the order approved) and ``edit_id`` (the project
    «Sửa trong editor» asked for — the caller opens it)."""

    def __init__(self, parent, rows: list[dict], *,
                 approve: Callable[[str], str | None]) -> None:
        super().__init__("Duyệt hàng loạt", parent, width=1000)
        self._approve = approve
        self.approved: list[str] = []
        self.edit_id: str | None = None
        self._rows = _ordered([{**r, "label": display_title(r)} for r in rows])
        self.body.addWidget(label(
            "Các video đã dịch xong, đang chờ bạn duyệt. Bấm một video để đọc nhanh các câu được "
            "đánh dấu; cần sửa thì mở editor. Duyệt xong là khóa bản phụ đề để lồng tiếng — sửa "
            "sau khi duyệt sẽ phải duyệt lại.", "secondary", wrap=True))
        self.summary = label("", "h3")
        self.body.addWidget(self.summary)

        split = QHBoxLayout()
        split.setSpacing(16)
        self.list = QTableWidget(0, 3)
        self.list.setHorizontalHeaderLabels(["Video", "Dài", "Cần xem"])
        self.list.verticalHeader().setVisible(False)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setWordWrap(False)
        self.list.setTextElideMode(Qt.ElideRight)    # long Douyin titles elide, never scroll sideways
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        h = self.list.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.list.setMinimumSize(340, 380)
        self.list.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._show(r))
        split.addWidget(self.list, 2)

        right = QWidget()
        right.setObjectName("bare")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)
        self.cur_title = label("", "h3", wrap=True)
        rv.addWidget(self.cur_title)
        self.cur_info = label("", "secondary", wrap=True)
        rv.addWidget(self.cur_info)
        self.lines = flag_table()
        self.lines.setMinimumHeight(200)
        rv.addWidget(self.lines, 1)
        self.empty = label("", "secondary", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        rv.addWidget(self.empty, 1)
        self.cur_after = label("", "small", wrap=True)
        rv.addWidget(self.cur_after)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        acts.addStretch(1)
        self.edit_btn = button("Sửa trong editor", "Captions",
                               tip="Đóng cửa sổ này và mở video trong editor")
        self.edit_btn.clicked.connect(self._edit_current)
        acts.addWidget(self.edit_btn)
        self.approve_btn = button("Duyệt video này", "ShieldCheck", variant="primary")
        self.approve_btn.clicked.connect(self._approve_current)
        acts.addWidget(self.approve_btn)
        rv.addLayout(acts)
        split.addWidget(right, 3)
        self.body.addLayout(split, 1)
        self.note = label("", "small", wrap=True)
        self.note.setVisible(False)
        self.body.addWidget(self.note)

        self.clean_btn = button("", "ShieldCheck",
                                tip="Duyệt một lần mọi video không còn câu nào được đánh dấu "
                                    "(hỏi lại trước khi duyệt)")
        self.clean_btn.clicked.connect(self._approve_clean)
        self._buttons.insertWidget(0, self.clean_btn)     # left, away from Đóng
        self.add_button("Đóng")
        for b in (self.edit_btn, self.approve_btn, self.clean_btn):
            b.setAutoDefault(False)            # Enter never approves: the gate is a click
        self._fill(0)

    # --- rows ---------------------------------------------------------------------------
    def rows(self) -> list[dict]:
        return list(self._rows)

    def clean_rows(self) -> list[dict]:
        return [r for r in self._rows if is_clean(r)]

    def _current(self) -> dict | None:
        r = self.list.currentRow()
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def select(self, iid: str) -> None:
        r = next((k for k, row in enumerate(self._rows) if row["id"] == iid), -1)
        if r >= 0:
            self.list.setCurrentCell(r, 0)

    def _fill(self, select: int) -> None:
        t = self.list
        t.blockSignals(True)
        t.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            name = QTableWidgetItem(row["label"])
            name.setToolTip(row["title"])
            name.setData(ROLE, row["id"])
            dur = QTableWidgetItem(duration_label(row["duration"]) if row.get("duration") else "—")
            count = QTableWidgetItem(flag_count(row))
            if row.get("flagged") is None:
                count.setToolTip("Không đọc được các câu cần xem — mở editor để xem")
            for c, it in enumerate((name, dur, count)):
                t.setItem(r, c, it)
        cur = max(0, min(select, len(self._rows) - 1)) if self._rows else -1
        if cur >= 0:
            t.setCurrentCell(cur, 0)
        t.blockSignals(False)
        self._show(cur)
        self._update_counts()

    def _drop(self, ids: set[str]) -> None:
        at = self.list.currentRow()
        self._rows = [r for r in self._rows if r["id"] not in ids]
        self._fill(at)

    def _show(self, r: int) -> None:
        """The right side for row ``r`` (-1: none left). The placeholder takes the table's
        room when there is nothing flagged to list."""
        row = self._rows[r] if 0 <= r < len(self._rows) else None
        for w in (self.cur_title, self.cur_info, self.cur_after, self.edit_btn, self.approve_btn):
            w.setVisible(row is not None)
        flagged = row.get("flagged") if row is not None else None
        self.lines.setVisible(bool(flagged))
        self.empty.setVisible(not flagged)
        if row is None:
            self.empty.setText("Đã duyệt hết các video đang chờ." if self.approved else
                               "Không có video nào đang chờ duyệt.")
            return
        self.cur_title.setText(row["label"])
        self.cur_title.setToolTip(row["title"])
        cues = row.get("cues") or 0
        if flagged is None:
            info = f"{cues} câu · không đọc được các câu cần xem."
            self.empty.setText("Mở «Sửa trong editor» để xem các câu trước khi duyệt.")
        elif flagged:
            info = (f"{cues} câu · {len(flagged)} câu được đánh dấu cần xem — đọc nhanh các câu "
                    "dưới đây; cần sửa thì mở editor.")
        else:
            info = f"{cues} câu · không có câu nào bị đánh dấu."
            self.empty.setText("Không còn câu nào cần xem lại.")
        self.cur_info.setText(info)
        fill_flags(self.lines, flagged or [])
        self.cur_after.setText(row.get("after") or "")
        self.cur_after.setVisible(bool(row.get("after")))

    def _update_counts(self) -> None:
        n, k = len(self._rows), len(self.clean_rows())
        self.summary.setText(f"{n} video chờ duyệt · {k} video không còn câu cần xem" if n else
                             "Không còn video nào chờ duyệt.")
        self.clean_btn.setText(f"Duyệt các video không còn câu cần xem ({k})")
        self.clean_btn.setEnabled(k > 0)

    def _say(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    # --- actions ------------------------------------------------------------------------
    def _edit_current(self) -> None:
        row = self._current()
        if row is None:
            return
        self.edit_id = row["id"]
        self.accept()

    def _approve_current(self) -> None:
        row = self._current()
        if row is None:
            return
        why = self._approve(row["id"])
        if why is None:
            self.approved.append(row["id"])
            self._drop({row["id"]})
            self._say(f"Đã duyệt “{clean_title(row['label'], 60)}”.")
        else:
            self._say(f"Chưa duyệt “{clean_title(row['label'], 60)}”: {why}.")

    def _confirm_clean(self, rows: list[dict]) -> bool:
        return bool(CleanConfirm(self, rows).exec())

    def _approve_clean(self) -> None:
        """One click + the confirmation: approve each video with nothing flagged, in order."""
        rows = self.clean_rows()
        if not rows or not self._confirm_clean(rows):
            return
        done, refused = [], []
        for row in rows:
            why = self._approve(row["id"])
            if why is None:
                done.append(row["id"])
            else:
                refused.append(f"“{clean_title(row['label'], 40)}” ({why})")
        self.approved += done
        self._drop(set(done))
        msg = f"Đã duyệt {len(done)} video." if done else ""
        if refused:
            more = f" và {len(refused) - 3} video khác" if len(refused) > 3 else ""
            msg += f" Chưa duyệt {', '.join(refused[:3])}{more}."
        self._say(msg.strip())
