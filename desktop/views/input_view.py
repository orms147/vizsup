"""Screen 1 — Bắt đầu (v2, appdesign/v2 "v2 — Bắt đầu"): one hero link box, optional
context note and options (collapsed by default), a running stage list with Hủy, and
inline error cards (Douyin cookie in three steps · network · missing key). The aside
lists recent projects to reopen at the edit gate. Emits startRequested(opts).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.cuemodel import fmt_time
from desktop.icons import pixmap
from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import badge, button, icon_button, icon_label, label
from desktop.widgets.provider_combo import ProviderCombo
from desktop.widgets.recent_projects import RecentProjects, short_title

SOURCES = (("auto", "Tự động · khuyên dùng"), ("asr", "AI nghe âm thanh"),
           ("ocr", "Đọc chữ cháy trên video (OCR)"), ("srt", "Tệp phụ đề .srt có sẵn"))
OCR_REGIONS = (("Phía dưới (mặc định)", "bottom-third"), ("Nửa dưới", "bottom-half"),
               ("Giữa màn hình", "middle-third"), ("Trên màn hình", "top-third"),
               ("Toàn khung (phụ đề di động)", "full"))
_CLIP_URL = re.compile(r"https?://[^\s]*(?:douyin\.com|iesdouyin\.com|bilibili\.com|b23\.tv)[^\s]*",
                       re.I)
_ALL_URLS = re.compile(r"https?://[^\s]+")
STAGES = (("download", "Tải video"), ("subtitle", "Lấy phụ đề tiếng Trung"),
          ("context", "Đọc ngữ cảnh"), ("translate", "Dịch sang tiếng Việt"))


def _field(title: str, w: QWidget) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(5)
    box.addWidget(label(title, "small"))
    box.addWidget(w)
    return box


class _StageRow(QWidget):
    def __init__(self, num: int, name: str) -> None:
        super().__init__()
        self._num = num
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(12)
        self.dot = QLabel()
        self.dot.setFixedSize(26, 26)
        self.dot.setAlignment(Qt.AlignCenter)
        h.addWidget(self.dot, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = label(name)
        self.detail = label("Chờ đến lượt", "small")
        self.detail.setWordWrap(True)
        col.addWidget(self.name)
        col.addWidget(self.detail)
        h.addLayout(col, 1)
        self.dur = label("", "mono")
        h.addWidget(self.dur, 0, Qt.AlignTop)
        self.set_state("wait")

    def set_state(self, state: str, detail: str | None = None, dur: str = "") -> None:
        self.state = state
        style = {"done": (V2["green_soft"], V2["green"]), "active": (V2["purple_soft"], V2["purple"]),
                 "error": (V2["red_soft"], V2["red"])}.get(state, (V2["raised"], V2["line"]))
        self.dot.setStyleSheet(f"background:{style[0]};border:1px solid {style[1]};"
                               f"border-radius:13px;color:{V2['muted']};font-size:11px;")
        if state == "done":
            self.dot.setPixmap(pixmap("Check", V2["green"], 13))
        elif state == "active":
            self.dot.setPixmap(pixmap("LoaderCircle", V2["purple_bright"], 13))
        elif state == "error":
            self.dot.setPixmap(pixmap("X", V2["red"], 13))
        elif state == "paused":
            self.dot.setPixmap(pixmap("Pause", V2["muted"], 13))
        else:
            self.dot.setPixmap(QPixmapEmpty())
            self.dot.setText(str(self._num))
        self.name.setStyleSheet(f"color:{V2['text'] if state != 'wait' else V2['muted']};"
                                f"font-weight:600;")
        if detail is not None:
            self.detail.setText(detail)
        self.dur.setText(dur)


def QPixmapEmpty():  # noqa: N802 - tiny helper: clear a QLabel's pixmap
    from PySide6.QtGui import QPixmap

    return QPixmap()


class InputView(QWidget):
    startRequested = Signal(dict)
    cancelRequested = Signal()
    resumeRequested = Signal(str)  # job_id of a recent project to reopen
    deleteRequested = Signal(str)  # job_id of a recent project to delete
    glossaryRequested = Signal()   # open the glossary dialog (global scope)
    settingsRequested = Signal()   # "Thêm khóa" on the missing-key card
    queueRequested = Signal(list)  # E-9: several links / files → run them one by one
    groupResumeRequested = Signal(str)   # a split video: queue its unfinished parts
    groupJoinRequested = Signal(str)     # a split video: join the rendered parts
    groupDeleteRequested = Signal(str)   # a split video: delete it and all its parts

    def __init__(self, providers: dict) -> None:
        super().__init__()
        self._file: str | None = None
        self._srt: str | None = None
        self._last_opts: dict | None = None
        self._busy = False
        self._t0 = 0.0
        self._stage = ""
        self._stage_t0: dict[str, float] = {}
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        # F-5: cost / time before Start — the video's length is looked up in the background
        self._bg = BgRunner(self)
        self._durations: dict[str, float | None] = {}   # url or file → seconds (None: failed)
        self._probe_errors: dict[str, str] = {}
        self._probing: set[str] = set()
        self._est_timer = QTimer(self)
        self._est_timer.setSingleShot(True)
        self._est_timer.setInterval(700)                # don't probe every keystroke
        self._est_timer.timeout.connect(self._update_estimate)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        main = QWidget()
        main.setMaximumWidth(780)
        col = QVBoxLayout(main)
        col.setContentsMargins(24, 24, 24, 24)
        col.setSpacing(18)
        intro = QVBoxLayout()
        intro.setSpacing(10)
        intro.addWidget(label("GÓC DỊCH VIDEO CỦA BẠN", "eyebrow"))
        self.headline = label("Một đường link.\nMột video của bạn.", "display")
        intro.addWidget(self.headline)
        self.tagline = label("Từ video tiếng Trung đến phụ đề và giọng đọc tiếng Việt.\n"
                             "Dán link, xem bản dịch, chỉnh cho vừa ý.", "secondary")
        intro.addWidget(self.tagline)
        col.addLayout(intro)

        col.addWidget(self._hero())
        col.addWidget(self._progress_card())
        self.alert = QFrame()
        self.alert.setVisible(False)
        self._alert_lay = QVBoxLayout(self.alert)
        col.addWidget(self.alert)

        toggles = QHBoxLayout()
        toggles.setSpacing(8)
        self.note_btn = button("Ghi chú ngữ cảnh", "BookOpen", checkable=True)
        self.opt_btn = button("Tùy chọn", "SlidersHorizontal", checkable=True)
        toggles.addWidget(self.note_btn)
        toggles.addWidget(self.opt_btn)
        toggles.addWidget(label("Không bắt buộc", "small"))
        toggles.addStretch(1)
        gl = button("Thuật ngữ", "BookOpen", variant="ghost", small=True,
                    tip="Bảng thuật ngữ Trung → Việt dùng chung — tự nạp vào bước dịch")
        gl.clicked.connect(self.glossaryRequested)
        toggles.addWidget(gl)
        self._toggles = QWidget()
        self._toggles.setLayout(toggles)
        col.addWidget(self._toggles)

        self.note_box = self._note_box()
        col.addWidget(self.note_box)
        self.options = self._options(providers)
        col.addWidget(self.options)
        col.addStretch(1)

        gate = QHBoxLayout()
        gate.setSpacing(10)
        gate.addWidget(icon_label("ShieldCheck", V2["secondary"], 16), 0, Qt.AlignTop)
        gate.addWidget(label("Bạn luôn được đọc và sửa phụ đề trước.\nChỉ lồng tiếng và dựng "
                             "khi bạn đã duyệt.", "secondary"), 1)
        gw = QFrame()
        gw.setStyleSheet(f"QFrame{{border-top:1px solid {V2['line_soft']};}} QLabel{{border:none;}}")
        gw.setLayout(gate)
        gate.setContentsMargins(0, 16, 0, 0)
        col.addWidget(gw)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(main, 6)
        hl.addStretch(0)
        scroll.setWidget(holder)
        self._scroll = scroll

        self.recent = RecentProjects()
        self.recent.openRequested.connect(self.resumeRequested)
        self.recent.deleteRequested.connect(self.deleteRequested)
        self.recent.groupResumeRequested.connect(self.groupResumeRequested)
        self.recent.groupJoinRequested.connect(self.groupJoinRequested)
        self.recent.groupDeleteRequested.connect(self.groupDeleteRequested)
        aside = QWidget()
        aside.setFixedWidth(360)
        al = QVBoxLayout(aside)
        al.setContentsMargins(28, 24, 28, 24)
        al.addWidget(self.recent)
        outer.addWidget(scroll, 1)
        vline = QFrame()
        vline.setFixedWidth(1)
        vline.setStyleSheet(f"background:{V2['line_soft']};")
        outer.addWidget(vline)
        outer.addWidget(aside)
        outer.addSpacing(40)

        self.note_btn.toggled.connect(self._toggle_note)
        self.opt_btn.toggled.connect(self._toggle_options)
        st = store.load_ui_state()
        self.opt_btn.setChecked(bool(st.get("options_open")))
        self._toggle_note(False)
        self._toggle_options(self.opt_btn.isChecked())
        self._apply_saved_choices(st)
        self._on_source_changed()
        self._refresh_start()
        self.progress.setVisible(False)

    # --- hero ------------------------------------------------------------------
    def _hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("panel")
        hero.setStyleSheet(f"QFrame#panel{{background:{V2['panel']};border-radius:13px;}}")
        v = QVBoxLayout(hero)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(14)
        cap = QHBoxLayout()
        cap.setSpacing(8)
        cap.addWidget(icon_label("Link", V2["secondary"], 16))
        cap.addWidget(label("Dán link Douyin hoặc Bilibili", "h3"))
        cap.addStretch(1)
        v.addLayout(cap)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.url = QLineEdit()
        self.url.setObjectName("hero")
        self.url.setPlaceholderText("https://www.douyin.com/video/…")
        self.url.textChanged.connect(self._refresh_start)
        self.url.returnPressed.connect(lambda: self.start.isEnabled() and self._emit_start())
        self.start = button("Bắt đầu", "ArrowRight", variant="primary")
        self.start.setMinimumWidth(122)
        self.start.setStyleSheet("QPushButton{min-height:47px;font-size:14px;padding:0 18px;}")
        self.start.clicked.connect(self._emit_start)
        row.addWidget(self.url, 1)
        row.addWidget(self.start)
        v.addLayout(row)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        pick = button("Chọn video trên máy", "Upload", variant="ghost", small=True)
        pick.clicked.connect(self._pick_file)
        foot.addWidget(pick)
        foot.addWidget(label("hoặc kéo thả video vào đây", "small"))
        self.file_badge = badge("", "purple")
        self.file_badge.setVisible(False)
        foot.addWidget(self.file_badge)
        foot.addStretch(1)
        v.addLayout(foot)
        self.defaults = label("", "small")
        v.addWidget(self.defaults)
        self.estimate = label("", "small")
        self.estimate.setVisible(False)
        v.addWidget(self.estimate)
        self.hint = label("", "small")
        self.hint.setVisible(False)
        v.addWidget(self.hint)

        # E-10: a Douyin / Bilibili link just copied elsewhere → one click to start
        self.clip_chip = QFrame()
        self.clip_chip.setObjectName("inset")
        ch = QHBoxLayout(self.clip_chip)
        ch.setContentsMargins(10, 6, 6, 6)
        ch.setSpacing(8)
        ch.addWidget(icon_label("Link", V2["purple_bright"], 14))
        ch.addWidget(label("Dùng link vừa copy?", "secondary"))
        self.clip_url = label("", "mono")
        ch.addWidget(self.clip_url, 1)
        use = button("Dùng", "ArrowRight", variant="primary", small=True)
        use.clicked.connect(self._use_clipboard)
        ch.addWidget(use)
        close = icon_button("X", "Bỏ qua link này")
        close.clicked.connect(self._dismiss_clipboard)
        ch.addWidget(close)
        self.clip_chip.setVisible(False)
        self._clip: str | None = None
        self._clip_dismissed: set[str] = set()
        v.addWidget(self.clip_chip)
        self.setAcceptDrops(True)
        self._hero_frame = hero
        return hero

    def offer_clipboard(self, text: str, *, known: set[str] = frozenset()) -> bool:
        """Show the chip for a Douyin / Bilibili link found in ``text`` that is not
        already typed, not an existing project and not dismissed. Never starts."""
        m = _CLIP_URL.search(text or "")
        url = m.group(0).rstrip(".,;)」】") if m else None
        if (self._busy or not url or url == self.url.text().strip() or url in known
                or url in self._clip_dismissed):
            return False
        self._clip = url
        self.clip_url.setText(url if len(url) <= 52 else url[:49] + "…")
        self.clip_url.setToolTip(url)
        self.clip_chip.setVisible(True)
        return True

    def _use_clipboard(self) -> None:
        if self._clip:
            self.url.setText(self._clip)
            self._clip_dismissed.add(self._clip)
            self.clip_chip.setVisible(False)
            if self.start.isEnabled():
                self._emit_start()

    def _dismiss_clipboard(self) -> None:
        if self._clip:
            self._clip_dismissed.add(self._clip)
        self.clip_chip.setVisible(False)

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls() and not self._busy:
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        urls = ev.mimeData().urls()
        files = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if len(files) > 1:                   # E-9: several videos dropped → a queue
            base = self._collect_opts()
            self.queueRequested.emit([{**base, "file": f, "url": None} for f in files])
            return
        for u in urls:
            if u.isLocalFile():
                self._set_file(u.toLocalFile())
                return
            self.url.setText(u.toString())
            return

    # --- note + options -------------------------------------------------------
    def _note_box(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(label("Ai nói với ai? Tên riêng hoặc cách xưng hô bạn muốn dùng.", "small"))
        self.context_note = QPlainTextEdit()
        self.context_note.setPlaceholderText(
            "Ví dụ: vlog nấu ăn của một cô gái Tứ Xuyên, nói thân mật với người xem; "
            "\"老李\" là chồng cô ấy, gọi là \"anh Lý\". AI viết lại thành brief đầy đủ và "
            "LUÔN theo thông tin của bạn.")
        self.context_note.setFixedHeight(74)
        self.context_note.setTabChangesFocus(True)
        v.addWidget(self.context_note)
        return w

    def _options(self, providers: dict) -> QFrame:
        box = QFrame()
        box.setObjectName("panel")
        g = QGridLayout(box)
        g.setContentsMargins(16, 14, 16, 14)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(12)
        self.source = QComboBox()
        for key, text in SOURCES:
            self.source.addItem(text, key)
        self.source.currentIndexChanged.connect(self._on_source_changed)
        g.addLayout(_field("Nguồn phụ đề tiếng Trung", self.source), 0, 0, 1, 2)
        self.ocr_region = QComboBox()
        for text, key in OCR_REGIONS:
            self.ocr_region.addItem(text, key)
        self._ocr_field = QWidget()
        self._ocr_field.setLayout(_field("Vùng đọc chữ trên video", self.ocr_region))
        self._ocr_field.layout().setContentsMargins(0, 0, 0, 0)
        g.addWidget(self._ocr_field, 1, 0)
        self.ocr_engine = ProviderCombo(providers.get("ocr") or [])
        self.ocr_engine.setToolTip(
            "Trên máy: miễn phí, không gửi gì đi, nhưng chậm (~0,3–0,5 s mỗi khung) và hay đọc sót "
            "chữ nghệ thuật / nền rối. AI qua mạng: gửi dải phụ đề của từng khung lên OpenRouter, "
            "đọc đúng hơn hẳn, ~$0,1 cho video 3 giờ, không dùng GPU của máy.")
        self._ocr_engine_field = QWidget()
        self._ocr_engine_field.setLayout(_field("Cách đọc chữ", self.ocr_engine))
        self._ocr_engine_field.layout().setContentsMargins(0, 0, 0, 0)
        g.addWidget(self._ocr_engine_field, 1, 1)
        srt_row = QHBoxLayout()
        self.srt_btn = button("Chọn tệp phụ đề .srt", "Upload", small=True)
        self.srt_btn.clicked.connect(self._pick_srt)
        self.srt_lbl = label("", "small")
        srt_row.addWidget(self.srt_btn)
        srt_row.addWidget(self.srt_lbl, 1)
        self._srt_field = QWidget()
        self._srt_field.setLayout(srt_row)
        srt_row.setContentsMargins(0, 0, 0, 0)
        g.addWidget(self._srt_field, 2, 0, 1, 2)

        self.draft = ProviderCombo(providers["translation"])
        # refine list greys engines whose refine pass would be a silent no-op
        self.refine = ProviderCombo(providers["translation"], allow_none=True, require="can_refine")
        self.asr = ProviderCombo(providers["asr"])
        self.tts = ProviderCombo(providers["tts"])
        g.addLayout(_field("Bộ dịch bản nháp", self.draft), 3, 0)
        g.addLayout(_field("Tinh chỉnh · tùy chọn", self.refine), 3, 1)
        self._asr_field = QWidget()
        self._asr_field.setLayout(_field("Nhận dạng giọng nói", self.asr))
        self._asr_field.layout().setContentsMargins(0, 0, 0, 0)
        g.addWidget(self._asr_field, 4, 0)
        g.addLayout(_field("Giọng lồng tiếng", self.tts), 4, 1)

        self.auto_context = QCheckBox("Tự đọc ngữ cảnh video")
        self.auto_context.setToolTip(
            "Trước khi dịch, AI nghe một đoạn ≤60 s (ai nói / hát, nam hay nữ, tuổi) và đọc tiêu đề "
            "+ phụ đề tiếng Trung, rồi viết 'brief': thể loại, ai nói với ai, xưng hô, tên riêng, "
            "giọng điệu. Gửi đoạn âm thanh đó lên máy chủ AI. Tốn rất ít API.")
        self.ai_frames = QCheckBox("AI xem khung hình")
        self.ai_frames.setToolTip(
            "Gửi thêm 6 ảnh của video để AI biết ai đang xuất hiện — xưng hô chính xác hơn. "
            "Mặc định tắt vì gửi hình ảnh lên máy chủ.")
        self.extract_terms = QCheckBox("Tự trích thuật ngữ")
        self.extract_terms.setToolTip(
            "Nhờ bộ dịch liệt kê tên riêng / thương hiệu lặp lại để giữ nhất quán. Bảng thuật "
            "ngữ của bạn luôn được ưu tiên. Tốn thêm API.")
        self.ocr_proofread = QCheckBox("AI sửa lỗi OCR")
        self.ocr_proofread.setToolTip("Một lượt sửa lỗi nhận dạng cho chữ Trung (chỉ gửi chữ, "
                                      "không gửi hình). Tốn thêm API.")
        self.auto_snap = QCheckBox("Tự canh lời theo giọng nói")
        self.auto_snap.setToolTip(
            "Sau khi có phụ đề tiếng Trung, dời đầu / cuối mỗi câu tới chỗ giọng nói bắt đầu / "
            "dừng (tối đa ~1 s). Chạy trên máy (Silero VAD), không gửi âm thanh đi đâu.")
        g.addWidget(self.auto_context, 5, 0)
        g.addWidget(self.auto_snap, 5, 1)
        self.ai_review = QCheckBox("AI soát bản dịch")
        self.ai_review.setToolTip(
            "Sau khi dịch, một model mạnh đọc lại từng cặp Trung–Việt và đánh dấu câu nghi sai "
            "(nghĩa, xưng hô, tên riêng…) kèm gợi ý sửa — vào mục “Cần xem lại”. Không tự sửa.")
        g.addWidget(self.ai_review, 7, 0)
        g.addWidget(self.ai_frames, 7, 1)
        g.addWidget(self.extract_terms, 6, 0)
        g.addWidget(self.ocr_proofread, 6, 1)
        self.split_long = QCheckBox("Chia video dài thành phần ~30 phút")
        self.split_long.setToolTip(
            "Video dài hơn 45 phút được cắt (ở chỗ lặng, không cắt giữa câu, không mã hóa lại) "
            "thành các phần ~30 phút chạy lần lượt: duyệt phần 1 trong lúc phần sau còn chạy, "
            "dựng từng phần rồi ghép lại thành một video. Dùng chung ngữ cảnh và thuật ngữ.")
        g.addWidget(self.split_long, 8, 0)
        self.ocr_fast = QCheckBox("OCR nhanh: 1 khung/giây")
        self.ocr_fast.setToolTip(
            "Đọc phụ đề cháy sẵn nhanh gấp đôi. Hợp phim / phim bộ (câu dài): đo trên phim mẫu "
            "không sót câu nào, mốc thời gian lệch tối đa ~0,8 s (tự canh lời theo giọng sửa "
            "lại). Vlog chữ ngắn có thể sót câu dưới 1 giây.")
        g.addWidget(self.ocr_fast, 8, 1)
        self.asr_note = label("Âm thanh được gửi lên máy chủ AI khi dùng “AI nghe âm thanh”.",
                              "small", wrap=True)
        g.addWidget(self.asr_note, 9, 0, 1, 2)
        for w in (self.draft, self.refine, self.asr, self.tts):
            w.currentIndexChanged.connect(self._update_defaults)
        for w in (self.draft, self.refine, self.asr, self.source, self.ocr_engine):
            w.currentIndexChanged.connect(lambda *_a: self._est_timer.start())
        for cb in (self.auto_context, self.ai_review, self.split_long):
            cb.toggled.connect(lambda *_a: self._est_timer.start())
        return box

    def _toggle_note(self, on: bool) -> None:
        self.note_box.setVisible(on)
        self.note_btn.setText("Ẩn ghi chú" if on else "Ghi chú ngữ cảnh")
        if on:
            self.context_note.setFocus()

    def _toggle_options(self, on: bool) -> None:
        self.options.setVisible(on)
        self.opt_btn.setText("Ẩn tùy chọn" if on else "Tùy chọn")
        st = store.load_ui_state()
        if bool(st.get("options_open")) != on:
            st["options_open"] = on
            store.save_ui_state(st)

    def _apply_saved_choices(self, st: dict) -> None:
        src = st.get("subtitle_source")
        if src in ("ocr", "asr"):  # never auto-restore 'srt' (needs a file)
            self.source.setCurrentIndex(self.source.findData(src))
        self.ocr_proofread.setChecked(bool(st.get("ocr_proofread")))
        self.extract_terms.setChecked(bool(st.get("extract_terms")))
        self.auto_context.setChecked(bool(st.get("auto_context", True)))   # on unless turned off
        self.auto_snap.setChecked(bool(st.get("auto_snap", True)))
        self.ai_review.setChecked(bool(st.get("ai_review", True)))
        self.ai_frames.setChecked(bool(st.get("ai_frames", False)))
        self.split_long.setChecked(bool(st.get("split_long", True)))
        self.ocr_fast.setChecked(bool(st.get("ocr_fast", False)))
        self.ocr_engine.select(st.get("ocr_engine"))
        ix = self.ocr_region.findData(st.get("ocr_region"))
        if ix >= 0:
            self.ocr_region.setCurrentIndex(ix)
        self.asr.select(st.get("asr"))
        self.draft.select(st.get("translator"))
        self.refine.select(st.get("refine"))
        self.tts.select(st.get("tts"))
        self._update_defaults()

    def _subtitle_source(self) -> str:
        return self.source.currentData() or "auto"

    def _on_source_changed(self, *_a) -> None:
        src = self._subtitle_source()
        # auto may end up on either path → keep both option groups live for it
        self._ocr_field.setVisible(src in ("auto", "ocr"))
        self._ocr_engine_field.setVisible(src in ("auto", "ocr"))
        self.ocr_proofread.setEnabled(src in ("auto", "ocr"))
        self.ocr_fast.setEnabled(src in ("auto", "ocr"))
        self._asr_field.setEnabled(src in ("auto", "asr"))
        self._srt_field.setVisible(src == "srt")
        if src != "srt":
            self._srt = None
            self.srt_lbl.setText("")
        self._update_defaults()
        self._refresh_start()

    def _update_defaults(self, *_a) -> None:
        src = dict(SOURCES).get(self._subtitle_source(), "")
        src = "Nguồn tự động" if self._subtitle_source() == "auto" else src
        def name(combo) -> str:
            return combo.currentText().split(" — ")[0].split(" · ")[0].strip()

        parts = [src, name(self.draft), name(self.tts)]
        self.defaults.setText("   ·   ".join(p for p in parts if p))

    def _pick_srt(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn .srt tiếng Trung", "", "Subtitle (*.srt)")
        if fn:
            self._srt = fn
            self.srt_lbl.setText(Path(fn).name)
        self._refresh_start()

    def _pick_file(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn video", "",
                                            "Video (*.mp4 *.mkv *.mov *.webm);;All (*.*)")
        if fn:
            self._set_file(fn)

    def _set_file(self, fn: str) -> None:
        self._file = fn
        self.file_badge.setText(Path(fn).name)
        self.file_badge.setVisible(True)
        self.url.clear()
        self._refresh_start()

    def _refresh_start(self) -> None:
        has_src = bool(self.url.text().strip()) or bool(self._file)
        need_srt = self._subtitle_source() == "srt" and not self._srt
        self.start.setEnabled(has_src and not need_srt and not self._busy)
        if self.url.text().strip():
            self._file = None
            self.file_badge.setVisible(False)
        many = len(_ALL_URLS.findall(self.url.text()))
        self._est_timer.start()
        if need_srt and has_src:
            self.hint.setText("Chọn tệp phụ đề .srt trong Tùy chọn trước khi bắt đầu.")
        elif many > 1:
            self.hint.setText(f"{many} link — sẽ xử lý lần lượt tới bước duyệt, báo khi từng video xong.")
        self.hint.setVisible((need_srt and has_src) or many > 1)

    def _emit_start(self) -> None:
        opts = self._collect_opts()
        urls = _ALL_URLS.findall(self.url.text())
        if len(urls) > 1 and not self._file:          # E-9: several links pasted at once
            self._remember(opts)
            self.queueRequested.emit([{**opts, "url": u} for u in urls])
            return
        self._remember(opts)
        self._last_opts = dict(opts)
        self.startRequested.emit(opts)

    def _remember(self, opts: dict) -> None:
        st = store.load_ui_state()
        st.update({k: v for k, v in opts.items() if v is not None})
        store.save_ui_state(st)

    def _collect_opts(self) -> dict:
        opts: dict = {
            "subtitle_source": self._subtitle_source(),
            "asr": self.asr.current_name(),
            "translator": self.draft.current_name(),
            "refine": self.refine.current_name(),
            "tts": self.tts.current_name(),
            "ocr_region": self.ocr_region.currentData(),
            "ocr_engine": self.ocr_engine.current_name() or "rapidocr",
            "ocr_proofread": self.ocr_proofread.isChecked(),
            "extract_terms": self.extract_terms.isChecked(),
            "auto_context": self.auto_context.isChecked(),
            "auto_snap": self.auto_snap.isChecked(),
            "ai_review": self.ai_review.isChecked(),
            "ai_frames": self.ai_frames.isChecked(),
            "split_long": self.split_long.isChecked(),
            "ocr_fast": self.ocr_fast.isChecked(),
        }
        note = self.context_note.toPlainText().strip()
        if note:
            opts["context_note"] = note     # per-video: passed to the worker, never persisted
        if self._file:
            opts["file"] = self._file
        else:
            opts["url"] = self.url.text().strip()
        if opts["subtitle_source"] == "srt" and self._srt:
            opts["srt"] = self._srt
        return opts

    # --- progress card -----------------------------------------------------------
    def _progress_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("panel")
        card.setStyleSheet(f"QFrame#panel{{background:{V2['panel']};border-radius:13px;}}")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(14)
        thumb = QLabel()
        thumb.setFixedSize(44, 44)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setPixmap(pixmap("FileVideo", V2["purple_bright"], 20))
        thumb.setStyleSheet(f"background:{V2['raised']};border-radius:8px;")
        head.addWidget(thumb)
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        self.p_title = label("Video mới", "h2")
        self.p_meta = label("", "small")
        tcol.addWidget(self.p_title)
        tcol.addWidget(self.p_meta)
        head.addLayout(tcol, 1)
        self.p_badge = badge("0%", "purple")
        head.addWidget(self.p_badge, 0, Qt.AlignTop)
        v.addLayout(head)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        v.addWidget(self.bar)
        sub = QHBoxLayout()
        self.status = label("Đang chuẩn bị…", "small")
        self.status.setWordWrap(True)
        self.elapsed = label("00:00", "mono")
        sub.addWidget(self.status, 1)
        sub.addWidget(self.elapsed)
        v.addLayout(sub)
        self.stage_rows: dict[str, _StageRow] = {}
        for i, (key, name) in enumerate(STAGES, 1):
            row = _StageRow(i, name)
            self.stage_rows[key] = row
            v.addWidget(row)
        self.log = QPlainTextEdit()
        self.log.setObjectName("mono")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(140)
        self.log.setVisible(False)
        v.addWidget(self.log)
        foot = QHBoxLayout()
        self.log_btn = button("Xem chi tiết", "ChevronDown", variant="ghost", small=True,
                              checkable=True)
        self.log_btn.toggled.connect(self._toggle_log)
        foot.addWidget(self.log_btn)
        foot.addStretch(1)
        self.cancel = button("Hủy", "X", tip="Dừng sau bước hiện tại — các bước đã xong được giữ lại")
        self.cancel.clicked.connect(self.cancelRequested)
        foot.addWidget(self.cancel)
        v.addLayout(foot)
        self.progress = card
        return card

    def _toggle_log(self, on: bool) -> None:
        self.log.setVisible(on)
        self.log_btn.setText("Ẩn chi tiết" if on else "Xem chi tiết")

    # --- progress API (driven by MainWindow) ---------------------------------
    def refresh_providers(self, providers: dict) -> None:
        self.asr.update_availability(providers["asr"])
        self.draft.update_availability(providers["translation"])
        self.refine.update_availability(providers["translation"])
        self.tts.update_availability(providers["tts"])
        self._update_defaults()

    def refresh_recent(self) -> None:
        self.recent.refresh()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.log.clear()
            self._clear_alert()
            self._t0 = time.monotonic()
            self._stage = ""
            self._stage_t0.clear()
            for row in self.stage_rows.values():
                row.set_state("wait", "Chờ đến lượt")
            self.bar.setValue(0)
            self.p_badge.setText("0%")
            self.p_badge.setObjectName("badgePurple")
            self._repolish(self.p_badge)
            src = self._file or self.url.text().strip()
            self.p_title.setText(Path(src).name if self._file else "Video mới")
            self.p_meta.setText(_host(src))
            self.status.setText("Đang làm việc · bạn có thể mở Cài đặt")
            self.progress.setVisible(True)
            self.cancel.setEnabled(True)
            self.cancel.setVisible(True)
            self._tick.start()
            self._set_running_copy(True)
        self.start.setEnabled(not busy)
        self._hero_frame.setVisible(not busy)
        self._toggles.setVisible(not busy)
        self.note_box.setVisible(not busy and self.note_btn.isChecked())
        self.options.setVisible(not busy and self.opt_btn.isChecked())

    def _set_running_copy(self, running: bool) -> None:
        self.headline.setText("Để tiếng Việt\nkể lại câu chuyện." if running else
                              "Một đường link.\nMột video của bạn.")
        self.tagline.setText("Ứng dụng chuẩn bị bản dịch. Bạn là người quyết định câu chữ cuối "
                             "cùng." if running else
                             "Từ video tiếng Trung đến phụ đề và giọng đọc tiếng Việt.\n"
                             "Dán link, xem bản dịch, chỉnh cho vừa ý.")

    def set_title(self, title: str, meta: str = "") -> None:
        if title:
            self.p_title.setText(short_title(title))
            self.p_title.setToolTip(title)
        if meta:
            self.p_meta.setText(meta)

    def set_stage(self, key: str) -> None:
        """worker.stage_started: download → subtitle → translate (context is
        detected from progress text, it has no stage of its own)."""
        order = [k for k, _n in STAGES]
        if key not in order:
            return
        now = time.monotonic()
        for k in order[:order.index(key)]:
            row = self.stage_rows[k]
            if row.state == "active":
                row.set_state("done", "Hoàn tất", self._dur(k, now))
            elif row.state == "wait":
                row.set_state("done", "Bỏ qua" if k == "context" else "Hoàn tất")
        self._stage = key
        self._stage_t0.setdefault(key, now)
        self.stage_rows[key].set_state("active", "Đang xử lý…", "")

    def _dur(self, key: str, now: float) -> str:
        t0 = self._stage_t0.get(key)
        return f"{int(now - t0):02d} s" if t0 else ""

    def set_progress(self, frac: float, msg: str) -> None:
        pct = int(frac * 100)
        self.bar.setValue(pct)
        self.p_badge.setText(f"{pct}%")
        low = msg.lower()
        if self._stage == "subtitle" and ("ngữ cảnh" in low or "thuật ngữ" in low):
            self.set_stage("context")
        if self._stage in self.stage_rows:
            self.stage_rows[self._stage].set_state("active", msg, "Đang xử lý")

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def set_cancelling(self) -> None:
        self.status.setText("Đang hủy… (đợi bước hiện tại kết thúc)")
        self.cancel.setEnabled(False)
        self.append_log("⏳ Đang hủy…")

    def set_done(self) -> None:
        self._finish()
        now = time.monotonic()
        for k, row in self.stage_rows.items():
            if row.state in ("active", "wait"):
                row.set_state("done", "Hoàn tất" if row.state == "active" or k != "context"
                              else "Bỏ qua", self._dur(k, now))
        self.bar.setValue(100)
        self.p_badge.setText("Xong")
        self.p_badge.setObjectName("badgeOk")
        self._repolish(self.p_badge)
        self.status.setText("Phụ đề tiếng Việt đã sẵn sàng")
        self.cancel.setVisible(False)
        self.refresh_recent()

    def show_error(self, msg: str) -> None:
        cancelled = msg.strip().startswith("Đã hủy")
        self._finish()
        if self._stage in self.stage_rows:
            self.stage_rows[self._stage].set_state("paused" if cancelled else "error",
                                                   "Đã dừng" if cancelled else "Lỗi ở bước này")
        self.p_badge.setText("Đã dừng" if cancelled else "Lỗi")
        self.p_badge.setObjectName("badge" if cancelled else "badgeErr")
        self._repolish(self.p_badge)
        self.status.setText("Các bước đã xong được giữ lại" if cancelled else f"Lỗi: {msg}")
        self.append_log(f"✕ {msg}")
        self.cancel.setVisible(False)
        if not cancelled:
            self._show_alert(msg)
            if not self.alert.isHidden():
                self.progress.setVisible(False)     # the card says what to do; log is in it
        self._scroll.ensureWidgetVisible(self.progress if self.alert.isHidden() else self.alert)

    def idle(self) -> None:
        """Worker finished without gate/failed (defensive) — never leave Start dead."""
        if self._busy:
            self._finish()

    def _finish(self) -> None:
        self._tick.stop()
        self._busy = False
        self.set_busy(False)
        self._set_running_copy(False)
        self._refresh_start()

    def _on_tick(self) -> None:
        self.elapsed.setText(fmt_time(time.monotonic() - self._t0, 0))

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # --- error cards ----------------------------------------------------------------
    def _clear_alert(self) -> None:
        while self._alert_lay.count():
            item = self._alert_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear_layout(item.layout())
        self.alert.setVisible(False)

    def _show_alert(self, msg: str) -> None:
        self._clear_alert()
        low = msg.lower()
        if "cookie" in low:
            self._cookie_card()
        elif "api key" in low or "api_key" in low or "401" in low or "khóa api" in low:
            self._simple_card("KeyRound", "Thêm khóa API để dịch video",
                              "OpenRouter dùng chung cho nhận dạng giọng nói và dịch.",
                              "Thêm khóa", self.settingsRequested.emit)
        elif any(k in low for k in ("connection", "timed out", "timeout", "network",
                                    "name resolution", "kết nối")):
            self._simple_card("Wifi", "Chưa kết nối được máy chủ",
                              "Kiểm tra mạng rồi thử lại.", "Thử lại", self._retry,
                              icon_name="RefreshCw")
        else:
            return
        self.alert.setVisible(True)

    def _card_frame(self, warn: bool) -> None:
        self.alert.setObjectName("warnCard" if warn else "panel")
        self._repolish(self.alert)
        self._alert_lay.setContentsMargins(18, 16, 18, 16)
        self._alert_lay.setSpacing(10)

    def _simple_card(self, ic: str, title: str, text: str, action: str, slot,
                     icon_name: str | None = None) -> None:
        self._card_frame(False)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(icon_label(ic, V2["amber"], 18), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.addWidget(label(title, "h3"))
        col.addWidget(label(text, "small", wrap=True))
        row.addLayout(col, 1)
        b = button(action, icon_name, small=True)
        b.clicked.connect(slot)
        row.addWidget(b, 0, Qt.AlignVCenter)
        self._alert_lay.addLayout(row)

    def _cookie_card(self) -> None:
        self._card_frame(True)
        head = QHBoxLayout()
        head.addWidget(icon_label("TriangleAlert", V2["amber"], 18))
        t = label("Douyin cần cookie để tải video", "h2")
        t.setStyleSheet(f"color:{V2['amber']};background:transparent;")
        head.addWidget(t, 1)
        self._alert_lay.addLayout(head)
        self._alert_lay.addWidget(label("Chỉ cần làm một lần. Sau đó chọn tệp ngay ở đây rồi "
                                        "tiếp tục.", "secondary", wrap=True))
        steps = QHBoxLayout()
        steps.setSpacing(18)
        for n, title, text in (
            (1, "Cài tiện ích", "Get cookies.txt LOCALLY trong trình duyệt bạn dùng Douyin."),
            (2, "Mở Douyin", "Vào douyin.com, đăng nhập và mở video muốn dịch."),
            (3, "Xuất cookie", "Chọn Export để lưu tệp cookies.txt trên máy."),
        ):
            col = QVBoxLayout()
            col.setSpacing(4)
            num = QLabel(str(n))
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet(f"border:1px solid {V2['amber']};border-radius:11px;"
                              f"color:{V2['amber']};font-size:11px;background:transparent;")
            col.addWidget(num)
            col.addWidget(label(title, "h3"))
            col.addWidget(label(text, "small", wrap=True))
            col.addStretch(1)
            steps.addLayout(col, 1)
        self._alert_lay.addLayout(steps)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        pick = button("Chọn tệp cookies.txt", "FolderOpen", variant="primary")
        self.retry_btn = button("Thử lại", "RefreshCw")
        self.retry_btn.setEnabled(False)
        self.cookie_badge = badge("", "ok")
        self.cookie_badge.setVisible(False)
        pick.clicked.connect(self._pick_cookie)
        self.retry_btn.clicked.connect(self._retry)
        acts.addWidget(pick)
        acts.addWidget(self.retry_btn)
        acts.addWidget(self.cookie_badge)
        acts.addStretch(1)
        self._alert_lay.addLayout(acts)
        self._alert_lay.addWidget(label("Tệp dùng để tải video và chỉ lưu trên máy của bạn.",
                                        "small"))

    def _pick_cookie(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn tệp cookies.txt", "",
                                            "Cookie (*.txt);;All (*.*)")
        if not fn:
            return
        from app.config import settings
        from desktop.settings_dialog import _write_env

        _write_env({"YTDLP_COOKIES_FILE": fn})
        settings.ytdlp_cookies_file = fn
        self.cookie_badge.setText(Path(fn).name)
        self.cookie_badge.setVisible(True)
        self.retry_btn.setEnabled(True)

    def _retry(self) -> None:
        if self._last_opts and not self._busy:
            self.startRequested.emit(dict(self._last_opts))

    # --- F-5: cost and time before Start -----------------------------------------
    def _estimate_source(self) -> str | None:
        """The one video the next Start would process (a file, or exactly one link)."""
        if self._file:
            return self._file
        urls = _ALL_URLS.findall(self.url.text())
        return urls[0] if len(urls) == 1 else None

    def _update_estimate(self) -> None:
        key = self._estimate_source()
        if not key or self._busy:
            self.estimate.setVisible(False)
            return
        if key not in self._durations:
            self.estimate.setText("Đang xem thời lượng video để ước tính chi phí…")
            self.estimate.setVisible(True)
            if key not in self._probing:
                self._probing.add(key)
                is_file = bool(self._file)

                def work(k=key, f=is_file):
                    if f:
                        from app.pipeline.assemble import probe_duration

                        return probe_duration(Path(k)) or None
                    from app.pipeline import download

                    return float(download.probe(k).get("duration") or 0) or None

                def done(sec, k=key):
                    self._probing.discard(k)
                    self._durations[k] = sec
                    self._update_estimate()

                def fail(msg, k=key):
                    self._probing.discard(k)
                    self._durations[k] = None
                    self._probe_errors[k] = msg
                    self._update_estimate()

                self._bg.run(work, done, fail)
            return
        sec = self._durations[key]
        if not sec:
            err = self._probe_errors.get(key, "")
            if "cookie" in err.lower():      # the download would fail the same way: say so now
                self.estimate.setText("⚠ Douyin cần cookie mới — cập nhật trong Cài đặt → Tải video "
                                      "rồi hãy bắt đầu (lần tải này sẽ lỗi).")
                self.estimate.setToolTip(err.split(": ", 1)[-1])
            else:
                self.estimate.setText("Chưa ước tính được chi phí (không đọc được thời lượng video) — "
                                      "chi phí thật hiện sau khi chạy.")
                self.estimate.setToolTip(err)
            self.estimate.setVisible(True)
            return
        from app import estimate as est

        e = est.estimate(sec, self._collect_opts(), history=est.history(store._storage()))
        self.estimate.setText(e.label())
        tip = [f"Ước tính {e.basis}: ~{e.lines[0]}–{e.lines[1]} câu phụ đề (bài hát ít câu, "
               f"nói chuyện nhiều câu). Chi phí thật của từng video hiện ở “Tiếp tục dự án”.",
               *e.notes]
        self.estimate.setToolTip("\n".join(tip))
        self.estimate.setVisible(True)

    def shutdown(self) -> None:
        self._est_timer.stop()
        self._bg.shutdown()
        self.recent.shutdown()


def _host(src: str) -> str:
    if not src:
        return ""
    if "douyin" in src:
        return "Douyin"
    if "bilibili" in src or "b23.tv" in src:
        return "Bilibili"
    return "Tệp trên máy" if Path(src).exists() else src[:60]


def _clear_layout(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())
