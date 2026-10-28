"""Inspector (v2): the right panel — tabs "Câu đang chọn | Kiểu chữ | Ngữ cảnh".

The cue tab edits ONE cue: Vietnamese text (Enter = commit + next, Shift+Enter =
newline), the Chinese reference, fit warnings, start/end fields with ±50/200 ms
nudges and "set to playhead", the timing lock, and a collapsible per-line dub
section (gain / mute / listen). It only emits intents; EditorView mutates the cues
(so every change goes through its undo stack).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedLayout,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from app.pipeline import review
from desktop.cuemodel import EditorCue, fmt_time, parse_time
from desktop.icons import icon
from desktop.theme import V2, cjk_font
from desktop.widgets.chrome import badge, button, hbox, icon_label, label
from desktop.widgets.no_wheel import NoWheelComboBox

NUDGES = (-200, -50, 50, 200)


class ViEdit(QPlainTextEdit):
    """Enter commits and moves on; Shift+Enter inserts a line break; Esc hands the
    keyboard back to the list (so ↑/↓ and the plain-letter keys work again)."""

    commitNext = Signal()
    focused = Signal()
    escaped = Signal()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and not (ev.modifiers() & Qt.ShiftModifier):
            self.commitNext.emit()
            return
        if ev.key() == Qt.Key_Escape and not ev.modifiers():
            self.escaped.emit()
            return
        super().keyPressEvent(ev)

    def focusInEvent(self, ev) -> None:  # noqa: N802
        super().focusInEvent(ev)
        self.focused.emit()


class TimeField(QLineEdit):
    focused = Signal()

    def focusInEvent(self, ev) -> None:  # noqa: N802
        super().focusInEvent(ev)
        self.focused.emit()


class CueInspector(QFrame):
    viEdited = Signal(str)             # every keystroke
    editStarted = Signal()             # first keystroke of an editing session → one undo step
    commitNext = Signal()
    editFocused = Signal()             # pause the video while typing / timing
    nudgeRequested = Signal(str, int)  # ("start"|"end", ms)
    setEdgeRequested = Signal(str)     # "start"|"end" → playhead
    timeTyped = Signal(str, float)
    lockToggled = Signal(bool)
    lineBreakRequested = Signal()
    autoSnapRequested = Signal()       # "Tự canh" — snap this cue's edges to the voice
    applyFixRequested = Signal(str)    # "Dùng gợi ý" from the AI review (E-5)
    retranslateRequested = Signal()    # "Dịch lại" (E-6, Alt+R)
    editStyleRequested = Signal()      # "Phong cách của tôi" (E-7)
    shortenRequested = Signal()
    listenRequested = Signal()
    glossaryRequested = Signal()
    leaveRequested = Signal()          # Esc in the text box → focus back to the list

    def __init__(self, style_panel: QWidget) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._idx = -1
        self._session = False           # an editing session already snapshotted
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.tabbar = QTabBar()
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        # short names so five tabs fit the column (the full name is the tooltip)
        for t, tip in (("Câu", "Câu đang chọn: chữ, thời gian, gợi ý"),
                       ("Kiểu chữ", "Phông, màu, độ mờ, nền và vị trí phụ đề"),
                       ("Ngữ cảnh", "Tóm tắt video và ghi chú cho bộ dịch")):
            self.tabbar.setTabToolTip(self.tabbar.addTab(t), tip)
        tb = QFrame()
        tb.setObjectName("panelHead")
        th = QHBoxLayout(tb)
        th.setContentsMargins(10, 4, 10, 0)
        th.addWidget(self.tabbar)
        th.addStretch(1)
        v.addWidget(tb)

        pages = QWidget()
        self._pages = QStackedLayout(pages)
        self._pages.addWidget(self._scroll(self._cue_page()))
        self._pages.addWidget(style_panel)              # already a QScrollArea
        self._pages.addWidget(self._scroll(self._context_page()))
        self.tabbar.currentChanged.connect(self._pages.setCurrentIndex)
        v.addWidget(pages, 1)
        self.set_cue(-1, None, [])

    @staticmethod
    def _scroll(w: QWidget) -> QScrollArea:
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setFrameShape(QFrame.NoFrame)
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s.setWidget(w)
        return s

    def content_min_width(self) -> int:
        """Narrowest width showing every tab whole: the pages sit in scroll areas that
        never scroll sideways, so anything narrower is cut off at the right edge."""
        need = self.tabbar.minimumSizeHint().width() + 20
        for i in range(self._pages.count()):
            page = self._pages.widget(i)
            inner = page.widget() if isinstance(page, QScrollArea) else page
            if inner is not None:
                need = max(need, inner.minimumSizeHint().width() + 14)   # + scroll bar
        return need + 2                                                   # panel border

    def set_tab(self, i: int) -> None:
        self.tabbar.setCurrentIndex(i)

    def add_page(self, title: str, widget: QWidget, tip: str = "") -> int:
        """One more tab (F-4 "Bình luận"); returns its index."""
        self._pages.addWidget(self._scroll(widget))
        i = self.tabbar.addTab(title)
        if tip:
            self.tabbar.setTabToolTip(i, tip)
        return i

    # --- cue tab --------------------------------------------------------------
    def _cue_page(self) -> QWidget:
        page = QWidget()
        self._cue_stack = QStackedLayout(page)
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        ev.addWidget(icon_label("Captions", V2["muted"], 22), 0, Qt.AlignHCenter)
        e = label("Chọn một câu để sửa", "secondary")
        e.setAlignment(Qt.AlignCenter)
        ev.addWidget(e)
        ev.addStretch(2)
        self._cue_stack.addWidget(empty)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        self.sel_lbl = label("Câu —", "secondary")
        self.dur_lbl = label("", "mono")
        self.btn_shorten = button("Rút gọn", "Scissors", variant="ghost", small=True,
                                  tip="Nhờ AI rút gọn câu cho vừa thời lượng lồng tiếng")
        self.btn_shorten.clicked.connect(self.shortenRequested)
        head.addWidget(self.sel_lbl)
        head.addStretch(1)
        head.addWidget(self.dur_lbl)
        self.btn_retranslate = button("Dịch lại", "Sparkles", variant="ghost", small=True,
                                      tip="3 phương án dịch cho câu này (Alt+R)")
        self.btn_retranslate.clicked.connect(self.retranslateRequested)
        head.addWidget(self.btn_retranslate)
        head.addWidget(self.btn_shorten)
        lay.addLayout(head)

        self.vi_edit = ViEdit()
        self.vi_edit.setFixedHeight(62)
        self.vi_edit.setToolTip("Enter: lưu và sang câu sau · Shift+Enter: xuống dòng · "
                                "Ctrl+↑/↓: câu trước/sau · Esc: về danh sách")
        self.vi_edit.textChanged.connect(self._on_text)
        self.vi_edit.commitNext.connect(self._commit)
        self.vi_edit.escaped.connect(self.leaveRequested)
        self.vi_edit.focused.connect(self.editFocused)
        lay.addWidget(self.vi_edit)

        self.zh_lbl = QLabel("")
        self.zh_lbl.setWordWrap(True)
        self.zh_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.zh_lbl.setStyleSheet(f"color:{V2['muted']};font-family:'{cjk_font()}';font-size:12px;")
        lay.addWidget(self.zh_lbl)

        self.fit = QFrame()
        fh = QHBoxLayout(self.fit)
        fh.setContentsMargins(10, 7, 10, 7)
        fh.setSpacing(7)
        self.fit_icon = QLabel()
        self.fit_lbl = QLabel("")
        self.fit_lbl.setStyleSheet("font-weight:600;font-size:12px;background:transparent;")
        self.fit_lbl.setWordWrap(True)
        fh.addWidget(self.fit_icon)
        fh.addWidget(self.fit_lbl, 1)
        lay.addWidget(self.fit)

        # E-5: the AI reviewer's note + proposed line for THIS cue (hidden otherwise)
        self.ai_box = QFrame()
        self.ai_box.setObjectName("inset")
        ab = QVBoxLayout(self.ai_box)
        ab.setContentsMargins(10, 8, 10, 8)
        ab.setSpacing(4)
        self.ai_note = QLabel("")
        self.ai_note.setWordWrap(True)
        self.ai_note.setStyleSheet(f"color:{V2['secondary']};font-size:12px;background:transparent;")
        self.ai_fix = QLabel("")
        self.ai_fix.setWordWrap(True)
        self.ai_fix.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ai_fix.setStyleSheet(f"color:{V2['purple_bright']};font-size:13px;background:transparent;")
        self.ai_apply = button("Dùng gợi ý", "Check", variant="ghost", small=True,
                               tip="Thay câu bằng gợi ý của AI (Ctrl+Z để hoàn tác)")
        self.ai_apply.clicked.connect(lambda: self.applyFixRequested.emit(self.ai_fix.text()))
        ab.addWidget(self.ai_note)
        ab.addWidget(self.ai_fix)
        ab.addWidget(self.ai_apply, 0, Qt.AlignLeft)
        self.ai_box.setVisible(False)
        lay.addWidget(self.ai_box)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.t_fields: dict[str, TimeField] = {}
        self.nudge_btns: list[QPushButton] = []
        self.edge_btns: dict[str, QPushButton] = {}
        for col, (which, title, key) in enumerate((("start", "Bắt đầu", "["),
                                                    ("end", "Kết thúc", "]"))):
            grid.addWidget(label(title, "small"), 0, col)
            f = TimeField()
            f.setObjectName("mono")
            f.setToolTip("Gõ mm:ss.mmm rồi Enter")
            f.returnPressed.connect(lambda w=which: self._typed(w))
            f.focused.connect(self.editFocused)
            self.t_fields[which] = f
            grid.addWidget(f, 1, col)
            row = QHBoxLayout()
            row.setSpacing(4)
            for ms in NUDGES:
                b = QPushButton(f"{ms:+d}" if ms > 0 else str(ms))
                b.setObjectName("nudge")
                b.setToolTip(f"{'Đầu' if which == 'start' else 'Cuối'} {ms:+d} ms")
                b.clicked.connect(lambda _=False, w=which, d=ms: self.nudgeRequested.emit(w, d))
                self.nudge_btns.append(b)
                row.addWidget(b)
            grid.addLayout(row, 2, col)
            eb = button(f"Đặt {'đầu' if which == 'start' else 'cuối'}   {key}", variant="link",
                        tip=f"Đặt {'đầu' if which == 'start' else 'cuối'} câu = vị trí phát ({key})")
            eb.clicked.connect(lambda _=False, w=which: self.setEdgeRequested.emit(w))
            self.edge_btns[which] = eb
            grid.addWidget(eb, 3, col)
        lay.addLayout(grid)

        cap = QHBoxLayout()
        self.snap_btn = button("Tự canh   A", "Sparkles", variant="ghost", small=True)
        self.snap_btn.clicked.connect(self.autoSnapRequested)
        cap.addWidget(self.snap_btn)
        cap.addStretch(1)
        self.play_lbl = label("Đang phát 00:00.00", "small")
        cap.addWidget(self.play_lbl)
        lay.addLayout(cap)

        lock_row = QHBoxLayout()
        self.lock_cb = QCheckBox("Khóa timing")
        self.lock_cb.setToolTip("Khóa đầu/cuối câu — không kéo, canh hay đặt nhầm được")
        self.lock_cb.toggled.connect(self._on_lock)
        lock_row.addWidget(self.lock_cb)
        lock_row.addStretch(1)
        br = button("Xuống dòng", "AlignCenter", variant="ghost", small=True,
                    tip="Chèn ngắt dòng ở vị trí con trỏ · Shift+Enter")
        br.clicked.connect(self.lineBreakRequested)
        lock_row.addWidget(br)
        lay.addLayout(lock_row)
        # line count + F-6 reading speed on their own line: beside the lock they pushed
        # the inspector wider than a 1366 px window gives it (cut off at the right)
        self.lines_lbl = label("", "small")
        self.read_lbl = label("", "small")
        lay.addLayout(hbox(self.lines_lbl, None, self.read_lbl))
        lay.addWidget(_hline())

        # collapsible per-line dub
        self.voice_toggle = button("Lồng tiếng câu này", "Volume2", variant="ghost", checkable=True,
                                   small=True)
        lay.addWidget(self.voice_toggle, 0, Qt.AlignLeft)
        self.voice_box = QWidget()
        vb = QVBoxLayout(self.voice_box)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(6)
        gr = QHBoxLayout()
        self.gain = QSlider(Qt.Horizontal)
        self.gain.setRange(-18, 12)
        self.gain.setToolTip("Âm lượng giọng đọc của câu này")
        self.gain_lbl = label("0 dB", "mono")
        self.mute_cb = QCheckBox("Tắt")
        self.mute_cb.setToolTip("Không lồng tiếng câu này (vẫn giữ phụ đề)")
        gr.addWidget(self.gain, 1)
        gr.addWidget(self.gain_lbl)
        gr.addWidget(self.mute_cb)
        vb.addLayout(gr)
        vr = QHBoxLayout()
        vr.addWidget(label("Giọng", "small"))
        self.voice_combo = NoWheelComboBox()
        self.voice_combo.addItem("Theo lựa chọn chung", "")
        self.voice_combo.addItem("Giọng nữ", "f")
        self.voice_combo.addItem("Giọng nam", "m")
        self.voice_combo.setToolTip(
            "Chỉ có tác dụng khi bật “Giọng theo nhân vật (nam/nữ)” ở trang dựng. "
            "Máy đoán theo cao độ giọng gốc — sửa ở đây nếu đoán sai.")
        vr.addWidget(self.voice_combo, 1)
        vb.addLayout(vr)
        self.btn_listen = button("Nghe thử", "Volume2")
        self.btn_listen.clicked.connect(self.listenRequested)
        vb.addWidget(self.btn_listen, 0, Qt.AlignLeft)
        self.voice_box.setVisible(False)
        self.voice_toggle.toggled.connect(self.voice_box.setVisible)
        lay.addWidget(self.voice_box)
        lay.addStretch(1)
        self._cue_stack.addWidget(body)
        return page

    # --- context tab ------------------------------------------------------------
    def _context_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("Ngữ cảnh của video", "secondary"))
        head.addStretch(1)
        self.ctx_badge = badge("AI đã đọc", "purple")
        head.addWidget(self.ctx_badge)
        lay.addLayout(head)
        self.ctx_text = QLabel("")
        self.ctx_text.setWordWrap(True)
        self.ctx_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ctx_text.setObjectName("inset")
        self.ctx_text.setStyleSheet(
            f"background:{V2['input']};border:1px solid {V2['line']};border-radius:7px;"
            f"padding:10px;color:{V2['secondary']};font-size:12px;")
        lay.addWidget(self.ctx_text)
        gl = button("Mở thuật ngữ", "BookOpen", small=True)
        gl.clicked.connect(self.glossaryRequested)
        lay.addWidget(gl, 0, Qt.AlignLeft)
        lay.addWidget(label("Thông tin bạn ghi cho video luôn được ưu tiên khi dịch.", "small",
                            wrap=True))
        lay.addWidget(_hline())
        sh = QHBoxLayout()
        sh.addWidget(label("Phong cách của tôi", "secondary"))
        sh.addStretch(1)
        sh.addWidget(badge("mọi video", "purple"))
        lay.addLayout(sh)
        self.style_text = QLabel("")
        self.style_text.setWordWrap(True)
        self.style_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.style_text.setStyleSheet(f"color:{V2['secondary']};font-size:12px;")
        lay.addWidget(self.style_text)
        st = button("Sửa phong cách", "Type", small=True,
                    tip="Quy tắc rút ra từ các lần bạn sửa bản dịch — sửa tay được")
        st.clicked.connect(self.editStyleRequested)
        lay.addWidget(st, 0, Qt.AlignLeft)
        lay.addStretch(1)
        return page

    def set_style_rules(self, rules: str) -> None:
        self.style_text.setText(rules or "Chưa có. Sau vài video bạn duyệt, AI rút ra thói quen "
                                         "sửa của bạn (xưng hô, từ ngữ…) và áp cho video sau.")

    def set_context(self, brief: str | None, note: str | None = None) -> None:
        text = (brief or "").strip()
        if note:
            text = f"Ghi chú của bạn: {note.strip()}\n\n{text}".strip()
        self.ctx_text.setText(text or "Chưa có ngữ cảnh cho video này. Ghi chú ngữ cảnh ở "
                                      "màn Bắt đầu (hoặc bật “tự đọc ngữ cảnh”) rồi dịch lại.")
        self.ctx_badge.setVisible(bool(brief))

    # --- state ------------------------------------------------------------------
    def set_cue(self, idx: int, c: EditorCue | None, issues: list[str], *,
                lines: int | None = None) -> None:
        """Show cue ``idx`` (or the empty state). Never emits edit signals."""
        changed = idx != self._idx
        self._idx = idx
        if changed:
            self._session = False
        if c is None:
            self._cue_stack.setCurrentIndex(0)
            return
        self._cue_stack.setCurrentIndex(1)
        self.sel_lbl.setText(f"Câu {idx + 1:02d}")
        self.dur_lbl.setText(f"{c.dur:.2f} s")
        if self.vi_edit.toPlainText() != c.vi:
            self.vi_edit.blockSignals(True)
            cur = self.vi_edit.textCursor().position()
            self.vi_edit.setPlainText(c.vi)
            tc = self.vi_edit.textCursor()
            tc.setPosition(len(c.vi) if changed else min(cur, len(c.vi)))   # new cue → caret at end
            self.vi_edit.setTextCursor(tc)
            self.vi_edit.blockSignals(False)
        self.zh_lbl.setText(c.zh)
        self.zh_lbl.setVisible(bool(c.zh))
        self.refresh_timing(c)
        self.set_issues(issues)
        self.lock_cb.blockSignals(True)
        self.lock_cb.setChecked(c.locked)
        self.lock_cb.blockSignals(False)
        self._apply_lock(c.locked)
        self.gain.blockSignals(True)
        self.mute_cb.blockSignals(True)
        self.gain.setValue(int(round(c.gain_db)))
        self.gain_lbl.setText(f"{int(round(c.gain_db)):+d} dB")
        self.mute_cb.setChecked(c.mute)
        self.gain.blockSignals(False)
        self.mute_cb.blockSignals(False)
        self.voice_combo.blockSignals(True)
        self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(c.voice)))
        self.voice_combo.blockSignals(False)
        self.lines_lbl.setText(f"đang {lines} dòng" if lines else "")
        self.set_reading(c.vi, c.end - c.start)

    def set_reading(self, text: str, seconds: float) -> None:
        """F-6: this line's reading speed against the Netflix Vietnamese 17 cps."""
        from app.pipeline import review

        if not (text or "").strip():
            self.read_lbl.setText("")
            return
        cps, n = review.reading(text, seconds)
        if cps <= review.READ_CPS:
            note, color = "dễ đọc", V2["green"]
        elif cps <= review.READ_FLAG_CPS:
            note, color = "hơi nhanh", V2["amber"]
        else:
            note, color = "đọc không kịp — Rút gọn?", V2["red"]
        self.read_lbl.setText(f"{cps:.0f} ký tự/giây · {note}")
        self.read_lbl.setStyleSheet(f"color:{color};font-size:11px;")
        self.read_lbl.setToolTip(
            f"Chuẩn Netflix cho phụ đề tiếng Việt: ≤ {review.READ_CPS:.0f} ký tự/giây, "
            f"≤ {review.CHARS_PER_LINE} ký tự/dòng, ≤ {review.MAX_LINES} dòng. Gắn cờ khi quá "
            f"{review.READ_FLAG_CPS:.0f} ký tự/giây hoặc cần hơn {review.MAX_LINES} dòng "
            f"(câu này: {n} dòng).")

    def refresh_timing(self, c: EditorCue) -> None:
        for which, val in (("start", c.start), ("end", c.end)):
            f = self.t_fields[which]
            if not f.hasFocus():
                f.setText(fmt_time(val, 3))
        self.dur_lbl.setText(f"{c.dur:.2f} s")
        self.set_reading(c.vi, c.end - c.start)

    def set_ai_finding(self, finding: dict | None) -> None:
        from app.pipeline import qa

        if not finding:
            self.ai_box.setVisible(False)
            return
        kind = qa.KINDS.get(finding.get("kind", ""), "AI nghi sai")
        self.ai_note.setText(f"AI soát · {kind}: {finding.get('note', '')}")
        fix = finding.get("fix", "")
        self.ai_fix.setText(fix)
        self.ai_fix.setVisible(bool(fix))
        self.ai_apply.setVisible(bool(fix))
        self.ai_box.setVisible(True)

    def set_issues(self, issues: list[str]) -> None:
        ok = not issues
        color = V2["green"] if ok else V2["amber"]
        bg = V2["green_soft"] if ok else V2["amber_soft"]
        self.fit.setStyleSheet(f"QFrame{{background:{bg};border-radius:7px;}}")
        self.fit_icon.setPixmap(icon("CircleCheck" if ok else "TriangleAlert", color, 15).pixmap(15, 15))
        self.fit_icon.setStyleSheet("background:transparent;")
        self.fit_lbl.setText("Vừa thời lượng" if ok else
                             " · ".join(review.LABELS.get(k, k) for k in issues[:2]))
        self.fit_lbl.setToolTip(
            "Lần dựng trước, giọng câu này vào trễ so với phụ đề vì câu trước đọc lâu quá. "
            "Rút gọn câu trước (hoặc câu này) rồi dựng lại." if "late" in issues else "")
        self.fit_lbl.setStyleSheet(f"color:{color};font-weight:600;font-size:12px;"
                                   f"background:transparent;")

    def set_lines(self, n: int | None) -> None:
        self.lines_lbl.setText(f"đang {n} dòng" if n else "")

    def set_playhead(self, t: float) -> None:
        self.play_lbl.setText(f"Đang phát {fmt_time(t, 2)}")

    def set_listen_enabled(self, on: bool, tip: str) -> None:
        self.btn_listen.setEnabled(on)
        self.btn_listen.setToolTip(tip)

    def set_busy(self, which: str, busy: bool) -> None:
        b = self.btn_shorten if which == "shorten" else self.btn_listen
        b.setEnabled(not busy)
        b.setText("Đang xử lý…" if busy else ("Rút gọn" if which == "shorten" else "Nghe thử"))

    def focus_text(self, select_all: bool = False) -> None:
        self.set_tab(0)
        self.vi_edit.setFocus()
        if select_all:
            self.vi_edit.selectAll()

    def end_session(self) -> None:
        self._session = False

    # --- internal -----------------------------------------------------------------
    def _on_text(self) -> None:
        if self._idx < 0:
            return
        if not self._session:
            self._session = True
            self.editStarted.emit()
        self.viEdited.emit(self.vi_edit.toPlainText())

    def _commit(self) -> None:
        self._session = False
        self.commitNext.emit()

    def _typed(self, which: str) -> None:
        t = parse_time(self.t_fields[which].text())
        if t is not None:
            self.timeTyped.emit(which, t)
        self.t_fields[which].clearFocus()

    def _on_lock(self, on: bool) -> None:
        self._apply_lock(on)
        self.lockToggled.emit(on)

    def set_snap_available(self, on: bool, why: str = "") -> None:
        """Voice spans loaded? (EditorView, after app.pipeline.vad)"""
        self._snap_ok, self._snap_why = on, why
        self._apply_lock(self.lock_cb.isChecked())

    def _apply_lock(self, on: bool) -> None:
        ok = getattr(self, "_snap_ok", False)
        self.snap_btn.setEnabled(ok and not on)
        self.snap_btn.setToolTip(
            "Câu đang khóa timing" if on and ok else
            "Dời đầu / cuối câu tới chỗ giọng nói bắt đầu / dừng (tối đa ~1 s) — phím A"
            if ok else (getattr(self, "_snap_why", "") or "Đang phân tích giọng nói…"))
        for b in self.nudge_btns:
            b.setEnabled(not on)
        for b in self.edge_btns.values():
            b.setEnabled(not on)
        for f in self.t_fields.values():
            f.setReadOnly(on)
        self.lock_cb.setIcon(icon("LockKeyhole" if on else "UnlockKeyhole",
                                  V2["purple_bright"] if on else V2["secondary"], 14))


def _hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{V2['line_soft']};")
    return f
