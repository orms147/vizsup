"""Editable subtitle table: # | bắt đầu | kết thúc | Tiếng Việt (sửa) | 中文 (đọc).
VI text + times are editable; Chinese is read-only reference. Too-long lines are
flagged. Emits selection + seek + edited signals.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

from desktop.cuemodel import EditorCue, fmt_time, parse_time
from desktop.theme import C

COLS = ["#", "bắt đầu", "kết thúc", "Tiếng Việt", "中文"]


class SubtitleTable(QTableWidget):
    rowSelected = Signal(int)
    seekRequested = Signal(float)
    edited = Signal()
    beforeEdit = Signal()  # fires before a cell edit is applied (for undo snapshots)

    def __init__(self) -> None:
        super().__init__(0, len(COLS))
        self._cues: list[EditorCue] = []
        self._loading = False
        self.setHorizontalHeaderLabels(COLS)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setWordWrap(True)
        h = self.horizontalHeader()
        self.setColumnWidth(0, 46)
        self.setColumnWidth(1, 92)
        self.setColumnWidth(2, 92)
        h.setStretchLastSection(True)
        h.setSectionResizeMode(3, h.ResizeMode.Stretch)
        self.itemChanged.connect(self._on_item_changed)
        self.itemSelectionChanged.connect(self._on_select)
        self.cellDoubleClicked.connect(self._on_double)

    def load(self, cues: list[EditorCue]) -> None:
        self._loading = True
        self._cues = cues
        self.setRowCount(len(cues))
        for r, c in enumerate(cues):
            self._set_row(r, c)
        self._loading = False
        self.resizeRowsToContents()

    def _set_row(self, r: int, c: EditorCue) -> None:
        num = QTableWidgetItem(str(r + 1))
        num.setTextAlignment(Qt.AlignCenter)
        num.setFlags(Qt.ItemIsEnabled)
        start = QTableWidgetItem(fmt_time(c.start))
        end = QTableWidgetItem(fmt_time(c.end))
        for it in (start, end):
            it.setForeground(QBrush(QColor(C["muted"])))
        vi = QTableWidgetItem(c.vi)
        zh = QTableWidgetItem(c.zh)
        zh.setFlags(Qt.ItemIsEnabled)  # read-only
        zh.setForeground(QBrush(QColor(C["muted2"])))
        if c.too_long:
            num.setForeground(QBrush(QColor(C["amber"])))
            vi.setToolTip(f"Quá dài để lồng tiếng: ~{c.est_speak:.1f}s nói / {c.dur:.1f}s khe")
        for col, it in enumerate((num, start, end, vi, zh)):
            self.setItem(r, col, it)

    def get_cues(self) -> list[EditorCue]:
        return self._cues

    def set_playing_row(self, idx: int) -> None:
        if 0 <= idx < self.rowCount() and idx != self.currentRow():
            self.selectRow(idx)

    def refresh_row(self, idx: int) -> None:
        """Re-display a row from the (possibly externally mutated) cue."""
        if 0 <= idx < len(self._cues):
            self._loading = True
            self._set_row(idx, self._cues[idx])
            self._loading = False

    # --- edits ---------------------------------------------------------------
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        r, col = item.row(), item.column()
        if not (0 <= r < len(self._cues)):
            return
        if col in (1, 2, 3):
            self.beforeEdit.emit()  # snapshot the pre-edit state (cues still old here)
        c = self._cues[r]
        if col == 3:
            c.vi = item.text()
        elif col in (1, 2):
            t = parse_time(item.text())
            if t is None:
                self._loading = True
                item.setText(fmt_time(c.start if col == 1 else c.end))
                self._loading = False
                return
            if col == 1:
                c.start = t
            else:
                c.end = max(t, c.start + 0.1)
        else:
            return
        # refresh too-long flag on the row
        self._loading = True
        self._set_row(r, c)
        self._loading = False
        self.edited.emit()

    def _on_select(self) -> None:
        r = self.currentRow()
        if r >= 0:
            self.rowSelected.emit(r)

    def _on_double(self, r: int, _c: int) -> None:
        if 0 <= r < len(self._cues):
            self.seekRequested.emit(self._cues[r].start)
