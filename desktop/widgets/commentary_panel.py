"""Inspector tab "Bình luận" (F-4): the user's own commentary notes.

Platforms accept a reused clip when the poster adds something of their own — a
storyline, commentary (docs/plan/F-roadmap.md). Each note is placed at a moment of the
video and either freezes the picture while it is spoken ("Dừng hình") or is spoken over
the running video ("Nói đè"). The panel only edits the list; EditorView saves it
(commentary.json), knows the playhead and the cues, and plays previews.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.pipeline import commentary as cm
from desktop.cuemodel import fmt_time, parse_time
from desktop.theme import V2
from desktop.widgets.chrome import button, label

def _voice_name(v: str) -> str:
    from app.providers.registry import voice_label

    return voice_label(v)


class CommentaryPanel(QWidget):
    changed = Signal()            # a note was added / edited / removed → save
    seekRequested = Signal(float)
    addRequested = Signal(str)    # "here" | "intro" | "outro" — EditorView picks the time
    hereRequested = Signal()      # move the selected note to the playhead
    previewRequested = Signal()   # listen to the selected note
    importRequested = Signal()    # use a recording for the selected note

    def __init__(self) -> None:
        super().__init__()
        self._notes: list[cm.Note] = []
        self._duration = 0.0
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.addWidget(label("Bình luận của bạn", "h3"), 1)
        self.share_lbl = label("", "mono")
        head.addWidget(self.share_lbl)
        v.addLayout(head)
        v.addWidget(label("YouTube, Facebook và TikTok chỉ coi video đăng lại là của bạn khi có "
                          "phần bình luận / cốt truyện của riêng bạn. Giọng bình luận khác giọng "
                          "lồng tiếng.", "small", wrap=True))
        adds = QHBoxLayout()
        adds.setSpacing(6)
        self.add_here = button("Tại vị trí phát", "Plus", small=True,
                               tip="Thêm bình luận ở chỗ video đang dừng (sau câu đang nói)")
        self.add_intro = button("Mở đầu", "Plus", variant="ghost", small=True)
        self.add_outro = button("Kết thúc", "Plus", variant="ghost", small=True)
        for b, kind in ((self.add_here, "here"), (self.add_intro, "intro"),
                        (self.add_outro, "outro")):
            b.clicked.connect(lambda _c=False, k=kind: self.addRequested.emit(k))
            adds.addWidget(b)
        adds.addStretch(1)
        v.addLayout(adds)

        self.list = QListWidget()
        self.list.setMinimumHeight(64)
        self.list.setMaximumHeight(112)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setStyleSheet(
            f"QListWidget{{background:{V2['panel']};border:1px solid {V2['line']};"
            f"border-radius:6px;}}"
            f"QListWidget::item{{padding:3px 6px;}}"
            f"QListWidget::item:selected{{background:{V2['purple_soft']};color:{V2['text']};}}")
        self.list.currentRowChanged.connect(self._on_row)
        self.list.itemDoubleClicked.connect(
            lambda _it: self.seekRequested.emit(self.selected().at) if self.selected() else None)
        v.addWidget(self.list)

        self.detail = QFrame()
        self.detail.setObjectName("inset")
        d = QVBoxLayout(self.detail)
        d.setContentsMargins(10, 8, 10, 8)
        d.setSpacing(6)
        trow = QHBoxLayout()
        trow.addWidget(label("Thời điểm", "secondary"))
        self.time_edit = QLineEdit()
        self.time_edit.setFixedWidth(84)
        self.time_edit.editingFinished.connect(self._on_time)
        trow.addWidget(self.time_edit)
        self.here_btn = button("Đặt = vị trí phát", "Clock3", variant="ghost", small=True)
        self.here_btn.clicked.connect(self.hereRequested)
        trow.addWidget(self.here_btn)
        trow.addStretch(1)
        d.addLayout(trow)
        mrow = QHBoxLayout()
        self.mode_pause = QRadioButton("Dừng hình khi nói")
        self.mode_pause.setToolTip("Video đứng hình, tiếng gốc tạm dừng trong lúc bình luận — "
                                   "không đè lên lời thoại. Video dài thêm.")
        self.mode_over = QRadioButton("Nói đè lên video")
        self.mode_over.setToolTip("Video vẫn chạy, tiếng gốc nhỏ lại — nên đặt ở chỗ không có "
                                  "lời thoại.")
        self._modes = QButtonGroup(self)
        for b in (self.mode_pause, self.mode_over):
            self._modes.addButton(b)
            mrow.addWidget(b)
        mrow.addStretch(1)
        self._modes.buttonToggled.connect(lambda *_: self._on_field())
        d.addLayout(mrow)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Bạn muốn nói gì ở đoạn này?")
        self.text_edit.setFixedHeight(72)
        self.text_edit.textChanged.connect(self._on_field)
        d.addWidget(self.text_edit)
        vrow = QHBoxLayout()
        vrow.addWidget(label("Giọng", "secondary"))
        self.voice = QComboBox()
        self.voice.currentIndexChanged.connect(lambda _i: self._on_field())
        vrow.addWidget(self.voice, 1)
        d.addLayout(vrow)
        self.rec_lbl = label("", "small", wrap=True)
        d.addWidget(self.rec_lbl)
        brow = QHBoxLayout()
        self.listen_btn = button("Nghe thử", "Play", variant="ghost", small=True)
        self.listen_btn.clicked.connect(self.previewRequested)
        self.rec_btn = button("Dùng file ghi âm…", "Mic", variant="ghost", small=True,
                              tip="Dùng giọng thật của bạn (wav / mp3 / m4a) thay cho giọng đọc")
        self.rec_btn.clicked.connect(self.importRequested)
        self.unrec_btn = button("Bỏ file ghi âm", "X", variant="ghost", small=True)
        self.unrec_btn.clicked.connect(self._drop_recording)
        self.del_btn = button("Xóa", "Trash2", variant="ghost", small=True)
        self.del_btn.clicked.connect(self._delete)
        for b in (self.listen_btn, self.rec_btn, self.unrec_btn):
            brow.addWidget(b)
        brow.addStretch(1)
        brow.addWidget(self.del_btn)
        d.addLayout(brow)
        self.warn_lbl = label("", "small", wrap=True)
        self.warn_lbl.setStyleSheet(f"color:{V2['amber']};")
        d.addWidget(self.warn_lbl)
        v.addWidget(self.detail)
        v.addStretch(1)
        self.set_voices([], None)
        self._refresh()

    # --- data in / out -------------------------------------------------------------
    def set_notes(self, notes: list[cm.Note], duration: float = 0.0) -> None:
        self._notes = sorted(notes, key=lambda n: n.at)
        self._duration = duration or self._duration
        self._refresh(select=0 if self._notes else -1)

    def set_duration(self, duration: float) -> None:
        self._duration = duration
        self._update_share()

    def notes(self) -> list[cm.Note]:
        return list(self._notes)

    def selected(self) -> cm.Note | None:
        i = self.list.currentRow()
        return self._notes[i] if 0 <= i < len(self._notes) else None

    def set_voices(self, voices: list[str], dub_voice: str | None) -> None:
        self._loading = True
        self.voice.clear()
        auto = cm.pick_voice(voices, dub_voice)
        self.voice.addItem("Tự chọn — khác giọng lồng tiếng"
                           + (f" ({_voice_name(auto)})" if auto else ""), None)
        for vv in voices:
            self.voice.addItem(_voice_name(vv), vv)
        self._loading = False
        self._show(self.selected())

    def add(self, note: cm.Note) -> None:
        self._notes.append(note)
        self._notes.sort(key=lambda n: n.at)
        self._refresh(select=self._notes.index(note))
        self.changed.emit()
        self.text_edit.setFocus()

    def update_selected(self, **fields) -> None:
        n = self.selected()
        if n is None:
            return
        for k, val in fields.items():
            setattr(n, k, val)
        self._notes.sort(key=lambda x: x.at)
        self._refresh(select=self._notes.index(n))
        self.changed.emit()

    def set_warning(self, text: str) -> None:
        self.warn_lbl.setText(text)
        self.warn_lbl.setVisible(bool(text))

    # --- internals -------------------------------------------------------------------
    def _row_text(self, n: cm.Note) -> str:
        mode = "Dừng hình" if n.mode == "pause" else "Nói đè"
        what = "(ghi âm) " if n.audio else ""
        text = n.text.strip().replace("\n", " ") or "…"
        return f"{fmt_time(n.at)}  ·  {mode}  ·  {what}{text[:48]}"

    def _refresh(self, select: int | None = None) -> None:
        row = self.list.currentRow() if select is None else select
        self.list.blockSignals(True)
        self.list.clear()
        for n in self._notes:
            self.list.addItem(QListWidgetItem(self._row_text(n)))
        self.list.blockSignals(False)
        if 0 <= row < len(self._notes):
            self.list.setCurrentRow(row)
        self._show(self.selected())
        self._update_share()

    def _update_share(self) -> None:
        if not self._notes:
            self.share_lbl.setText("chưa có")
            return
        secs, share = cm.estimate(self._notes, self._duration)
        self.share_lbl.setText(f"{len(self._notes)} đoạn · ~{secs:.0f} s · {share * 100:.1f}%")
        self.share_lbl.setToolTip("Ước tính thời lượng bình luận và tỉ lệ trên toàn video "
                                  "(số chính xác có sau khi dựng)")

    def _on_row(self, _i: int) -> None:
        self._show(self.selected())

    def _show(self, n: cm.Note | None) -> None:
        self.detail.setVisible(n is not None)
        if n is None:
            return
        self._loading = True
        self.time_edit.setText(fmt_time(n.at))
        (self.mode_pause if n.mode == "pause" else self.mode_over).setChecked(True)
        if self.text_edit.toPlainText() != n.text:
            self.text_edit.setPlainText(n.text)
        i = self.voice.findData(n.voice) if n.voice else 0
        self.voice.setCurrentIndex(max(i, 0))
        self.rec_lbl.setText(f"Đang dùng file ghi âm: {n.audio.rsplit('/', 1)[-1]}" if n.audio
                             else "")
        self.rec_lbl.setVisible(bool(n.audio))
        self.unrec_btn.setVisible(bool(n.audio))
        self.voice.setEnabled(not n.audio)
        self._loading = False

    def _on_field(self) -> None:
        n = self.selected()
        if self._loading or n is None:
            return
        n.text = self.text_edit.toPlainText()
        n.mode = "pause" if self.mode_pause.isChecked() else "over"
        n.voice = self.voice.currentData()
        item = self.list.item(self.list.currentRow())
        if item is not None:
            item.setText(self._row_text(n))
        self._update_share()
        self.changed.emit()

    def _on_time(self) -> None:
        n = self.selected()
        t = parse_time(self.time_edit.text())
        if n is None or t is None:
            if n is not None:
                self.time_edit.setText(fmt_time(n.at))
            return
        t = max(0.0, min(t, self._duration)) if self._duration else max(0.0, t)
        if abs(t - n.at) > 1e-3:
            self.update_selected(at=t)

    def _drop_recording(self) -> None:
        self.update_selected(audio=None)

    def _delete(self) -> None:
        i = self.list.currentRow()
        if 0 <= i < len(self._notes):
            del self._notes[i]
            self._refresh(select=min(i, len(self._notes) - 1))
            self.changed.emit()
