"""Inspector tab "Nhạc nền": the user's own background music, for the whole video or per
section (app/pipeline/music.py).

Each clip is an audio file (copied into the project), a span on the VIDEO timeline (from
… to … or to the end), a volume, fades, "loop when shorter" and "quieter under the
Vietnamese voice". The panel only edits the list; EditorView picks files, knows the
playhead, saves music.json and plays previews.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.pipeline import music as mu
from desktop.cuemodel import fmt_time, parse_time
from desktop.theme import V2
from desktop.widgets.chrome import button, label


class MusicPanel(QWidget):
    changed = Signal()             # a clip was added / edited / removed → save
    addRequested = Signal(str)     # "all" (cả video) | "here" (từ vị trí phát) → pick a file
    startHereRequested = Signal()  # selected clip starts at the playhead
    endHereRequested = Signal()    # selected clip ends at the playhead
    seekRequested = Signal(float)
    previewRequested = Signal()    # listen to the selected clip's file
    replaceRequested = Signal()    # another file for the selected clip

    def __init__(self) -> None:
        super().__init__()
        self._clips: list[mu.Clip] = []
        self._duration = 0.0
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.addWidget(label("Nhạc nền của bạn", "h3"), 1)
        self.count_lbl = label("", "mono")
        head.addWidget(self.count_lbl)
        v.addLayout(head)
        v.addWidget(label("Thêm nhạc cho cả video hoặc từng đoạn. Nhạc được trộn lúc dựng, tự "
                          "nhỏ lại khi có giọng Việt — không cần duyệt lại phụ đề.", "small",
                          wrap=True))
        adds = QHBoxLayout()
        adds.setSpacing(6)
        self.add_all = button("Cả video…", "Music2", small=True,
                              tip="Chọn một bài nhạc chạy suốt video (lặp lại nếu ngắn hơn)")
        self.add_here = button("Từ vị trí phát…", "Plus", variant="ghost", small=True,
                               tip="Chọn một bài nhạc bắt đầu ở chỗ video đang dừng")
        for b, kind in ((self.add_all, "all"), (self.add_here, "here")):
            b.clicked.connect(lambda _c=False, k=kind: self.addRequested.emit(k))
            adds.addWidget(b)
        adds.addStretch(1)
        v.addLayout(adds)

        self.list = QListWidget()
        self.list.setMinimumHeight(56)
        self.list.setMaximumHeight(104)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setStyleSheet(
            f"QListWidget{{background:{V2['panel']};border:1px solid {V2['line']};"
            f"border-radius:6px;}}"
            f"QListWidget::item{{padding:3px 6px;}}"
            f"QListWidget::item:selected{{background:{V2['purple_soft']};color:{V2['text']};}}")
        self.list.currentRowChanged.connect(lambda _i: self._show(self.selected()))
        self.list.itemDoubleClicked.connect(
            lambda _it: self.seekRequested.emit(self.selected().start) if self.selected() else None)
        v.addWidget(self.list)

        self.detail = QFrame()
        self.detail.setObjectName("inset")
        d = QVBoxLayout(self.detail)
        d.setContentsMargins(10, 8, 10, 8)
        d.setSpacing(6)
        self.file_lbl = label("", "small", wrap=True)
        d.addWidget(self.file_lbl)

        srow = QHBoxLayout()
        cap = label("Bắt đầu", "secondary")
        cap.setFixedWidth(64)
        srow.addWidget(cap)
        self.start_edit = QLineEdit()
        self.start_edit.setFixedWidth(84)
        self.start_edit.editingFinished.connect(self._on_start)
        srow.addWidget(self.start_edit)
        self.start_here = button("= vị trí phát", "Clock3", variant="ghost", small=True)
        self.start_here.clicked.connect(self.startHereRequested)
        srow.addWidget(self.start_here)
        srow.addStretch(1)
        d.addLayout(srow)

        erow = QHBoxLayout()
        cap = label("Kết thúc", "secondary")
        cap.setFixedWidth(64)
        erow.addWidget(cap)
        self.end_edit = QLineEdit()
        self.end_edit.setFixedWidth(84)
        self.end_edit.editingFinished.connect(self._on_end)
        erow.addWidget(self.end_edit)
        self.end_here = button("= vị trí phát", "Clock3", variant="ghost", small=True)
        self.end_here.clicked.connect(self.endHereRequested)
        erow.addWidget(self.end_here)
        self.to_end = QCheckBox("Đến hết video")
        self.to_end.toggled.connect(self._on_to_end)
        erow.addWidget(self.to_end)
        erow.addStretch(1)
        d.addLayout(erow)

        vrow = QHBoxLayout()
        cap = label("Âm lượng", "secondary")
        cap.setFixedWidth(64)
        vrow.addWidget(cap)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, int(mu.VOLUME_MAX * 100))
        self.volume.valueChanged.connect(self._on_volume)
        vrow.addWidget(self.volume, 1)
        self.volume_lbl = label("", "mono")
        self.volume_lbl.setFixedWidth(40)
        self.volume_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        vrow.addWidget(self.volume_lbl)
        d.addLayout(vrow)

        frow = QHBoxLayout()
        self.fade_in = self._spin("Hiện dần")
        self.fade_out = self._spin("Tắt dần")
        for cap_text, sp in (("Hiện dần", self.fade_in), ("Tắt dần", self.fade_out)):
            frow.addWidget(label(cap_text, "secondary"))
            frow.addWidget(sp)
        frow.addStretch(1)
        d.addLayout(frow)

        self.loop_cb = QCheckBox("Lặp lại nếu bài nhạc ngắn hơn đoạn")
        self.duck_cb = QCheckBox("Tự nhỏ lại khi có giọng Việt")
        self.duck_cb.setToolTip("Nhạc giảm khoảng 12 dB trong lúc lồng tiếng / bình luận đang "
                                "nói, rồi lên lại")
        for cb in (self.loop_cb, self.duck_cb):
            cb.toggled.connect(lambda _on: self._on_field())
            d.addWidget(cb)

        brow = QHBoxLayout()
        self.listen_btn = button("Nghe thử", "Play", variant="ghost", small=True)
        self.listen_btn.clicked.connect(self.previewRequested)
        self.replace_btn = button("Đổi tệp…", "Replace", variant="ghost", small=True)
        self.replace_btn.clicked.connect(self.replaceRequested)
        self.del_btn = button("Xóa", "Trash2", variant="ghost", small=True)
        self.del_btn.clicked.connect(self._delete)
        brow.addWidget(self.listen_btn)
        brow.addWidget(self.replace_btn)
        brow.addStretch(1)
        brow.addWidget(self.del_btn)
        d.addLayout(brow)
        self.warn_lbl = label("", "small", wrap=True)
        self.warn_lbl.setStyleSheet(f"color:{V2['amber']};")
        d.addWidget(self.warn_lbl)
        v.addWidget(self.detail)
        v.addStretch(1)
        self._refresh()

    def _spin(self, tip: str) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(0.0, mu.FADE_MAX)
        sp.setSingleStep(0.5)
        sp.setDecimals(1)
        sp.setSuffix(" s")
        sp.setFixedWidth(72)
        sp.setToolTip(tip)
        sp.valueChanged.connect(lambda _v: self._on_field())
        return sp

    # --- data in / out -------------------------------------------------------------
    def set_clips(self, clips: list[mu.Clip], duration: float = 0.0) -> None:
        self._clips = sorted(clips, key=lambda c: c.start)
        self._duration = duration or self._duration
        self._refresh(select=0 if self._clips else -1)

    def set_duration(self, duration: float) -> None:
        self._duration = duration

    def clips(self) -> list[mu.Clip]:
        return list(self._clips)

    def selected(self) -> mu.Clip | None:
        i = self.list.currentRow()
        return self._clips[i] if 0 <= i < len(self._clips) else None

    def add(self, clip: mu.Clip) -> None:
        self._clips.append(clip)
        self._clips.sort(key=lambda c: c.start)
        self._refresh(select=self._clips.index(clip))
        self.changed.emit()

    def update_selected(self, **fields) -> None:
        c = self.selected()
        if c is None:
            return
        for k, val in fields.items():
            setattr(c, k, val)
        if c.end is not None and c.end <= c.start:
            c.end = None                       # an end before the start → to the end
        self._clips.sort(key=lambda x: x.start)
        self._refresh(select=self._clips.index(c))
        self.changed.emit()

    def set_warning(self, text: str) -> None:
        self.warn_lbl.setText(text)
        self.warn_lbl.setVisible(bool(text))

    # --- internals -------------------------------------------------------------------
    def _row_text(self, c: mu.Clip) -> str:
        end = "hết video" if c.end is None else fmt_time(c.end)
        name = c.name or c.file.rsplit("/", 1)[-1]
        return f"{fmt_time(c.start)} → {end}  ·  {int(round(c.volume * 100))}%  ·  {name}"

    def _refresh(self, select: int | None = None) -> None:
        row = self.list.currentRow() if select is None else select
        self.list.blockSignals(True)
        self.list.clear()
        for c in self._clips:
            self.list.addItem(QListWidgetItem(self._row_text(c)))
        self.list.blockSignals(False)
        if 0 <= row < len(self._clips):
            self.list.setCurrentRow(row)
        self._show(self.selected())
        self.count_lbl.setText(f"{len(self._clips)} đoạn" if self._clips else "chưa có")

    def _show(self, c: mu.Clip | None) -> None:
        self.detail.setVisible(c is not None)
        if c is None:
            return
        self._loading = True
        self.file_lbl.setText(f"Tệp: {c.name or c.file.rsplit('/', 1)[-1]}")
        self.start_edit.setText(fmt_time(c.start))
        self.to_end.setChecked(c.end is None)
        self.end_edit.setText("" if c.end is None else fmt_time(c.end))
        self.end_edit.setEnabled(c.end is not None)
        self.end_edit.setPlaceholderText("hết video")
        self.volume.setValue(int(round(c.volume * 100)))
        self.volume_lbl.setText(f"{int(round(c.volume * 100))}%")
        self.fade_in.setValue(c.fade_in)
        self.fade_out.setValue(c.fade_out)
        self.loop_cb.setChecked(c.loop)
        self.duck_cb.setChecked(c.duck)
        self._loading = False

    def _update_row(self) -> None:
        c, item = self.selected(), self.list.item(self.list.currentRow())
        if c is not None and item is not None:
            item.setText(self._row_text(c))

    def _on_field(self) -> None:
        c = self.selected()
        if self._loading or c is None:
            return
        c.fade_in = round(self.fade_in.value(), 2)
        c.fade_out = round(self.fade_out.value(), 2)
        c.loop = self.loop_cb.isChecked()
        c.duck = self.duck_cb.isChecked()
        self._update_row()
        self.changed.emit()

    def _on_volume(self, v: int) -> None:
        self.volume_lbl.setText(f"{v}%")
        c = self.selected()
        if self._loading or c is None:
            return
        c.volume = round(v / 100, 3)
        self._update_row()
        self.changed.emit()

    def _clamp(self, t: float) -> float:
        return max(0.0, min(t, self._duration)) if self._duration else max(0.0, t)

    def _on_start(self) -> None:
        c = self.selected()
        t = parse_time(self.start_edit.text())
        if c is None or t is None:
            if c is not None:
                self.start_edit.setText(fmt_time(c.start))
            return
        t = self._clamp(t)
        if abs(t - c.start) > 1e-3:
            self.update_selected(start=round(t, 2))

    def _on_end(self) -> None:
        c = self.selected()
        t = parse_time(self.end_edit.text()) if self.end_edit.text().strip() else None
        if c is None:
            return
        if t is None:
            self.end_edit.setText("" if c.end is None else fmt_time(c.end))
            return
        t = self._clamp(t)
        if c.end is None or abs(t - c.end) > 1e-3:
            self.update_selected(end=round(t, 2))

    def _on_to_end(self, on: bool) -> None:
        c = self.selected()
        if self._loading or c is None:
            return
        if on:
            self.update_selected(end=None)
        else:
            # a concrete end: the clip's start + 30 s (or the video's end), editable after
            end = c.start + 30.0
            if self._duration:
                end = min(end, self._duration)
            self.update_selected(end=round(max(end, c.start + 1.0), 2))

    def _delete(self) -> None:
        i = self.list.currentRow()
        if 0 <= i < len(self._clips):
            del self._clips[i]
            self._refresh(select=min(i, len(self._clips) - 1))
            self.changed.emit()
