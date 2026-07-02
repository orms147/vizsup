"""Screen 1 — Input / Setup: source + subtitle-source choice + per-stage engine
pickers + start, with a live progress/log panel. Emits startRequested(opts).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.theme import C
from desktop.widgets.provider_combo import ProviderCombo


def _section(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("section")
    return lb


class InputView(QWidget):
    startRequested = Signal(dict)
    cancelRequested = Signal()
    resumeRequested = Signal(str)  # job_id of a recent project to reopen
    deleteRequested = Signal(str)  # job_id of a recent project to delete

    def __init__(self, providers: dict) -> None:
        super().__init__()
        self._file: str | None = None
        self._srt: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        wrap = QWidget()
        wrap.setMaximumWidth(820)
        col = QVBoxLayout(wrap)
        col.setContentsMargins(32, 28, 32, 28)
        col.setSpacing(14)

        title = QLabel("Tạo dự án lồng tiếng mới")
        title.setObjectName("h1")
        sub = QLabel("Dán liên kết Douyin / Bilibili hoặc chọn tệp. vizsup sẽ lấy phụ đề tiếng Trung, "
                     "dịch và để bạn duyệt trước khi lồng tiếng.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(sub)

        # recent projects (resume to the edit gate without re-downloading)
        self._recent_box = QWidget()
        self._recent_lay = QVBoxLayout(self._recent_box)
        self._recent_lay.setContentsMargins(0, 0, 0, 0)
        self._recent_lay.setSpacing(6)
        col.addWidget(self._recent_box)
        self._build_recent()

        # source row
        col.addWidget(_section("Nguồn video"))
        srow = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://www.douyin.com/video/…  hoặc  https://www.bilibili.com/video/…")
        self.url.textChanged.connect(self._refresh_start)
        pick = QPushButton("Chọn tệp…")
        pick.setObjectName("ghost")
        pick.clicked.connect(self._pick_file)
        srow.addWidget(self.url, 1)
        srow.addWidget(pick)
        col.addLayout(srow)
        self.file_lbl = QLabel("")
        self.file_lbl.setObjectName("mono")
        self.file_lbl.setVisible(False)
        col.addWidget(self.file_lbl)

        # subtitle source
        col.addSpacing(4)
        col.addWidget(_section("Nguồn phụ đề tiếng Trung"))
        self.src_group = QButtonGroup(self)
        self.rb_auto = QRadioButton("  Tự động phát hiện (khuyên dùng) — dò chữ cháy trên video rồi tự chọn OCR/ASR")
        self.rb_asr = QRadioButton("  Tự nhận dạng giọng nói (ASR) — video chỉ có âm thanh, không có chữ")
        self.rb_ocr = QRadioButton("  Đọc chữ cháy trên video (OCR) — video đã có sẵn phụ đề tiếng Trung")
        self.rb_srt = QRadioButton("  Dùng file .srt tiếng Trung có sẵn")
        self.rb_auto.setChecked(True)
        for rb in (self.rb_auto, self.rb_asr, self.rb_ocr, self.rb_srt):
            self.src_group.addButton(rb)
            col.addWidget(rb)
        self.srt_lbl = QLabel("")
        self.srt_lbl.setObjectName("mono")
        self.srt_lbl.setVisible(False)
        col.addWidget(self.srt_lbl)

        # OCR region — for subtitles that aren't in the default bottom strip
        self._ocr_region_lbl = QLabel("Vùng đọc OCR")
        self._ocr_region_lbl.setStyleSheet(f"color:{C['muted']};font-size:12px;")
        self.ocr_region = QComboBox()
        for label, key in (("Dưới màn hình (mặc định)", "bottom-third"),
                           ("Nửa dưới", "bottom-half"),
                           ("Giữa màn hình", "middle-third"),
                           ("Trên màn hình", "top-third"),
                           ("Toàn khung (phụ đề di động)", "full")):
            self.ocr_region.addItem(label, key)
        col.addWidget(self._ocr_region_lbl)
        col.addWidget(self.ocr_region)

        # opt-in: after OCR, let the (already-picked) LLM fix recognition errors in
        # the Chinese lines. Text only — no frames uploaded. Off by default (costs API).
        self.ocr_proofread = QCheckBox("  Dùng AI sửa lỗi OCR (dùng bộ dịch bản nháp · chỉ gửi chữ · tốn API)")
        self.ocr_proofread.setToolTip(
            "Sau khi OCR, gửi các dòng chữ Trung cho bộ dịch bản nháp để sửa lỗi nhận dạng "
            "(không gửi hình ảnh). Mặc định tắt vì có tốn API."
        )
        col.addWidget(self.ocr_proofread)

        # engines
        col.addSpacing(4)
        col.addWidget(_section("Công cụ xử lý"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(13)
        grid.setVerticalSpacing(10)
        self.asr = ProviderCombo(providers["asr"])
        self.draft = ProviderCombo(providers["translation"])
        # refine list greys engines whose refine pass would be a silent no-op
        self.refine = ProviderCombo(providers["translation"], allow_none=True,
                                    require="can_refine")
        self.tts = ProviderCombo(providers["tts"])
        self._asr_caption = QLabel("Nhận dạng giọng nói (ASR)")
        self._asr_caption.setStyleSheet(f"color:{C['muted']};font-size:12px;")
        labelled = [
            (self._asr_caption, self.asr),
            (QLabel("Dịch — bản nháp"), self.draft),
            (QLabel("Dịch — tinh chỉnh (tùy chọn)"), self.refine),
            (QLabel("Giọng lồng tiếng (TTS)"), self.tts),
        ]
        for r, (cap, w) in enumerate(labelled):
            if cap.styleSheet() == "":
                cap.setStyleSheet(f"color:{C['muted']};font-size:12px;")
            grid.addWidget(cap, (r // 2) * 2, r % 2)
            grid.addWidget(w, (r // 2) * 2 + 1, r % 2)
        col.addLayout(grid)

        # start
        col.addSpacing(8)
        srow2 = QHBoxLayout()
        self.start = QPushButton("▶  Bắt đầu")
        self.start.setObjectName("primary")
        self.start.setFixedHeight(42)
        self.start.clicked.connect(self._emit_start)
        self.hint = QLabel("Dán liên kết hoặc chọn tệp để bắt đầu")
        self.hint.setObjectName("muted")
        srow2.addWidget(self.start)
        srow2.addWidget(self.hint, 1)
        col.addLayout(srow2)

        # progress (hidden until busy)
        self.prog_box = QWidget()
        pb = QVBoxLayout(self.prog_box)
        pb.setContentsMargins(0, 10, 0, 0)
        self.status = QLabel("Đang chuẩn bị…")
        self.status.setWordWrap(True)  # long errors (e.g. cookie help) wrap instead of truncating
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.log = QPlainTextEdit()
        self.log.setObjectName("mono")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(160)
        self.cancel = QPushButton("✕  Hủy")
        self.cancel.setObjectName("danger")
        self.cancel.clicked.connect(self.cancelRequested)
        prow = QHBoxLayout()
        prow.addWidget(self.status, 1)
        prow.addWidget(self.cancel)
        pb.addLayout(prow)
        pb.addWidget(self.bar)
        pb.addWidget(self.log)
        self.prog_box.setVisible(False)
        col.addWidget(self.prog_box)

        col.addStretch(1)

        # Whole screen scrolls: the form + live log can exceed the window height,
        # so without this the bottom (e.g. a long error in the log, and its own
        # scrollbar) is pushed off-screen and unreachable.
        center = QWidget()
        clay = QHBoxLayout(center)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.addStretch(1)
        clay.addWidget(wrap)
        clay.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(center)
        outer.addWidget(self._scroll)

        # wire subtitle-source behavior (after asr combo exists)
        self.rb_srt.toggled.connect(self._on_srt_radio)
        self.src_group.buttonToggled.connect(self._on_source_changed)
        self._apply_saved_choices()
        self._on_source_changed()
        self._refresh_start()

    def _apply_saved_choices(self) -> None:
        st = store.load_ui_state()
        src = st.get("subtitle_source")
        if src == "ocr":  # don't auto-restore 'srt' (would pop a file dialog)
            self.rb_ocr.setChecked(True)
        elif src == "asr":
            self.rb_asr.setChecked(True)  # anything else keeps the 'auto' default
        self.ocr_proofread.setChecked(bool(st.get("ocr_proofread")))
        ix = self.ocr_region.findData(st.get("ocr_region"))
        if ix >= 0:
            self.ocr_region.setCurrentIndex(ix)
        self.asr.select(st.get("asr"))
        self.draft.select(st.get("translator"))
        self.refine.select(st.get("refine"))
        self.tts.select(st.get("tts"))

    # --- helpers -------------------------------------------------------------
    def _build_recent(self) -> None:
        while self._recent_lay.count():
            item = self._recent_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        recent = store.list_jobs()
        self._recent_box.setVisible(bool(recent))
        if not recent:
            return
        self._recent_lay.addWidget(_section("Dự án gần đây"))
        for j in recent:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            status = "đã dựng video" if j["has_output"] else "sửa phụ đề"
            open_b = QPushButton(f"  {j['title']}   ·   {status}")
            open_b.setObjectName("ghost")
            open_b.setStyleSheet("text-align:left;")
            open_b.clicked.connect(lambda _=False, jid=j["id"]: self.resumeRequested.emit(jid))
            del_b = QPushButton("Xóa")
            del_b.setObjectName("danger")
            del_b.setFixedWidth(58)
            del_b.clicked.connect(lambda _=False, jid=j["id"]: self.deleteRequested.emit(jid))
            rl.addWidget(open_b, 1)
            rl.addWidget(del_b)
            self._recent_lay.addWidget(row)

    def refresh_recent(self) -> None:
        self._build_recent()

    def _pick_file(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn video", "", "Video (*.mp4 *.mkv *.mov *.webm);;All (*.*)")
        if fn:
            self._file = fn
            self.file_lbl.setText(f"📄 {Path(fn).name}")
            self.file_lbl.setVisible(True)
            self.url.clear()
            self._refresh_start()

    def _on_source_changed(self, *_args) -> None:
        # auto may end up on either path → keep both option groups live for it
        asr_on = self.rb_asr.isChecked() or self.rb_auto.isChecked()
        ocr_on = self.rb_ocr.isChecked() or self.rb_auto.isChecked()
        self.asr.setEnabled(asr_on)
        self._asr_caption.setEnabled(asr_on)
        self.ocr_region.setEnabled(ocr_on)
        self._ocr_region_lbl.setEnabled(ocr_on)
        self.ocr_proofread.setEnabled(ocr_on)

    def _on_srt_radio(self, on: bool) -> None:
        if on:
            fn, _ = QFileDialog.getOpenFileName(self, "Chọn .srt tiếng Trung", "", "Subtitle (*.srt)")
            if fn:
                self._srt = fn
                self.srt_lbl.setText(f"📑 {Path(fn).name}")
                self.srt_lbl.setVisible(True)
            else:
                self.rb_asr.setChecked(True)  # cancelled → revert
        else:
            self._srt = None
            self.srt_lbl.setVisible(False)

    def _refresh_start(self) -> None:
        ok = bool(self.url.text().strip()) or bool(self._file)
        self.start.setEnabled(ok)
        self.hint.setText("Sẵn sàng — nhấn Bắt đầu" if ok else "Dán liên kết hoặc chọn tệp để bắt đầu")

    def _subtitle_source(self) -> str:
        if self.rb_srt.isChecked():
            return "srt"
        if self.rb_ocr.isChecked():
            return "ocr"
        if self.rb_asr.isChecked():
            return "asr"
        return "auto"

    def _emit_start(self) -> None:
        opts: dict = {
            "subtitle_source": self._subtitle_source(),
            "asr": self.asr.current_name(),
            "translator": self.draft.current_name(),
            "refine": self.refine.current_name(),
            "tts": self.tts.current_name(),
            "ocr_region": self.ocr_region.currentData(),
            "ocr_proofread": self.ocr_proofread.isChecked(),
        }
        if self._file:
            opts["file"] = self._file
        else:
            opts["url"] = self.url.text().strip()
        if opts["subtitle_source"] == "srt" and self._srt:
            opts["srt"] = self._srt
        store.save_ui_state(opts)
        self.startRequested.emit(opts)

    # --- progress API (driven by MainWindow) ---------------------------------
    def refresh_providers(self, providers: dict) -> None:
        self.asr.update_availability(providers["asr"])
        self.draft.update_availability(providers["translation"])
        self.refine.update_availability(providers["translation"])
        self.tts.update_availability(providers["tts"])

    def set_busy(self, busy: bool) -> None:
        if busy:
            self.log.clear()
            self.prog_box.setVisible(True)  # keep visible after the run so errors stay readable
            self._scroll.ensureWidgetVisible(self.prog_box)  # scroll the page down to the log
        self.start.setEnabled(not busy)

    def set_progress(self, frac: float, msg: str) -> None:
        self.bar.setValue(int(frac * 100))
        self.status.setText(msg)

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def show_error(self, msg: str) -> None:
        self.status.setText(f"Lỗi: {msg}")
        self.append_log(f"✕ {msg}")
        self.start.setEnabled(True)
        self._scroll.ensureWidgetVisible(self.prog_box)  # bring the full error into view
