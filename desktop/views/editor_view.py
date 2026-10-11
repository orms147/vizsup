"""Screen 2 — Subtitle Editor (the human gate), v2 layout (appdesign/v2):

    ProjectBar  (title · meta · CN→VI · layout · Thuật ngữ)
    [ Video nguồn | N câu (list + review filter) | Câu đang chọn / Kiểu chữ / Ngữ cảnh ]
    [ Dòng thời gian (real waveform · blocks · zoom)                                   ]

Edits autosave to job.vi_srt (600 ms debounce; a failed write says so and retries).
Every mutation of ``self.cues`` goes through ``_snapshot()`` first (undo/redo).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.pipeline import review
from desktop import cuemodel, store
from desktop.cuemodel import EditorCue, fmt_time
from desktop.ui_thread import connect_on_ui
from desktop.widgets.chrome import ProjectBar, badge, button, icon_button, label
from desktop.widgets.cue_inspector import CueInspector
from desktop.widgets.cue_list import CueList
from desktop.widgets.style_panel import StylePanel
from desktop.widgets.timeline import TimelinePanel
from desktop.widgets.video_panel import VideoPanel

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    _HAVE_MM = True
except Exception:  # noqa: BLE001
    _HAVE_MM = False

SAVE_MS, RETRY_MS = 600, 4000
LAYOUTS = {"balanced": ("Cân bằng", (36, 38, 26)), "video": ("Ưu tiên video", (50, 27, 23))}


class _Task(QObject):
    """Run one blocking backend call off the UI thread."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class EditorView(QWidget):
    dirtyChanged = Signal(bool)        # True = pending, False = saved to disk
    saveFailed = Signal(str)           # autosave could not write — retried
    message = Signal(str, str)         # (text, kind ok|busy|warn|err) → status bar
    approveRequested = Signal()        # Ctrl+Enter
    shortcutsRequested = Signal()      # "?"
    previewStyleRequested = Signal()   # "Xem thử kiểu" — main burns a WYSIWYG frame
    glossaryRequested = Signal()       # open the glossary dialog (main owns the job)

    def __init__(self) -> None:
        super().__init__()
        self.job = None
        self.cues: list[EditorCue] = []
        self.issues: list[list[str]] = []
        self._spans: tuple[list, list] = ([], [])   # (ocr, glossary) flag spans
        self._speech: list | None = None   # voice spans (app.pipeline.vad) for "Tự canh"
        self._ctx: dict = {}          # tts/voice/translator context from step 1
        self._sel: int = -1           # selected cue index
        self._following = False       # selection driven by playback, not the user
        self._tasks: list = []        # keep async (thread, worker) refs alive
        self._burning = False         # guards one style re-burn at a time
        self._layout = "balanced"
        self._meta = {"title": "", "platform": "", "dur": 0.0, "dims": (0, 0)}
        self._g_font, self._g_size = "Be Vietnam Pro", 20   # cached GLOBAL style (render/preview)
        self._g_style: dict | None = None                   # — so selected-mode edits can't bleed
        self._dub_player = None
        if _HAVE_MM:
            self._dub_player = QMediaPlayer()
            self._dub_audio = QAudioOutput()
            self._dub_player.setAudioOutput(self._dub_audio)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_MS)
        self._save_timer.timeout.connect(self._save)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._project_bar())

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(18, 4, 18, 12)
        bl.setSpacing(0)

        self.video = VideoPanel("Video nguồn")
        self.cue_list = CueList()
        self.style_panel = StylePanel()
        self.inspector = CueInspector(self.style_panel)
        self.gain, self.mute_cb = self.inspector.gain, self.inspector.mute_cb
        self.timeline_panel = TimelinePanel()
        self.timeline = self.timeline_panel.timeline

        self.top = QSplitter(Qt.Horizontal)
        self.top.setHandleWidth(8)
        self.top.setChildrenCollapsible(False)
        self.video.setMinimumWidth(260)
        self.cue_list.setMinimumWidth(320)
        self.inspector.setMinimumWidth(280)
        for w in (self.video, self.cue_list, self.inspector):
            self.top.addWidget(w)
        self.split = QSplitter(Qt.Vertical)
        self.split.setHandleWidth(8)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.top)
        self.split.addWidget(self.timeline_panel)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.timeline_panel.setMinimumHeight(150)
        bl.addWidget(self.split, 1)
        root.addWidget(body, 1)

        self.cue_list.layout().insertWidget(1, self._find_bar())
        self.find_bar.hide()                 # opens on Ctrl+F / the search button
        self._apply_layout(self._layout, initial=True)
        self._wire()
        self._shortcuts()

    # --- construction ---------------------------------------------------------
    def _project_bar(self) -> ProjectBar:
        self.project = ProjectBar()
        self.project.add_action(badge("Tiếng Trung → Tiếng Việt", "purple"))
        self.layout_btn = button("Cân bằng", "PanelRightClose", variant="ghost",
                                 tip="Đổi bố cục: cân bằng / ưu tiên video")
        self.layout_btn.clicked.connect(self._toggle_layout)
        self.project.add_action(self.layout_btn)
        gl = button("Thuật ngữ", "BookOpen", tip="Sửa bảng thuật ngữ Trung → Việt (tự nạp khi dịch)")
        gl.clicked.connect(self.glossaryRequested)
        self.project.add_action(gl)
        return self.project

    def _wire(self) -> None:
        cl, ins, tl, vid = self.cue_list, self.inspector, self.timeline, self.video
        cl.rowSelected.connect(self._on_row_selected)
        cl.editRequested.connect(lambda: ins.focus_text(select_all=True))
        cl.splitRequested.connect(self._split)
        cl.mergeRequested.connect(self._merge)
        cl.addRequested.connect(self._add)
        cl.deleteRequested.connect(self._delete)
        cl.snapAllRequested.connect(self._auto_snap_all)
        cl.findRequested.connect(lambda: self._toggle_find())
        cl.reviewedRequested.connect(self._mark_reviewed)
        cl.nextPendingRequested.connect(lambda: self._goto_pending(+1))
        cl.view.selectionModel().selectionChanged.connect(lambda *_: self._update_scope_hint())

        ins.editStarted.connect(self._snapshot)        # one undo step per editing session
        ins.viEdited.connect(self._on_vi_edited)
        ins.commitNext.connect(self._commit_next)
        ins.editFocused.connect(vid.pause)
        ins.nudgeRequested.connect(self._nudge)
        ins.setEdgeRequested.connect(self._set_edge)
        ins.timeTyped.connect(self._type_time)
        ins.lockToggled.connect(self._on_lock)
        ins.lineBreakRequested.connect(self._line_break)
        ins.autoSnapRequested.connect(self._auto_snap_selected)
        ins.shortenRequested.connect(self._shorten)
        ins.listenRequested.connect(self._preview_dub)
        ins.glossaryRequested.connect(self.glossaryRequested)
        self.gain.sliderPressed.connect(self._snapshot)   # once per slider gesture
        self.gain.valueChanged.connect(self._on_gain)
        self.mute_cb.toggled.connect(self._on_mute)

        tl.seeked.connect(vid.seek_seconds)
        tl.blockSelected.connect(lambda i: cl.selectRow(i, center=False))
        tl.dragStarted.connect(self._snapshot)            # once per drag, not per pixel
        tl.cueChanged.connect(self._on_cue_dragged)
        tl.dragFinished.connect(lambda _i: self._refresh_issues())
        self.timeline_panel.fitRequested.connect(self._fit_selection)
        self.timeline_panel.snapAllRequested.connect(self._auto_snap_all)

        vid.positionChanged.connect(self._on_position)
        vid.durationChanged.connect(self._on_duration)
        vid.dimsChanged.connect(self._on_dims)
        vid.expandRequested.connect(self._toggle_layout)
        vid.marginsChanged.connect(self.style_panel.set_drag_margins)  # drag box → margins
        vid.dragCommitted.connect(self._reburn_preview)                # release → exact frame

        self.style_panel.previewRequested.connect(self.previewStyleRequested)
        self.style_panel.styleChanged.connect(self._on_style_changed)
        self.style_panel.scopeChanged.connect(self._update_scope_hint)
        self.style_panel.resetSelectedRequested.connect(self._reset_selected_style)

        # undo/redo: anything that mutates self.cues WITHOUT a _snapshot() first is
        # a hole in undo; add the call rather than special-casing it here.
        self._undo_stack: list[list[EditorCue]] = []
        self._redo_stack: list[list[EditorCue]] = []

    def _shortcuts(self) -> None:
        """Editor keys (design: appdesign/v2 · desktop/widgets/shortcuts.py). Text
        inputs claim plain keys via ShortcutOverride, so Space / S / M / [ ] still
        type inside the VI box and the find fields."""
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(lambda: fn() if self.isVisible() and self.isEnabled() else None)
            return s

        QShortcut(QKeySequence.Undo, self, activated=self._undo_action)
        QShortcut(QKeySequence.Redo, self, activated=self._redo_action)          # Ctrl+Y
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo_action)
        QShortcut(QKeySequence.Find, self, activated=lambda: self._toggle_find(True))
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar,
                        activated=lambda: self._toggle_find(False))
        esc.setContext(Qt.WidgetWithChildrenShortcut)   # don't swallow Esc app-wide
        sc("Space", self.video.toggle)
        sc("Ctrl+Space", lambda: self.video.set_loop(not self.video.loop_enabled()))
        sc("[", lambda: self._set_edge("start"))
        sc("]", lambda: self._set_edge("end"))
        sc("S", self._split)
        sc("M", self._merge)
        sc("A", self._auto_snap_selected)
        sc("Shift+A", self._auto_snap_all)
        sc("F8", lambda: self._goto_pending(+1))
        sc("Shift+F8", lambda: self._goto_pending(-1))
        sc("Ctrl+Return", self.approveRequested.emit)
        sc("Ctrl+Enter", self.approveRequested.emit)
        sc("?", self.shortcutsRequested.emit)
        sc("Shift+?", self.shortcutsRequested.emit)
        for mods, which in (("Alt", "start"), ("Ctrl+Alt", "end")):
            for big in (False, True):
                pre = mods + ("+Shift" if big else "")
                step = 200 if big else 50
                sc(f"{pre}+Left", lambda w=which, d=-step: self._nudge(w, d))
                sc(f"{pre}+Right", lambda w=which, d=step: self._nudge(w, d))

    # --- layout ---------------------------------------------------------------
    def _toggle_layout(self) -> None:
        self._apply_layout("video" if self._layout == "balanced" else "balanced")

    def _apply_layout(self, name: str, *, initial: bool = False) -> None:
        self._layout = name
        text, ratio = LAYOUTS[name]
        self.layout_btn.setText(text)
        total = max(900, self.top.width() or 1330)
        self.top.setSizes([int(total * r / 100) for r in ratio])
        if initial:
            self.split.setSizes([560, 170])

    # --- find / replace (UX-4) -----------------------------------------------
    def _find_bar(self) -> QWidget:
        """Find/replace over the VI column. Search semantics are pure functions in
        cuemodel (headless-tested); this is the thin wrapper."""
        bar = QFrame()
        bar.setObjectName("inset")
        bar.setStyleSheet("QFrame#inset{border-left:none;border-right:none;border-radius:0;}")
        v = QVBoxLayout(bar)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        self.find_in = QLineEdit()
        self.find_in.setPlaceholderText("Tìm trong tiếng Việt…")
        self.find_in.returnPressed.connect(self._find_next)
        self.find_in.textChanged.connect(self._update_find_count)
        self.find_lbl = label("", "small")
        close = icon_button("X", "Đóng (Esc)")
        close.clicked.connect(lambda: self._toggle_find(False))
        r1.addWidget(self.find_in, 1)
        r1.addWidget(self.find_lbl)
        r1.addWidget(close)
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self.repl_in = QLineEdit()
        self.repl_in.setPlaceholderText("Thay bằng…")
        self.repl_in.returnPressed.connect(self._replace_one)
        r2.addWidget(self.repl_in, 1)
        for text, slot, tip in (
            ("Tiếp", self._find_next, "Tới câu khớp tiếp theo (Enter)"),
            ("Thay", self._replace_one, "Thay ở câu đang chọn, rồi tới câu khớp sau"),
            ("Thay tất cả", self._replace_all,
             "Thay trong TOÀN BỘ phụ đề — hoàn tác được bằng Ctrl+Z"),
        ):
            b = button(text, small=True, tip=tip)
            b.clicked.connect(slot)
            r2.addWidget(b)
        self.find_case = QCheckBox("Aa")
        self.find_case.setToolTip("Phân biệt hoa / thường")
        self.find_case.toggled.connect(self._update_find_count)
        r2.addWidget(self.find_case)
        v.addLayout(r1)
        v.addLayout(r2)
        self.find_bar = bar
        return bar

    def _toggle_find(self, on: bool | None = None) -> None:
        show = self.find_bar.isHidden() if on is None else on
        self.find_bar.setVisible(show)
        if show:
            self.find_in.setFocus()
            self.find_in.selectAll()
            self._update_find_count()

    def _case(self) -> bool:
        return self.find_case.isChecked()

    def _update_find_count(self) -> None:
        needle = self.find_in.text()
        if not needle:
            self.find_lbl.setText("")
            return
        n = len(cuemodel.find_matches(self.cues, needle, case_sensitive=self._case()))
        self.find_lbl.setText(f"{n} câu khớp" if n else "không thấy")

    def _find_next(self) -> None:
        i = cuemodel.find_next(self.cues, self.find_in.text(), self._cur() + 1,
                               case_sensitive=self._case())
        if i >= 0:
            self.cue_list.selectRow(i)
        self._update_find_count()

    def _replace_one(self) -> None:
        needle = self.find_in.text()
        i = self._cur()
        if not needle:
            return
        # only replace where the cursor already sits on a match — otherwise the
        # first click would silently edit whichever row happened to be selected
        here = 0 <= i < len(self.cues) and bool(
            cuemodel.find_matches([self.cues[i]], needle, case_sensitive=self._case()))
        if not here:
            self._find_next()
            return
        self._snapshot()
        cuemodel.replace_in_cue(self.cues, i, needle, self.repl_in.text(),
                                case_sensitive=self._case())
        self._reload()
        self.cue_list.selectRow(i)
        self._find_next()

    def _replace_all(self) -> None:
        needle = self.find_in.text()
        if not needle:
            return
        if not cuemodel.find_matches(self.cues, needle, case_sensitive=self._case()):
            self.find_lbl.setText("không thấy")
            return                       # decide before snapshotting: a no-op must not
                                         # touch the undo stack or clear the redo chain
        self._snapshot()
        occ, lines = cuemodel.replace_all(self.cues, needle, self.repl_in.text(),
                                          case_sensitive=self._case())
        self._reload()
        self.find_lbl.setText(f"đã thay {occ} chỗ / {lines} câu")

    # --- inspector ------------------------------------------------------------
    def set_context(self, opts: dict) -> None:
        """tts/voice/rate/translator chosen in step 1 — used by preview & shorten."""
        self._ctx = opts or {}

    def _update_cue_panel(self, idx: int) -> None:
        self._sel = idx
        if not (0 <= idx < len(self.cues)):
            self.inspector.set_cue(-1, None, [])
            return
        self.inspector.set_cue(idx, self.cues[idx], self._issues_at(idx), lines=self._line_count(idx))
        self._update_listen()

    def _issues_at(self, i: int) -> list[str]:
        return self.issues[i] if 0 <= i < len(self.issues) else []

    def _line_count(self, i: int) -> int | None:
        from desktop.widgets.style_panel import estimate_line_count

        if not (0 <= i < len(self.cues)):
            return None
        c = self.cues[i]
        eff = c.style if c.style else self.get_style()
        try:
            return estimate_line_count(
                c.vi, size_px=self.get_size_px(), font=self.get_font(),
                margin_l=eff.get("margin_l", 60), margin_r=eff.get("margin_r", 60),
                bold=eff.get("bold", False), letter_spacing=eff.get("letter_spacing", 0))
        except Exception:  # noqa: BLE001 - a hint, never a blocker
            return None

    def _refresh_lines_badge(self) -> None:
        self.inspector.set_lines(self._line_count(self._sel))

    def refresh_approval(self) -> None:
        self._update_listen()

    def _update_listen(self) -> None:
        from app.pipeline import approval

        ok = self.job is not None and approval.is_approved(self.job)
        self.inspector.set_listen_enabled(
            ok, "Nghe giọng đọc của câu này" if ok else
            "Duyệt phụ đề trước khi tạo giọng; tiếng gốc luôn nghe được")

    def _on_vi_edited(self, text: str) -> None:
        i = self._sel
        if not (0 <= i < len(self.cues)) or self.cues[i].vi == text:
            return
        self.cues[i].vi = text
        self.cue_list.refresh_row(i)
        self.video.set_cues(self.cues)
        self.timeline.update()
        self._refresh_issues()
        self._refresh_lines_badge()
        self._mark_dirty()

    def _commit_next(self) -> None:
        self.inspector.end_session()
        nxt = self._sel + 1
        if 0 <= nxt < len(self.cues):
            self.cue_list.selectRow(nxt)
            self.inspector.focus_text()

    def _line_break(self) -> None:
        ed = self.inspector.vi_edit
        ed.setFocus()
        ed.insertPlainText("\n")

    def _on_gain(self, v: int) -> None:
        if 0 <= self._sel < len(self.cues):
            if not self.gain.isSliderDown():
                # keyboard/wheel step — a discrete action, so one undo step each.
                # During a mouse drag the sliderPressed snapshot already covers it.
                self._snapshot()
            self.cues[self._sel].gain_db = float(v)
            self.inspector.gain_lbl.setText(f"{v:+d} dB")
            self._mark_dirty()

    def _on_mute(self, on: bool) -> None:
        if 0 <= self._sel < len(self.cues):
            # snapshot HERE, not via a second toggled connection: this handler is
            # connected first, so a later connection would capture the post-edit state.
            self._snapshot()
            self.cues[self._sel].mute = on
            self.cue_list.refresh_row(self._sel)
            self._mark_dirty()

    def _on_lock(self, on: bool) -> None:
        if 0 <= self._sel < len(self.cues) and self.cues[self._sel].locked != on:
            self._snapshot()
            self.cues[self._sel].locked = on
            self.cue_list.refresh_row(self._sel)
            self.timeline.update()
            self._mark_dirty()

    # --- timing ---------------------------------------------------------------
    def _duration(self) -> float | None:
        d = self.video.duration()
        return d if d > 0 else None

    def _timing_edit(self, fn) -> None:
        """Run a timing mutation on the selected cue as one undo step; a refused
        edit (locked / no-op) leaves the undo stack untouched."""
        i = self._sel
        if not (0 <= i < len(self.cues)):
            return
        c = self.cues[i]
        if c.locked:
            self.message.emit("Câu đang khóa timing — bỏ khóa để canh.", "warn")
            return
        self._snapshot()
        if not fn(c):
            self._undo_stack.pop()
            return
        self.cue_list.refresh_row(i)
        self.inspector.refresh_timing(c)
        self.timeline.set_cues(self.cues)
        self.video.set_cues(self.cues)
        self._refresh_issues()
        self._mark_dirty()

    def _nudge(self, which: str, delta_ms: int) -> None:
        self._timing_edit(lambda c: cuemodel.nudge(c, which, delta_ms, duration=self._duration()))

    def _set_edge(self, which: str) -> None:
        t = self.video.current_time()
        self._timing_edit(lambda c: cuemodel.clamp_edge(c, which, t, duration=self._duration()))

    def _type_time(self, which: str, t: float) -> None:
        self._timing_edit(lambda c: cuemodel.clamp_edge(c, which, t, duration=self._duration()))
        if 0 <= self._sel < len(self.cues):
            self.inspector.refresh_timing(self.cues[self._sel])

    # --- tự canh lời (snap to the voice) ----------------------------------------
    def _set_speech(self, spans: list | None, why: str = "") -> None:
        self._speech = spans or None
        self.timeline.set_speech(spans or [])
        ok = bool(spans)
        self.inspector.set_snap_available(ok, why)
        self.timeline_panel.snap_all_btn.setEnabled(ok)
        if not ok and why:
            self.timeline_panel.snap_all_btn.setToolTip(why)

    def _load_speech(self, job) -> None:
        from app.pipeline import vad

        self._set_speech(None, "Đang phân tích giọng nói…")
        if not vad.model_ready():
            self.message.emit("Đang tải bộ nhận diện giọng nói (một lần, ~2 MB)…", "busy")
        notes: list[str] = []          # written on the worker thread, read in done()

        def done(spans):
            if self.job is not job:
                return
            if spans:
                self._set_speech(spans)
                return
            why = notes[-1].lstrip("⚠ ") if notes else "Không thấy giọng nói trong video."
            self._set_speech(None, why)
            if notes:
                self.message.emit(f"Tự canh lời chưa dùng được: {why}", "warn")

        self._run_async(lambda: vad.speech_spans(job, log=notes.append), done,
                        lambda m: self._set_speech(None, m))

    def _auto_snap_selected(self) -> None:
        """Snap the selected cue's edges to the nearest voice start / stop (≤ ~1 s);
        neighbours stay fixed. One undo step."""
        from app.pipeline import snap

        i = self._sel
        if not self._speech or not (0 <= i < len(self.cues)):
            return
        if self.cues[i].locked:
            self.message.emit("Câu đang khóa timing — bỏ khóa để canh.", "warn")
            return
        times, rep = snap.plan(self.cues, self._speech, duration=self._duration(), only={i})
        if i in rep.no_voice:
            self.message.emit("Không thấy giọng nói trong câu này — giữ nguyên.", "warn")
            return
        if i not in rep.changed:
            self.message.emit("Câu này đã khớp giọng nói.", "ok")
            return

        def apply(c) -> bool:
            c.start, c.end = times[i]
            return True

        self._timing_edit(apply)
        self.message.emit(f"Đã canh câu {i + 1} theo giọng nói — Ctrl+Z để hoàn tác.", "ok")

    def _auto_snap_all(self) -> None:
        """Snap every unlocked cue at once — one undo step."""
        from app.pipeline import snap

        if not self._speech or not self.cues:
            return
        times, rep = snap.plan(self.cues, self._speech, duration=self._duration())
        if not rep.changed:
            self.message.emit("Mọi câu đã khớp giọng nói (hoặc không thấy giọng) — không đổi gì.",
                              "ok")
            return
        self._snapshot()
        for k in rep.changed:
            self.cues[k].start, self.cues[k].end = times[k]
        self._reload()
        parts = [f"Đã canh {len(rep.changed)} câu theo giọng nói"]
        if rep.locked:
            parts.append(f"bỏ qua {len(rep.locked)} câu khóa")
        if rep.no_voice:
            parts.append(f"{len(rep.no_voice)} câu không thấy giọng")
        self.message.emit(" · ".join(parts) + " — Ctrl+Z để hoàn tác.", "ok")

    def _fit_selection(self) -> None:
        if 0 <= self._sel < len(self.cues):
            c = self.cues[self._sel]
            self.timeline_panel.fit_span(c.start, c.end)

    # --- review ---------------------------------------------------------------
    def _refresh_issues(self) -> None:
        ocr, term = self._spans
        self.issues = review.cue_issues(self.cues, ocr_spans=ocr, term_spans=term)
        self.cue_list.set_issues(self.issues)
        self.timeline.set_issues(self.issues)
        if 0 <= self._sel < len(self.cues):
            self.inspector.set_issues(self._issues_at(self._sel))

    def _mark_reviewed(self) -> None:
        i = self._sel
        if not (0 <= i < len(self.cues)):
            return
        cuemodel.mark_reviewed(self.cues[i])
        self.cue_list.refresh_row(i)
        self._mark_dirty()
        nxt = self.cue_list.next_pending(i)
        self.cue_list.reapply_filter()
        if nxt >= 0 and nxt != i:
            self.cue_list.selectRow(nxt)
        elif not self.cue_list.pending_rows():
            self.message.emit("Bạn đã xem hết các câu cần chú ý.", "ok")

    def _goto_pending(self, step: int) -> None:
        rows = self.cue_list.pending_rows()
        if not rows:
            self.message.emit("Không còn câu nào cần xem lại.", "ok")
            return
        cur = self._sel
        if step > 0:
            nxt = next((r for r in rows if r > cur), rows[0])
        else:
            nxt = next((r for r in reversed(rows) if r < cur), rows[-1])
        self.cue_list.selectRow(nxt)

    def pending_count(self) -> int:
        return len(self.cue_list.pending_rows())

    # --- dub preview / shorten -------------------------------------------------
    def _toast(self, msg: str) -> None:
        self.message.emit(msg, "warn")

    def _preview_dub(self) -> None:
        """Synthesize the selected line's VI (off-thread) and play it. Only after
        approval (the inspector disables it before) — no TTS on unapproved text."""
        if not (0 <= self._sel < len(self.cues)) or self._dub_player is None or self.job is None:
            return
        text = self.cues[self._sel].vi.strip()
        if not text:
            return
        gain_db = self.cues[self._sel].gain_db
        out = self.job.work_dir / "_dub_preview.mp3"
        tts_name = self._ctx.get("tts", "edge")
        voice, rate = self._ctx.get("voice"), self._ctx.get("rate")
        self.inspector.set_busy("listen", True)

        def work():
            from app.providers.registry import get_tts
            get_tts(tts_name).synthesize(text, out, voice=voice, rate=rate)
            return str(out)

        def done(path):
            from PySide6.QtCore import QUrl
            self._dub_audio.setVolume(min(1.0, 10 ** (gain_db / 20.0)))
            self._dub_player.setSource(QUrl.fromLocalFile(str(path)))
            self._dub_player.play()
            self.inspector.set_busy("listen", False)

        def fail(msg):
            self.inspector.set_busy("listen", False)
            self._toast(f"Lỗi nghe thử: {msg}")

        self._run_async(work, done, fail)

    def _shorten(self) -> None:
        """Ask the LLM to shorten the selected VI line so it fits its dub slot."""
        if not (0 <= self._sel < len(self.cues)):
            return
        idx = self._sel
        text = self.cues[idx].vi.strip()
        if not text:
            return
        provider = self._ctx.get("translator") or "openrouter"
        self.inspector.set_busy("shorten", True)

        def work():
            from app.pipeline.translate import shorten_line
            return shorten_line(text, provider=provider)

        def done(result):
            from desktop.widgets.dialogs import ShortenDialog

            self.inspector.set_busy("shorten", False)
            if not (result and result != text and 0 <= idx < len(self.cues)):
                self.message.emit("AI không rút gọn thêm được câu này.", "warn")
                return
            dlg = ShortenDialog(self, index=idx, current=text, proposal=result)
            if dlg.exec() and dlg.text() and dlg.text() != self.cues[idx].vi:
                self._snapshot()
                self.cues[idx].vi = dlg.text()
                self.cue_list.refresh_row(idx)
                self.video.set_cues(self.cues)
                self._refresh_issues()
                self._update_cue_panel(idx)
                self._mark_dirty()

        def fail(msg):
            self.inspector.set_busy("shorten", False)
            self._toast(f"Lỗi rút gọn: {msg}")

        self._run_async(work, done, fail)

    def shutdown(self) -> None:
        """Join any short-lived task threads so closing the window never tears down
        a running QThread (hard crash)."""
        for thread, _task in list(self._tasks):
            try:
                thread.quit()
                thread.wait(2000)
            except RuntimeError:
                pass

    def _run_async(self, fn, on_done, on_fail=None) -> None:
        thread = QThread()
        task = _Task(fn)
        pair = (thread, task)
        self._tasks.append(pair)
        task.moveToThread(thread)
        thread.started.connect(task.run)
        connect_on_ui(task.done, on_done, self)     # closures touch widgets → GUI thread
        if on_fail:
            connect_on_ui(task.failed, on_fail, self)
        task.done.connect(thread.quit)
        task.failed.connect(thread.quit)
        thread.finished.connect(task.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._tasks.remove(pair) if pair in self._tasks else None)
        thread.start()

    # --- load / save ---------------------------------------------------------
    def load(self, job) -> None:
        self.flush()   # job switch: save the OLD job's pending edit before replacing it
        self.job = job
        self.cues = cuemodel.load_pair(job)
        try:
            self._spans = review.load_flag_spans(job)
        except Exception:  # noqa: BLE001 - flags are hints; never block opening a job
            self._spans = ([], [])
        self._meta = {"title": _job_title(job), "platform": job.metadata.get("platform") or
                      _job_field(job, "platform") or "", "dur": 0.0, "dims": (0, 0)}
        self._update_project()
        self.video.load(job.source_video)
        self.timeline.set_peaks([])
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._sel = -1
        self._reload(dirty=False)
        st = self._load_style(job)
        if st:
            self.style_panel.set_from(st)
        self._set_global_from_panel()
        self.video.set_style(self._g_style, self._g_size, self._g_font)
        self._update_scope_hint()
        self.inspector.set_context(*_brief_and_note(job))
        if self.cues:
            self.cue_list.selectRow(0)
        self._load_waveform(job)
        self._load_speech(job)

    def _load_waveform(self, job) -> None:
        from app.pipeline import waveform

        def done(peaks):
            if self.job is job:
                self.timeline.set_peaks(peaks, waveform.PER_SEC)

        self._run_async(lambda: waveform.peaks(job), done, lambda _m: None)

    def _reload(self, *, dirty: bool = True) -> None:
        ocr, term = self._spans
        self.issues = review.cue_issues(self.cues, ocr_spans=ocr, term_spans=term)
        self.cue_list.load(self.cues, self.issues)
        self.timeline.set_cues(self.cues)
        self.timeline.set_issues(self.issues)
        self.video.set_cues(self.cues)
        if self._sel >= len(self.cues):
            self._sel = len(self.cues) - 1
        self._update_cue_panel(self._sel)
        if dirty:
            self._mark_dirty()

    # --- project bar ------------------------------------------------------------
    def _on_duration(self, d: float) -> None:
        self._meta["dur"] = d
        self.timeline.set_duration(d)
        self._update_project()

    def _on_dims(self, w: int, h: int) -> None:
        self._meta["dims"] = (w, h)
        self._update_project()

    def _update_project(self) -> None:
        m = self._meta
        parts = [{"douyin": "Douyin", "bilibili": "Bilibili"}.get(m["platform"], m["platform"] or "")]
        if m["dur"]:
            parts.append(fmt_time(m["dur"], 0))
        if m["dims"][0]:
            parts.append(f"{m['dims'][0]} × {m['dims'][1]}")
        self.project.set_project(m["title"] or "Dự án", " · ".join(p for p in parts if p))

    # --- undo / redo ---------------------------------------------------------
    def _copy_cues(self) -> list[EditorCue]:
        return [cuemodel.copy_cue(c) for c in self.cues]

    def _snapshot(self) -> None:
        """Record the state BEFORE an edit. A fresh edit invalidates the redo chain
        (standard editor semantics: you cannot redo a future you just diverged from)."""
        self._undo_stack.append(self._copy_cues())
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _undo_action(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self._copy_cues())
        self.cues = self._undo_stack.pop()
        self.inspector.end_session()
        self._reload()

    def _redo_action(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(self._copy_cues())
        self.cues = self._redo_stack.pop()
        self.inspector.end_session()
        self._reload()

    def _save(self) -> None:
        if self.job is None:
            return
        try:
            cuemodel.save_vi(self.job, self.cues)
            self._save_style()
        except OSError as exc:
            # never report "Đã lưu" for a failed write; keep the edit and retry
            store.debug(f"autosave FAILED: {exc}")
            self.saveFailed.emit(str(exc))
            self._save_timer.start(RETRY_MS)
            return
        self._save_timer.setInterval(SAVE_MS)
        self.dirtyChanged.emit(False)
        self._update_listen()

    # --- style (delegates to the Kiểu chữ tab) -------------------------------
    def _set_global_from_panel(self) -> None:
        self._g_font = self.style_panel.get_font()
        self._g_size = self.style_panel.get_size_px()
        self._g_style = self.style_panel.get_style()

    def _on_style_changed(self) -> None:
        # scope "selected" applies to the chosen/current cue; if there is none, fall
        # back to the global style so an edit is NEVER silently lost.
        if self.style_panel.get_scope() == "selected":
            self._snapshot()        # per-line style lives on the cues → undoable
            if not self._apply_style_to_selection():
                self._undo_stack.pop()
                self._set_global_from_panel()
                self.video.set_style(self._g_style, self._g_size, self._g_font)
        else:
            self._set_global_from_panel()                # update the global (Default) style
            self.video.set_style(self._g_style, self._g_size, self._g_font)
        self._refresh_lines_badge()
        self._mark_dirty()

    def _scope_rows(self) -> tuple[list[int], str]:
        """Which cues a per-line style edit targets: the highlighted rows (Shift /
        Ctrl-click in the list), else the cue in the inspector."""
        sel = self.cue_list.selected_rows()
        if len(sel) > 1:
            return sel, "đang chọn"
        if 0 <= self._sel < len(self.cues):
            return [self._sel], "đang xem"
        return [], ""

    def _update_scope_hint(self) -> None:
        if self.style_panel.get_scope() != "selected":
            self.style_panel.set_scope_hint("→ áp cho toàn bộ phụ đề")
            return
        rows, src = self._scope_rows()
        if not rows:
            self.style_panel.set_scope_hint(
                "→ chưa chọn câu nào. Bấm một câu trong danh sách; Shift / Ctrl-click "
                "để chọn nhiều câu.")
        else:
            nums = ", ".join("#" + str(r + 1) for r in rows[:8])
            more = "…" if len(rows) > 8 else ""
            self.style_panel.set_scope_hint(f"→ áp cho {len(rows)} câu {src}: {nums}{more}")

    def _apply_style_to_selection(self) -> bool:
        rows, _src = self._scope_rows()
        if not rows:
            return False
        s = self.style_panel.get_style()
        for r in rows:
            if 0 <= r < len(self.cues):
                self.cues[r].style = dict(s)
                self.cue_list.refresh_row(r)
        self.video.set_cues(self.cues)
        return True

    def _reset_selected_style(self) -> None:
        rows, _src = self._scope_rows()
        if not any(0 <= r < len(self.cues) and self.cues[r].style is not None for r in rows):
            return
        self._snapshot()
        for r in rows:
            if 0 <= r < len(self.cues) and self.cues[r].style is not None:
                self.cues[r].style = None
                self.cue_list.refresh_row(r)
        self.video.set_cues(self.cues)
        self._refresh_lines_badge()   # selected cue reverts to the global line count
        self._mark_dirty()

    def get_style(self) -> dict:
        return self._g_style if self._g_style is not None else self.style_panel.get_style()

    def get_font(self) -> str:
        return self._g_font

    def get_size_px(self) -> int:
        return self._g_size

    def _reburn_preview(self) -> None:
        """After a drag, burn one exact FFmpeg frame at the current time and show it
        on the canvas (true WYSIWYG). Off-thread; one at a time."""
        if self.job is None or self._burning:
            return
        self._burning = True
        from app.pipeline import assemble
        job, cues = self.job, self.cues
        size = int(round(self.get_size_px() * 3))
        font, style = self.get_font(), self.get_style()
        at = self.video.current_time()

        def work():
            return str(assemble.style_preview_frame(
                job, cues, at=at, font=font, size=size, cover_hardsubs=False, style=style))

        def done(path):
            self.video.set_burned_overlay(path)
            self._burning = False

        def fail(_msg):
            self._burning = False

        self._run_async(work, done, fail)

    def _load_style(self, job):
        import json
        p = job.work_dir / "style.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
        return None

    def _save_style(self) -> None:
        import json
        data = {"font": self.get_font(), "size": self.get_size_px(), "style": self.get_style()}
        (self.job.work_dir / "style.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_now(self) -> None:
        self._save_timer.stop()
        self._save()

    def flush(self) -> None:
        """Persist a pending debounced autosave immediately. The 600 ms window
        means the newest edit lives only in memory — without this, quitting or
        switching jobs inside that window silently drops it. No-op when clean."""
        if self._save_timer.isActive():
            self.save_now()

    def _mark_dirty(self) -> None:
        self.dirtyChanged.emit(True)
        self._save_timer.start()

    # --- sync ----------------------------------------------------------------
    def _on_row_selected(self, idx: int) -> None:
        self._select(idx, seek=not self._following)

    def _select(self, idx: int, *, seek: bool) -> None:
        if idx != self._sel:
            self.inspector.end_session()
        self.timeline.set_selected(idx)
        self._update_cue_panel(idx)
        self._update_scope_hint()
        if seek and 0 <= idx < len(self.cues):
            self.video.seek_seconds(self.cues[idx].start)
            self.timeline_panel.ensure_visible(self.cues[idx].start)

    def _editing(self) -> bool:
        ins = self.inspector
        return ins.vi_edit.hasFocus() or any(f.hasFocus() for f in ins.t_fields.values())

    def _on_position(self, t: float) -> None:
        self.timeline.set_time(t)
        self.inspector.set_playhead(t)
        if not self.video.is_playing():
            return
        sel = self._sel
        if self.video.loop_enabled() and 0 <= sel < len(self.cues):
            c = self.cues[sel]
            if t >= c.end or t < c.start - 0.25:
                self.video.seek_seconds(c.start)
                return
        if not self.cue_list.follow_enabled():
            return
        self.timeline_panel.ensure_visible(t)
        if self._editing():
            return                 # never yank the selection out from under the user
        if 0 <= sel < len(self.cues) and self.cues[sel].start <= t < self.cues[sel].end:
            return                 # overlap: the user's cue wins
        i = next((k for k, c in enumerate(self.cues) if c.start <= t < c.end), -1)
        if i >= 0 and i != sel:
            self._following = True
            try:
                self.cue_list.selectRow(i, center=False)
            finally:
                self._following = False

    def _on_cue_dragged(self, idx: int) -> None:
        self.cue_list.refresh_row(idx)
        if idx == self._sel and 0 <= idx < len(self.cues):
            self.inspector.refresh_timing(self.cues[idx])
        self._mark_dirty()

    # --- tools ---------------------------------------------------------------
    def _cur(self) -> int:
        return self.cue_list.currentRow()

    def _add(self) -> None:
        self._snapshot()
        i = self._cur()
        last = self.cues[-1] if self.cues else None
        start = (self.cues[i].end if 0 <= i < len(self.cues) else (last.end if last else 0.0))
        at = i + 1 if i >= 0 else len(self.cues)
        self.cues.insert(at, EditorCue(start=start, end=start + 2.0, vi="", zh=""))
        self._reload()
        self.cue_list.selectRow(at)
        self.inspector.focus_text()

    def _delete(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues):
            self._snapshot()
            self.cues.pop(i)
            self._reload()
            if self.cues:
                self.cue_list.selectRow(min(i, len(self.cues) - 1))

    def _split(self) -> None:
        """Split the selected cue at the playhead (design: the playhead must be
        inside the cue). Policy lives in cuemodel.split_at (pure, tested)."""
        i = self._cur()
        if not (0 <= i < len(self.cues)):
            return
        parts = cuemodel.split_at(self.cues[i], self.video.current_time())
        if parts is None:
            self.message.emit("Đưa vị trí phát vào trong câu (cách mép ≥ 0.2 s) rồi bấm Tách.",
                              "warn")
            return
        self._snapshot()
        self.cues[i:i + 1] = list(parts)
        self._reload()
        self.cue_list.selectRow(i + 1, center=False)

    def _merge(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues) - 1:
            self._snapshot()
            # policy lives in cuemodel.merge_cues (pure, headless-tested)
            self.cues[i:i + 2] = [cuemodel.merge_cues(self.cues[i], self.cues[i + 1])]
            self._reload()
            self.cue_list.selectRow(i, center=False)


# --- job helpers ---------------------------------------------------------------------

def _job_field(job, key: str):
    import json

    try:
        return json.loads((job.work_dir / "job.json").read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError):
        return None


def _job_title(job) -> str:
    """Douyin titles are the whole post caption — keep the part before the hashtags."""
    raw = (job.metadata.get("title") or _job_field(job, "title") or job.id).strip()
    head = raw.split("#", 1)[0].strip()
    return head or raw


def _brief_and_note(job) -> tuple[str | None, str | None]:
    import json

    from app.pipeline import brief

    try:
        data = json.loads((job.work_dir / brief.BRIEF_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    b = data.get("brief") if isinstance(data.get("brief"), str) else None
    n = data.get("note") if isinstance(data.get("note"), str) else None
    return (b or None), (n or None)
