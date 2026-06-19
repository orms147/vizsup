"""Screen 1 — Input / Setup: source + subtitle-source choice + per-stage engine
pickers + start, with a live progress/log panel. Emits startRequested(opts).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import C
from desktop.widgets.provider_combo import ProviderCombo


def _section(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("section")
    return lb


class InputView(QWidget):
    startRequested = Signal(dict)
    cancelRequested = Signal()

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
        self.rb_asr = QRadioButton("  Tự nhận dạng giọng nói (ASR) — video chỉ có âm thanh, không có chữ")
        self.rb_ocr = QRadioButton("  Đọc chữ cháy trên video (OCR) — video đã có sẵn phụ đề tiếng Trung")
        self.rb_srt = QRadioButton("  Dùng file .srt tiếng Trung có sẵn")
        self.rb_asr.setChecked(True)
        for rb in (self.rb_asr, self.rb_ocr, self.rb_srt):
            self.src_group.addButton(rb)
            col.addWidget(rb)
        self.srt_lbl = QLabel("")
        self.srt_lbl.setObjectName("mono")
        self.srt_lbl.setVisible(False)
        col.addWidget(self.srt_lbl)

        # engines
        col.addSpacing(4)
        col.addWidget(_section("Công cụ xử lý"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(13)
        grid.setVerticalSpacing(10)
        self.asr = ProviderCombo(providers["asr"])
        self.draft = ProviderCombo(providers["translation"])
        self.refine = ProviderCombo(providers["translation"], allow_none=True)
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
        outer.addWidget(wrap, 0, Qt.AlignHCenter)

        # wire subtitle-source behavior (after asr combo exists)
        self.rb_srt.toggled.connect(self._on_srt_radio)
        self.src_group.buttonToggled.connect(self._on_source_changed)
        self._on_source_changed()
        self._refresh_start()

    # --- helpers -------------------------------------------------------------
    def _pick_file(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn video", "", "Video (*.mp4 *.mkv *.mov *.webm);;All (*.*)")
        if fn:
            self._file = fn
            self.file_lbl.setText(f"📄 {Path(fn).name}")
            self.file_lbl.setVisible(True)
            self.url.clear()
            self._refresh_start()

    def _on_source_changed(self, *_args) -> None:
        # ASR engine only matters when the ASR source is chosen
        self.asr.setEnabled(self.rb_asr.isChecked())
        self._asr_caption.setEnabled(self.rb_asr.isChecked())

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
        return "asr"

    def _emit_start(self) -> None:
        opts: dict = {
            "subtitle_source": self._subtitle_source(),
            "asr": self.asr.current_name(),
            "translator": self.draft.current_name(),
            "refine": self.refine.current_name(),
            "tts": self.tts.current_name(),
        }
        if self._file:
            opts["file"] = self._file
        else:
            opts["url"] = self.url.text().strip()
        if opts["subtitle_source"] == "srt" and self._srt:
            opts["srt"] = self._srt
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
