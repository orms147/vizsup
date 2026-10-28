"""“Ghép thành một video” — pick and order finished episodes (or an earlier compilation)
for app/pipeline/compilation.py.

Rows start in episode order (第12集 / EP12 / Tập 12 in the title); drag or Lên/Xuống to
change it, double-click a chapter name to rename it. "Thêm video…" lists every rendered
project not in the list yet — the way to extend "Tập 1–50" with 51 … 100. Warnings
(not rendered, edited after the render, an episode twice, a number missing) never block,
except that a source without a rendered video can't be joined at all."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from app.pipeline import compilation as comp
from app.pipeline.compilation import Source
from desktop.theme import V2
from desktop.widgets.chrome import button, icon_label, label
from desktop.widgets.dialogs import VzDialog
from desktop.widgets.recent_projects import duration_label

ROLE = Qt.UserRole


def _length(s: Source) -> str:
    return duration_label(s.duration) if s.duration else "—"


def _row_text(s: Source) -> str:
    """The chapter name, or for an earlier compilation its title + chapter count."""
    if s.kind == "compilation":
        return f"{comp.clean_title(s.title, 50)} · {len(s.chapters)} chương"
    return s.chapters[0][1] if s.chapters else comp.clean_title(s.title)


class _PickDialog(VzDialog):
    """Tick the rendered videos to add (episode order, numbers first)."""

    def __init__(self, parent, sources: list[Source]) -> None:
        super().__init__("Thêm video vào bản ghép", parent, width=560)
        self.body.addWidget(label("Các video đã dựng chưa có trong danh sách.", "secondary"))
        self.list = QListWidget()
        self.list.setMinimumHeight(320)
        self.list.setStyleSheet("QListWidget::item{padding:5px 6px;}")
        for s in comp.order(sources):
            it = QListWidgetItem(f"{_row_text(s)}   ·   {comp.clean_title(s.title, 50)}   ·   "
                                 f"{_length(s)}" if s.kind != "compilation" else
                                 f"{_row_text(s)}   ·   {_length(s)}")
            it.setData(ROLE, s.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setToolTip(s.title)
            self.list.addItem(it)
        self.body.addWidget(self.list)
        row = QHBoxLayout()
        for text, state in (("Chọn tất cả", Qt.Checked), ("Bỏ chọn", Qt.Unchecked)):
            b = button(text, variant="ghost", small=True)
            b.clicked.connect(lambda _=False, st=state: self._set_all(st))
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        if not sources:
            self.body.addWidget(label("Không còn video đã dựng nào khác.", "small"))
        self.add_button("Hủy")
        self.add_button("Thêm", icon_name="Plus", primary=True, accept=True)

    def _set_all(self, state) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def picked(self) -> list[str]:
        return [self.list.item(i).data(ROLE) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class CompileDialog(VzDialog):
    def __init__(self, parent, sources: list[Source], *,
                 candidates: Callable[[], list[Source]] | None = None) -> None:
        super().__init__("Ghép thành một video", parent, width=780)
        self._candidates = candidates or (lambda: [])
        self._sources: dict[str, Source] = {}
        self._skipped: list[Source] = []
        self._title_touched = False
        self.body.addWidget(label(
            "Nối các video đã dựng thành một video dài — không dịch, không lồng tiếng lại. "
            "Mỗi tập thành một chương (xem được trong trình phát và dán vào mô tả YouTube). "
            "Kéo để đổi thứ tự, bấm đúp tên chương để sửa.", "secondary", wrap=True))

        trow = QHBoxLayout()
        trow.setSpacing(10)
        trow.addWidget(label("Tên video", "section"))
        self.title_edit = QLineEdit()
        self.title_edit.textEdited.connect(lambda _t: setattr(self, "_title_touched", True))
        trow.addWidget(self.title_edit, 1)
        self.body.addLayout(trow)

        mid = QHBoxLayout()
        mid.setSpacing(10)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Chương", "Video", "Thời lượng"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(300)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.itemDoubleClicked.connect(self._edit_name)
        self.tree.setStyleSheet(
            f"QTreeView{{background:{V2['input']};border:1px solid {V2['line_soft']};"
            f"border-radius:7px;}} QTreeView::item{{padding:5px 4px;}}"
            f"QTreeView::item:selected{{background:{V2['purple_soft']};color:{V2['text']};}}")
        hd = self.tree.header()
        hd.setStretchLastSection(False)
        hd.setSectionResizeMode(0, QHeaderView.Interactive)
        hd.setSectionResizeMode(1, QHeaderView.Stretch)
        hd.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setColumnWidth(0, 230)
        model = self.tree.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.rowsMoved):
            sig.connect(lambda *_a: self._update())
        mid.addWidget(self.tree, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.add_btn = button("Thêm video…", "Plus", small=True,
                              tip="Thêm video đã dựng khác (vd. tập mới vào bản ghép cũ)")
        self.add_btn.clicked.connect(self._add)
        self.up_btn = button("Lên", "ChevronUp", variant="ghost", small=True)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = button("Xuống", "ChevronDown", variant="ghost", small=True)
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.sort_btn = button("Xếp theo số tập", variant="ghost", small=True,
                               tip="Sắp lại theo số tập trong tên (第12集, EP12, Tập 12…)")
        self.sort_btn.clicked.connect(self._sort)
        self.remove_btn = button("Bỏ khỏi danh sách", "X", variant="ghost", small=True)
        self.remove_btn.clicked.connect(self._remove)
        for b in (self.add_btn, self.up_btn, self.down_btn, self.sort_btn, self.remove_btn):
            side.addWidget(b)
        side.addStretch(1)
        mid.addLayout(side)
        self.body.addLayout(mid)

        self.warn = QFrame()
        self.warn.setObjectName("warnCard")
        wh = QHBoxLayout(self.warn)
        wh.setContentsMargins(12, 8, 12, 8)
        wh.addWidget(icon_label("TriangleAlert", V2["amber"], 15), 0, Qt.AlignTop)
        self.warn_lbl = label("", "warnText", wrap=True)
        wh.addWidget(self.warn_lbl, 1)
        self.body.addWidget(self.warn)
        self.summary = label("", "secondary")
        self.body.addWidget(self.summary)

        self.add_button("Hủy")
        self.ok_btn = self.add_button("Ghép video", icon_name="Merge", primary=True, accept=True)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.add_sources(sources)

    # --- rows -------------------------------------------------------------------------
    def add_sources(self, sources: list[Source]) -> None:
        """Rendered sources become rows (in the given order); the others are only
        reported in the warning box."""
        was_auto = self._is_auto_order()
        for s in sources:
            if s.id in self._sources:
                continue
            if s.state == "missing" or not s.clips:
                if all(x.id != s.id for x in self._skipped):
                    self._skipped.append(s)
                continue
            self._sources[s.id] = s
            it = QTreeWidgetItem([_row_text(s), comp.clean_title(s.title, 80), _length(s)])
            it.setData(0, ROLE, s.id)
            it.setToolTip(1, s.title)
            it.setFlags((it.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
            if s.kind != "compilation":
                it.setFlags(it.flags() | Qt.ItemIsEditable)
                it.setToolTip(0, "Bấm đúp để đổi tên chương")
            else:
                it.setToolTip(0, "\n".join(n for _t, n in s.chapters[:30]))
            if s.state == "stale":
                it.setForeground(1, QColor(V2["amber"]))
                it.setToolTip(1, f"{s.title}\n⚠ {s.note}")
            self.tree.addTopLevelItem(it)
        if was_auto:
            self._sort()
        self._update()

    def _ids(self) -> list[str]:
        return [self.tree.topLevelItem(i).data(0, ROLE) for i in range(self.tree.topLevelItemCount())]

    def ordered_sources(self) -> list[Source]:
        return [self._sources[i] for i in self._ids() if i in self._sources]

    def _is_auto_order(self) -> bool:
        cur = self.ordered_sources()
        return [s.id for s in comp.order(cur)] == [s.id for s in cur]

    def _sort(self) -> None:
        want = [s.id for s in comp.order(self.ordered_sources())]
        items = {self.tree.topLevelItem(i).data(0, ROLE): self.tree.topLevelItem(i)
                 for i in range(self.tree.topLevelItemCount())}
        for _ in range(self.tree.topLevelItemCount()):
            self.tree.takeTopLevelItem(0)
        for sid in want:
            self.tree.addTopLevelItem(items[sid])
        self._update()

    def _move(self, step: int) -> None:
        rows = sorted(self.tree.indexOfTopLevelItem(i) for i in self.tree.selectedItems())
        if not rows:
            return
        n = self.tree.topLevelItemCount()
        if (step < 0 and rows[0] == 0) or (step > 0 and rows[-1] == n - 1):
            return
        moved = []
        for r in (rows if step < 0 else reversed(rows)):
            it = self.tree.takeTopLevelItem(r)
            self.tree.insertTopLevelItem(r + step, it)
            moved.append(it)
        for it in moved:
            it.setSelected(True)
        self._update()

    def _remove(self) -> None:
        for it in self.tree.selectedItems():
            sid = it.data(0, ROLE)
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
            self._sources.pop(sid, None)
        self._update()

    def _edit_name(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.flags() & Qt.ItemIsEditable:
            self.tree.editItem(item, 0)

    def _add(self) -> None:
        have = set(self._sources)
        inside = {e.get("id") for s in self._sources.values() for e in s.episodes}
        # an episode already inside a listed compilation is not offered again
        pool = [s for s in self._candidates() if s.id not in have and s.state != "missing"
                and not (s.episodes and all(e.get("id") in inside for e in s.episodes))]
        dlg = _PickDialog(self, pool)
        if dlg.exec():
            ids = set(dlg.picked())
            self.add_sources([s for s in pool if s.id in ids])

    # --- state ------------------------------------------------------------------------
    def _update_buttons(self) -> None:
        sel = bool(self.tree.selectedItems())
        for b in (self.up_btn, self.down_btn, self.remove_btn):
            b.setEnabled(sel)

    def _update(self) -> None:
        srcs = self.ordered_sources()
        if not self._title_touched:
            self.title_edit.setText(comp.default_title(srcs) if srcs else "")
        notes = [f"{comp.clean_title(s.title, 40)}: {s.note} — bỏ qua" for s in self._skipped]
        notes += comp.warnings(srcs)
        self.warn_lbl.setText("\n".join(notes[:12]) + (f"\n… và {len(notes) - 12} lưu ý khác"
                                                        if len(notes) > 12 else ""))
        self.warn.setVisible(bool(notes))
        eps = sum(max(1, len(s.episodes)) for s in srcs)
        total = sum(s.duration for s in srcs)
        self.summary.setText(f"{eps} tập · khoảng {duration_label(total)}" if srcs and total else
                             f"{eps} tập" if srcs else "Chưa có video nào — bấm “Thêm video…”.")
        ok = len(srcs) >= 2 or (len(srcs) == 1 and len(srcs[0].clips) >= 2)
        self.ok_btn.setEnabled(ok)
        self.ok_btn.setToolTip("" if ok else "Cần ít nhất 2 video")
        self._update_buttons()

    def spec(self) -> dict:
        """What the worker needs: title, source ids in order, renamed chapters."""
        names = {}
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            s = self._sources.get(it.data(0, ROLE))
            text = it.text(0).strip()
            if s is not None and s.kind != "compilation" and text:
                names[s.id] = text
        srcs = self.ordered_sources()
        return {"title": self.title_edit.text().strip() or comp.default_title(srcs),
                "ids": [s.id for s in srcs], "names": names}
