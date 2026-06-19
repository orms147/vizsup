"""Screen 2 — Subtitle Editor (the human gate). Video + table + timeline, kept
in sync. Autosaves edits to job.vi_srt. Split/Merge/Add/Delete tools.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from desktop import cuemodel
from desktop.cuemodel import EditorCue
from desktop.theme import C
from desktop.widgets.subtitle_table import SubtitleTable
from desktop.widgets.timeline import Timeline
from desktop.widgets.video_panel import VideoPanel


class EditorView(QWidget):
    dirtyChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.job = None
        self.cues: list[EditorCue] = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        split = QSplitter(Qt.Vertical)
        # --- top: video | table ---
        top = QSplitter(Qt.Horizontal)
        self.video = VideoPanel()
        vwrap = QWidget()
        vlay = QVBoxLayout(vwrap)
        vlay.setContentsMargins(14, 14, 14, 14)
        cap = QLabel("VIDEO NGUỒN")
        cap.setObjectName("section")
        vlay.addWidget(cap)
        vlay.addWidget(self.video, 1)
        vwrap.setMinimumWidth(280)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        rlay.addLayout(self._toolbar())
        self.table = SubtitleTable()
        rlay.addWidget(self.table, 1)

        top.addWidget(vwrap)
        top.addWidget(right)
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setSizes([332, 900])

        split.addWidget(top)
        split.addWidget(self._dock())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([560, 190])
        root.addWidget(split)

        # wiring
        self.table.rowSelected.connect(lambda i: self._select(i, seek=True))
        self.table.seekRequested.connect(self.video.seek_seconds)
        self.table.edited.connect(self._mark_dirty)
        self.timeline.seeked.connect(self.video.seek_seconds)
        self.timeline.blockSelected.connect(lambda i: self.table.selectRow(i))
        self.timeline.cueChanged.connect(self._on_cue_dragged)
        self.video.positionChanged.connect(self.timeline.set_time)

    def _toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 9, 16, 9)
        title = QLabel("Phụ đề")
        title.setStyleSheet(f"font-weight:600;color:{C['text2']};")
        self.count = QLabel("0 dòng")
        self.count.setObjectName("mono")
        bar.addWidget(title)
        bar.addWidget(self.count)
        bar.addStretch(1)
        for label, slot in [("Tách", self._split), ("Gộp", self._merge),
                            ("Thêm", self._add), ("Xóa", self._delete)]:
            b = QPushButton(label)
            b.setObjectName("ghost")
            b.clicked.connect(slot)
            bar.addWidget(b)
        return bar

    def _dock(self) -> QWidget:
        dock = QWidget()
        dock.setObjectName("dock")
        lay = QVBoxLayout(dock)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 7, 16, 7)
        lb = QLabel("DÒNG THỜI GIAN")
        lb.setObjectName("section")
        head.addWidget(lb)
        head.addStretch(1)
        zo = QPushButton("－")
        zi = QPushButton("＋")
        for b in (zo, zi):
            b.setObjectName("ghost")
            b.setFixedSize(26, 26)
        head.addWidget(zo)
        head.addWidget(zi)
        lay.addLayout(head)
        self.timeline = Timeline()
        zo.clicked.connect(self.timeline.zoom_out)
        zi.clicked.connect(self.timeline.zoom_in)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.timeline)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)
        return dock

    # --- load / save ---------------------------------------------------------
    def load(self, job) -> None:
        self.job = job
        self.cues = cuemodel.load_pair(job)
        self.video.load(job.source_video)
        self.table.load(self.cues)
        self.timeline.set_cues(self.cues)
        self.count.setText(f"{len(self.cues)} dòng")

    def _reload(self) -> None:
        self.table.load(self.cues)
        self.timeline.set_cues(self.cues)
        self.count.setText(f"{len(self.cues)} dòng")
        self._mark_dirty()

    def _save(self) -> None:
        if self.job is not None:
            cuemodel.save_vi(self.job, self.cues)
            self.dirtyChanged.emit(False)

    def save_now(self) -> None:
        self._save_timer.stop()
        self._save()

    def _mark_dirty(self) -> None:
        self.dirtyChanged.emit(True)
        self._save_timer.start()

    # --- sync ----------------------------------------------------------------
    def _select(self, idx: int, *, seek: bool) -> None:
        self.timeline.set_selected(idx)
        if seek and 0 <= idx < len(self.cues):
            self.video.seek_seconds(self.cues[idx].start)

    def _on_cue_dragged(self, idx: int) -> None:
        self.table.refresh_row(idx)
        self._mark_dirty()

    # --- tools ---------------------------------------------------------------
    def _cur(self) -> int:
        return self.table.currentRow()

    def _add(self) -> None:
        i = self._cur()
        last = self.cues[-1] if self.cues else None
        start = (self.cues[i].end if 0 <= i < len(self.cues) else (last.end if last else 0.0))
        self.cues.insert(i + 1 if i >= 0 else len(self.cues),
                         EditorCue(start=start, end=start + 2.0, vi="", zh=""))
        self._reload()

    def _delete(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues):
            self.cues.pop(i)
            self._reload()

    def _split(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues):
            c = self.cues[i]
            mid = (c.start + c.end) / 2
            words = c.vi.split()
            half = len(words) // 2
            left, right = " ".join(words[:half]), " ".join(words[half:])
            c.end, c.vi = mid, left
            self.cues.insert(i + 1, EditorCue(start=mid, end=max(mid + 0.2, c.end), vi=right, zh=""))
            # fix the new cue end to original end
            self.cues[i + 1].end = max(mid + 0.2, mid + (c.end - c.start))
            self._reload()

    def _merge(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues) - 1:
            a, b = self.cues[i], self.cues[i + 1]
            a.end = b.end
            a.vi = (a.vi + " " + b.vi).strip()
            a.zh = (a.zh + b.zh).strip()
            self.cues.pop(i + 1)
            self._reload()
