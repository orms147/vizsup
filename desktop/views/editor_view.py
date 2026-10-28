"""Screen 2 — Subtitle Editor (the human gate), v2 layout (appdesign/v2):

    ProjectBar  (title · meta · CN→VI · layout · Thuật ngữ)
    [ Video nguồn | N câu (list + review filter) | Câu đang chọn / Kiểu chữ / Ngữ cảnh ]
    [ Dòng thời gian (real waveform · blocks · zoom)                                   ]

Edits autosave to job.vi_srt (600 ms debounce; a failed write says so and retries).
Every mutation of ``self.cues`` goes through ``_snapshot()`` first (undo/redo).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.pipeline import review
from desktop import cuemodel, store
from desktop.audio import follow_default_output
from desktop.cuemodel import EditorCue, fmt_time
from desktop.ui_thread import connect_on_ui, emit_quietly, guarded, spawn
from desktop.widgets.chrome import ProjectBar, badge, button, icon_button, label
from desktop.widgets.commentary_panel import CommentaryPanel
from desktop.widgets.music_panel import MusicPanel
from desktop.widgets.cue_inspector import CueInspector
from desktop.widgets.cue_list import CueList
from desktop.widgets.style_panel import StylePanel
from desktop.widgets.timeline import OVERVIEW_H, TimelinePanel
from desktop.widgets.video_panel import VideoPanel
from app.fsutil import atomic_write_text

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    _HAVE_MM = True
except Exception:  # noqa: BLE001
    _HAVE_MM = False

SAVE_MS, RETRY_MS = 600, 4000
LAYOUTS = {"balanced": ("Bố cục: cân bằng", (36, 38, 26)),
           "video": ("Bố cục: ưu tiên video", (64, 36, 0))}   # 0 = the inspector hides
COVER_STYLE = "Hộp đặc (che chữ Trung)"   # style_panel.BOX_STYLES key
BULK_TARGET_CPS = 20.0     # "Rút gọn hàng loạt" aims under the 25 cps flag, with headroom
# wide windows: past these widths the inspector / cue list only spread their content
# apart, so the extra room goes to the video instead
INSPECTOR_MAX, LIST_MAX = 560, 820
PANEL_CHROME = 112         # video panel head + control bar (the rest is the picture)


VIDEO_MIN = 300            # the video pane never narrower (its controls need it)
PORTRAIT_SLACK = 24        # a little air around a vertical picture


def split_sizes(total: int, layout: str, inspector_min: int = 0, *, aspect: float = 0.0,
                pic_h: int = 0) -> list[int]:
    """[video, cue list, inspector] widths for a ``total``-px row: the layout's ratio,
    the inspector never below what its tabs need (``inspector_min``) nor above
    INSPECTOR_MAX, the list capped at LIST_MAX, the video takes the rest.

    A vertical source (``aspect`` = width / height < 1, 9:16 for Douyin) only needs
    ``pic_h * aspect`` px: in "cân bằng" the video column shrinks to that and the width it
    would have wasted on black bars goes to the list, then the inspector."""
    rv, rl, ri = LAYOUTS[layout][1]
    ins = 0 if ri == 0 else max(inspector_min, min(int(total * ri / 100), INSPECTOR_MAX))
    rest = max(0, total - ins)
    lst = min(int(rest * rl / (rv + rl)), LIST_MAX)
    vid = rest - lst
    if layout == "balanced" and 0 < aspect < 1 and pic_h > 0:
        want = max(VIDEO_MIN, int(pic_h * aspect) + PORTRAIT_SLACK)
        if want < vid:
            vid = want
            lst = min(rest - vid, LIST_MAX)
            spare = rest - vid - lst
            if ins and spare > 0:
                more = min(spare, max(0, INSPECTOR_MAX - ins))
                ins += more
                spare -= more
            vid += spare              # nothing left to give: the picture takes the rest
    return [vid, lst, ins]


class _Splitter(QSplitter):
    """QSplitter that says when it was resized (after it laid its panes out)."""

    resized = Signal()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self.resized.emit()


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
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            emit_quietly(self.failed, f"{type(exc).__name__}: {exc}")
            return
        emit_quietly(self.done, result)


SEEK_STEP = 10.0       # ← / → jump, seconds (YouTube's)


class _FullscreenHost(QWidget):
    """The top-level window the video panel moves into for full screen. It has its own
    keys: the editor's shortcuts belong to the main window."""

    def __init__(self, editor: "EditorView") -> None:
        super().__init__(None, Qt.Window)
        self._editor = editor
        self.setWindowTitle("vizsup — video")
        self.setStyleSheet("background:#000;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        ed, k = self._editor, ev.key()
        if k in (Qt.Key_Escape, Qt.Key_F):
            ed.toggle_fullscreen()
        elif k == Qt.Key_Space:
            ed.video.toggle()
        elif k == Qt.Key_Left:
            ed.seek_by(-SEEK_STEP)
        elif k == Qt.Key_Right:
            ed.seek_by(+SEEK_STEP)
        elif k == Qt.Key_Up:
            ed.step_cue(-1)
        elif k == Qt.Key_Down:
            ed.step_cue(+1)
        else:
            super().keyPressEvent(ev)

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self._editor.is_fullscreen():           # closed from the taskbar / Alt+F4
            ev.ignore()
            self._editor.toggle_fullscreen()
            return
        super().closeEvent(ev)

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
        self._guess: list = []                      # ASR-estimated timing (timing_report)
        self._qa: list = []                         # AI review findings (qa_report, E-5)
        self._busy_bulk = False                     # one "Rút gọn hàng loạt" at a time
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
            follow_default_output(self._dub_audio)
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
        self.commentary = CommentaryPanel()                 # F-4: the user's own notes
        self.commentary_tab = self.inspector.add_page(
            "Bình luận", self.commentary, "Lời bình của bạn: giọng khác hoặc tự thu")
        self.music = MusicPanel()                           # the user's background music
        self.music_tab = self.inspector.add_page(
            "Nhạc", self.music, "Nhạc nền của bạn cho cả video hoặc từng đoạn")
        self.gain, self.mute_cb = self.inspector.gain, self.inspector.mute_cb
        self.timeline_panel = TimelinePanel()
        self.timeline = self.timeline_panel.timeline

        self.top = _Splitter(Qt.Horizontal)
        self.top.setHandleWidth(8)
        self.top.setChildrenCollapsible(False)
        self.video.setMinimumWidth(260)
        self.cue_list.setMinimumWidth(320)
        self.inspector.setMinimumWidth(280)
        for w in (self.video, self.cue_list, self.inspector):
            self.top.addWidget(w)
        # the layout ratio follows the window (maximize, 1366 → 2560) until the user
        # drags a handle; after that only the video and the list take extra width
        self._user_split = False
        self._aspect = 0.0                  # source width / height once known (vertical → < 1)
        self.top.splitterMoved.connect(lambda *_: setattr(self, "_user_split", True))
        self.top.resized.connect(lambda: self._user_split or self._fit_split())
        for i, stretch in enumerate((3, 2, 0)):
            self.top.setStretchFactor(i, stretch)
        self.split = QSplitter(Qt.Vertical)
        self.split.setHandleWidth(8)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.top)
        self.split.addWidget(self.timeline_panel)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.timeline_panel.setMinimumHeight(150 + OVERVIEW_H)     # + the whole-episode strip
        # the rows keep their widths (video | list | inspector, timeline) and scroll sideways
        # when the window is narrower — the window itself never grows past the screen
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidget(self.split)
        bl.addWidget(self._scroll, 1)
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
        # undo / redo for mouse users too (Ctrl+Z / Ctrl+Y stay the fast path)
        self.undo_btn = icon_button("Undo2", "Hoàn tác (Ctrl+Z)")
        self.undo_btn.clicked.connect(self._undo_action)
        self.redo_btn = icon_button("Redo2", "Làm lại (Ctrl+Y)")
        self.redo_btn.clicked.connect(self._redo_action)
        self.project.add_action(self.undo_btn)
        self.project.add_action(self.redo_btn)
        self.layout_btn = button("Cân bằng", "PanelRightClose", variant="ghost",
                                 tip="Đổi bố cục: cân bằng / ưu tiên video (video to hơn, tạm ẩn bảng chỉnh sửa bên phải)")
        self.layout_btn.clicked.connect(self._toggle_layout)
        self.project.add_action(self.layout_btn)
        gl = button("Thuật ngữ", "BookOpen", tip="Sửa bảng thuật ngữ Trung → Việt (tự nạp khi dịch)")
        gl.clicked.connect(self.glossaryRequested)
        self.project.add_action(gl)
        return self.project

    def _wire(self) -> None:
        cl, ins, tl, vid = self.cue_list, self.inspector, self.timeline, self.video
        cl.rowSelected.connect(self._on_row_selected)
        cl.contextRequested.connect(self._show_cue_menu)
        cl.csvExportRequested.connect(self._export_csv)
        cl.csvImportRequested.connect(self._import_csv)
        tl.contextRequested.connect(self._show_cue_menu)
        cl.editRequested.connect(lambda: ins.focus_text(select_all=True))
        cl.splitRequested.connect(self._split)
        cl.mergeRequested.connect(self._merge)
        cl.addRequested.connect(self._add)
        cl.deleteRequested.connect(self._delete)
        cl.snapAllRequested.connect(self._auto_snap_all)
        cl.retimeRequested.connect(self._retime)
        cl.bulkShortenRequested.connect(self._bulk_shorten)
        cl.aiSuggestionsRequested.connect(self._ai_suggestions)
        cl.tidyRequested.connect(self._tidy_timing)
        cl.clauseSplitRequested.connect(self._split_clauses)
        cl.timingPassRequested.connect(self._timing_pass)
        cl.viLintRequested.connect(self._vi_lint)
        cl.findRequested.connect(lambda: self._toggle_find())
        cl.reviewedRequested.connect(self._mark_reviewed)
        cl.nextPendingRequested.connect(lambda: self._goto_pending(+1))
        cl.view.selectionModel().selectionChanged.connect(lambda *_: self._update_scope_hint())

        ins.editStarted.connect(self._snapshot)        # one undo step per editing session
        ins.viEdited.connect(self._on_vi_edited)
        ins.commitNext.connect(self._commit_next)
        ins.leaveRequested.connect(lambda: self.cue_list.view.setFocus())
        ins.editFocused.connect(vid.pause)
        ins.nudgeRequested.connect(self._nudge)
        ins.setEdgeRequested.connect(self._set_edge)
        ins.timeTyped.connect(self._type_time)
        ins.lockToggled.connect(self._on_lock)
        ins.lineBreakRequested.connect(self._line_break)
        ins.autoSnapRequested.connect(self._auto_snap_selected)
        ins.applyFixRequested.connect(self._apply_ai_fix)
        ins.retranslateRequested.connect(self._retranslate)
        ins.editStyleRequested.connect(self._edit_style)
        ins.shortenRequested.connect(self._shorten)
        ins.listenRequested.connect(self._preview_dub)
        cm_ = self.commentary
        cm_.changed.connect(self._save_commentary)
        cm_.addRequested.connect(self._add_note)
        cm_.hereRequested.connect(self._note_to_playhead)
        cm_.seekRequested.connect(lambda t: self.video.seek_seconds(t))
        cm_.previewRequested.connect(self._preview_note)
        cm_.importRequested.connect(self._import_note_audio)
        cm_.list.currentRowChanged.connect(lambda _i: self._note_warning())
        mp = self.music
        mp.changed.connect(self._save_music)
        mp.addRequested.connect(self._add_music)
        mp.startHereRequested.connect(
            lambda: mp.update_selected(start=round(self.video.current_time(), 2)))
        mp.endHereRequested.connect(
            lambda: mp.update_selected(end=round(self.video.current_time(), 2)))
        mp.seekRequested.connect(lambda t: self.video.seek_seconds(t))
        mp.previewRequested.connect(self._preview_music)
        mp.replaceRequested.connect(self._replace_music_file)
        mp.list.currentRowChanged.connect(lambda _i: self._music_warning())
        ins.glossaryRequested.connect(self.glossaryRequested)
        self.gain.sliderPressed.connect(self._snapshot)   # once per slider gesture
        self.gain.valueChanged.connect(self._on_gain)
        self.mute_cb.toggled.connect(self._on_mute)
        ins.voice_combo.activated.connect(self._on_voice)

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
        vid.expandRequested.connect(self.toggle_fullscreen)
        vid.marginsChanged.connect(self.style_panel.set_drag_margins)  # drag box → margins
        vid.dragCommitted.connect(self._reburn_preview)                # release → exact frame

        self.style_panel.previewRequested.connect(self.previewStyleRequested)
        self.style_panel.styleChanged.connect(self._on_style_changed)
        self.style_panel.scopeChanged.connect(self._update_scope_hint)
        self.style_panel.resetSelectedRequested.connect(self._reset_selected_style)

        # undo/redo: anything that mutates self.cues WITHOUT a _snapshot() first is
        # a hole in undo; add the call rather than special-casing it here.
        self._fs: QWidget | None = None       # the full-screen window, when shown
        self._fs_index, self._fs_sizes = 0, []
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
        QShortcut(QKeySequence("Ctrl+H"), self,
                  activated=lambda: self._toggle_find(True, replace=True))
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar,
                        activated=lambda: self._toggle_find(False))
        esc.setContext(Qt.WidgetWithChildrenShortcut)   # don't swallow Esc app-wide
        sc("Space", self.video.toggle)
        sc("Ctrl+Space", lambda: self.video.set_loop(not self.video.loop_enabled()))
        sc("[", lambda: self._set_edge("start"))
        sc("]", lambda: self._set_edge("end"))
        sc("S", self._split)
        sc("M", self._merge)
        sc("Alt+R", self._retranslate)
        sc("A", self._auto_snap_selected)
        sc("Shift+A", self._auto_snap_all)
        # ↑/↓ = previous / next line, ←/→ = 10 s back / ahead like a video player (held
        # down they repeat). Text fields keep their own arrows (ShortcutOverride).
        sc("Up", lambda: self.step_cue(-1))
        sc("Down", lambda: self.step_cue(+1))
        sc("Left", lambda: self.seek_by(-SEEK_STEP))
        sc("Right", lambda: self.seek_by(+SEEK_STEP))
        sc("F", self.toggle_fullscreen)
        sc("D", self._mark_reviewed)            # "Đã xem" + jump to the next pending line
        sc("Ctrl+D", self._mark_reviewed)       # the same from inside the text box
        sc("Ctrl+Up", lambda: self.step_cue(-1, keep_typing=True))
        sc("Ctrl+Down", lambda: self.step_cue(+1, keep_typing=True))
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

    # --- keyboard navigation / full screen --------------------------------------------
    def step_cue(self, step: int, *, keep_typing: bool = False) -> None:
        """↑ / ↓: the previous / next line (from the one at the playhead when none is
        selected); the video jumps to it. ``keep_typing`` (Ctrl+↑/↓ from the text box)
        leaves the cursor in the box of the new line."""
        if not self.cues:
            return
        cur = self._sel
        if 0 <= cur < len(self.cues):
            idx = cur + step
        else:
            t = self.video.current_time()
            inside = next((i for i, c in enumerate(self.cues) if c.start <= t < c.end), None)
            if inside is not None:
                idx = inside + step
            elif step > 0:          # in a gap: the next line ahead / the one just passed
                idx = next((i for i, c in enumerate(self.cues) if c.start > t), len(self.cues) - 1)
            else:
                idx = max((i for i, c in enumerate(self.cues) if c.end <= t), default=0)
        idx = max(0, min(len(self.cues) - 1, idx))
        typing = keep_typing and self._editing()
        self.cue_list.selectRow(idx)
        self._select(idx, seek=True)
        if typing:
            self.inspector.focus_text()

    def seek_by(self, seconds: float) -> None:
        """← / →: jump back / ahead (10 s, like YouTube), clamped to the video."""
        t = self.video.current_time() + seconds
        dur = self.video.duration()
        t = max(0.0, min(t, dur) if dur > 0 else t)
        self.video.seek_seconds(t)
        self.timeline.set_time(t)
        self.timeline_panel.ensure_visible(t)

    def is_fullscreen(self) -> bool:
        return self._fs is not None

    def toggle_fullscreen(self) -> None:
        """The video alone on the whole screen; F / Esc / ⤢ bring it back. Space, ←/→,
        ↑/↓ keep working there."""
        if self._fs is None:
            self._fs_index = self.top.indexOf(self.video)
            self._fs_sizes = self.top.sizes()
            host = _FullscreenHost(self)
            host.layout().addWidget(self.video)          # reparents it out of the splitter
            self.video.set_fullscreen_icon(True)
            self._fs = host
            host.showFullScreen()
            host.activateWindow()
            host.setFocus()
            return
        host, self._fs = self._fs, None
        self.top.insertWidget(self._fs_index, self.video)
        self.top.setSizes(self._fs_sizes)
        self.video.set_fullscreen_icon(False)
        host.hide()
        host.deleteLater()
        self.video.show()

    # --- layout ---------------------------------------------------------------
    def _toggle_layout(self) -> None:
        self._apply_layout("video" if self._layout == "balanced" else "balanced")

    def _apply_layout(self, name: str, *, initial: bool = False) -> None:
        self._layout = name
        self.layout_btn.setText(LAYOUTS[name][0])
        # "ưu tiên video" = the picture gets the room: the editing panel steps aside until
        # the user picks "cân bằng" again (a wider video cannot grow while it keeps ~650 px)
        self.inspector.setVisible(name != "video")
        self._user_split = False
        self._fit_split()
        if initial:
            self.split.setSizes([560, 170 + OVERVIEW_H])

    def _fit_split(self) -> None:
        """Size the three panes for the current width (split_sizes); the inspector's
        minimum follows its widest tab so its time fields are never cut off."""
        ins = self.inspector.content_min_width()
        self.inspector.setMinimumWidth(ins)
        total = (self.top.width() or 1330) - 2 * self.top.handleWidth()
        # the picture's height = the row's height minus the video panel's head and controls
        pic_h = max(0, self.top.height() - PANEL_CHROME) if self.top.height() > 0 else 0
        self.top.setSizes(split_sizes(max(900, total), self._layout, ins, aspect=self._aspect,
                                      pic_h=pic_h))

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
        # one row for plain searching (the common case at 768 px high: it leaves the list
        # its room); "Thay…" / Ctrl+H adds the replace row under it
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        self.find_in = QLineEdit()
        self.find_in.setPlaceholderText("Tìm trong tiếng Việt…")
        self.find_in.returnPressed.connect(self._find_next)
        self.find_in.textChanged.connect(self._update_find_count)
        self.find_lbl = label("", "small")
        nxt = button("Tiếp", small=True, tip="Tới câu khớp tiếp theo (Enter)")
        nxt.clicked.connect(self._find_next)
        self.find_case = QCheckBox("Aa")
        self.find_case.setToolTip("Phân biệt hoa / thường")
        self.find_case.toggled.connect(self._update_find_count)
        self.repl_toggle = button("Thay…", small=True, checkable=True,
                                  tip="Hiện ô thay thế (Ctrl+H)")
        self.repl_toggle.toggled.connect(self._show_replace)
        close = icon_button("X", "Đóng (Esc)")
        close.clicked.connect(lambda: self._toggle_find(False))
        r1.addWidget(self.find_in, 1)
        r1.addWidget(self.find_lbl)
        r1.addWidget(nxt)
        r1.addWidget(self.find_case)
        r1.addWidget(self.repl_toggle)
        r1.addWidget(close)
        self.repl_row = QWidget()
        r2 = QHBoxLayout(self.repl_row)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(6)
        self.repl_in = QLineEdit()
        self.repl_in.setPlaceholderText("Thay bằng…")
        self.repl_in.returnPressed.connect(self._replace_one)
        r2.addWidget(self.repl_in, 1)
        for text, slot, tip in (
            ("Thay", self._replace_one, "Thay ở câu đang chọn, rồi tới câu khớp sau"),
            ("Thay tất cả", self._replace_all,
             "Thay trong TOÀN BỘ phụ đề — hoàn tác được bằng Ctrl+Z"),
        ):
            b = button(text, small=True, tip=tip)
            b.clicked.connect(slot)
            r2.addWidget(b)
        self.repl_row.hide()
        v.addLayout(r1)
        v.addWidget(self.repl_row)
        self.find_bar = bar
        return bar

    def _toggle_find(self, on: bool | None = None, *, replace: bool = False) -> None:
        show = self.find_bar.isHidden() if on is None else on
        self.find_bar.setVisible(show)
        if show:
            if replace:
                self.repl_toggle.setChecked(True)
            self.find_in.setFocus()
            self.find_in.selectAll()
            self._update_find_count()

    def _show_replace(self, on: bool) -> None:
        self.repl_row.setVisible(on)
        if on:
            self.repl_in.setFocus()

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
        self._show_ai_finding(idx)
        self._update_listen()

    def _show_ai_finding(self, i: int) -> None:
        from app.pipeline import qa

        found = qa.findings_for(self.cues[i], self._qa) if 0 <= i < len(self.cues) else []
        self.inspector.set_ai_finding(found[0] if found else None)

    def _edit_style(self) -> None:
        """E-7: the learnt rules are plain text the user owns."""
        from PySide6.QtWidgets import QPlainTextEdit

        from app.pipeline import memory
        from desktop.widgets.dialogs import VzDialog

        dlg = VzDialog("Phong cách của tôi", self, width=520)
        dlg.body.addWidget(label("Mỗi dòng một quy tắc, áp cho mọi video khi dịch. AI tự cập "
                                 "nhật sau mỗi 5 lần bạn duyệt; bạn sửa tay được.", "small", wrap=True))
        box = QPlainTextEdit(memory.style_rules())
        box.setPlaceholderText("- Người nói xưng \"mình\", gọi người xem \"các bạn\"\n- …")
        box.setMinimumHeight(200)
        dlg.body.addWidget(box)
        dlg.add_button("Hủy")
        dlg.add_button("Lưu", icon_name="Check", primary=True, accept=True)
        if dlg.exec():
            memory.save_style_rules(box.toPlainText())
            self.inspector.set_style_rules(memory.style_rules())
            self.message.emit("Đã lưu “Phong cách của tôi” — áp cho các video dịch sau.", "ok")

    def _retranslate(self) -> None:
        """E-6: three AI renderings of the selected line; the chosen one replaces it
        (one undo step)."""
        from app.pipeline import brief as brief_mod
        from app.pipeline import translate
        from desktop.widgets.dialogs import RetranslateDialog

        i = self._sel
        if not (0 <= i < len(self.cues)):
            return
        c = self.cues[i]
        before = [(x.zh, x.vi) for x in self.cues[max(0, i - 2):i]]
        after = [(x.zh, x.vi) for x in self.cues[i + 1:i + 3]]
        brief = (brief_mod.load_brief(self.job) or "") if self.job is not None else ""
        provider = self._ctx.get("refine") or self._ctx.get("translator") or "openrouter"
        dlg = RetranslateDialog(self, index=i, zh=c.zh, vi=c.vi)

        def ask(request: str) -> None:
            self._run_async(
                lambda: translate.alternatives(c.zh, c.vi, before=before, after=after,
                                               request=request, brief=brief, provider=provider),
                lambda opts: dlg.set_options(opts) if dlg.isVisible() else None,
                lambda m: dlg.set_error(m) if dlg.isVisible() else None)

        dlg.regenerate.connect(ask)
        self.video.pause()
        ask("")
        if dlg.exec() and dlg.choice() and dlg.choice() != c.vi and 0 <= i < len(self.cues):
            self._snapshot()
            self.cues[i].vi = dlg.choice()
            self.cue_list.refresh_row(i)
            self.video.set_cues(self.cues)
            self._refresh_issues()
            self._update_cue_panel(i)
            self._mark_dirty()

    def _apply_ai_fix(self, text: str) -> None:
        i = self._sel
        if not text or not (0 <= i < len(self.cues)) or self.cues[i].vi == text:
            return
        self._snapshot()
        self.cues[i].vi = text
        self.cue_list.refresh_row(i)
        self.video.set_cues(self.cues)
        self._refresh_issues()
        self._update_cue_panel(i)          # the finding no longer matches → hidden
        self._mark_dirty()
        self.message.emit("Đã dùng gợi ý của AI — Ctrl+Z để hoàn tác.", "ok")

    # --- bulk edits ("Rút gọn hàng loạt", "Gợi ý AI") ------------------------------
    def _bulk_targets(self) -> list[tuple[int, int, str]]:
        """Lines too fast to read or too long to dub → (index, target %, note)."""
        from app.pipeline import fit_estimate, review

        out = []
        for i, c in enumerate(self.cues):
            iss = self._issues_at(i)
            if not c.vi.strip() or not ({"read", "long"} & set(iss)):
                continue
            pct = 90
            cps, _n = review.reading(c.vi, max(0.1, c.end - c.start))
            if "read" in iss and cps > 0:
                pct = min(pct, int(100 * BULK_TARGET_CPS / cps))
            if "long" in iss:
                slot = fit_estimate.slot_seconds(self.cues, i)
                need = max(0.1, fit_estimate.speech_seconds(c.vi, self._voice_rate()))
                pct = min(pct, int(100 * slot * fit_estimate.MAX_TEMPO / need))
            pct = max(50, min(90, round(pct / 10) * 10))
            note = " · ".join(review.LABELS[k] for k in iss if k in ("read", "long"))
            out.append((i, pct, f"{note} · {cps:.0f} ký tự/giây"))
        return out

    def _bulk_shorten(self) -> None:
        """AI shortens every line flagged "Đọc không kịp" / "Quá dài" in a few batched
        calls; the user ticks what to keep in one dialog; applied as ONE undo step."""
        if self._busy_bulk:
            return
        targets = self._bulk_targets()
        if not targets:
            self.message.emit("Không có câu nào đọc không kịp hay quá dài để rút gọn.", "ok")
            return
        provider = self._ctx.get("translator") or "openrouter"
        texts = [(self.cues[i].vi, pct) for i, pct, _n in targets]
        snap = [self.cues[i].vi for i, _p, _n in targets]
        self._busy_bulk = True
        self.message.emit(f"AI đang rút gọn {len(targets)} câu…", "info")

        def work():
            from app.pipeline.translate import shorten_lines
            return shorten_lines(texts, provider=provider)

        def done(result):
            from desktop.widgets.dialogs import BulkEditDialog

            self._busy_bulk = False
            rows = [(i, old, new, note) for (i, _p, note), old, new in zip(targets, snap, result)
                    if new and new != old and i < len(self.cues) and self.cues[i].vi == old]
            if not rows:
                self.message.emit("AI không rút gọn thêm được câu nào.", "warn")
                return
            dlg = BulkEditDialog(self, title=f"Rút gọn {len(rows)} câu",
                                 intro="Bản đề xuất ngắn hơn để kịp đọc và lồng tiếng. Bỏ "
                                       "chọn câu bạn muốn giữ, hoặc sửa trực tiếp đề xuất.",
                                 rows=rows, checked=True)
            if dlg.exec():
                self._apply_bulk(dlg.chosen(), "Đã rút gọn")

        def fail(msg):
            self._busy_bulk = False
            self._toast(f"Lỗi rút gọn: {msg}")

        self._run_async(work, done, fail)

    def _ai_suggestions(self) -> None:
        """Every open AI-review finding with a proposed fix, in one list (unticked: the
        reviewer can be wrong — e.g. it judges half a sentence)."""
        from app.pipeline import qa

        rows = []
        for i, c in enumerate(self.cues):
            for f in qa.findings_for(c, self._qa):
                if f.get("fix") and f["fix"].strip() != c.vi.strip():
                    kind = qa.KINDS.get(f.get("kind", ""), "AI")
                    rows.append((i, c.vi, f["fix"].strip(),
                                 f"{kind}: {f.get('note', '')}  ·  {c.zh}".strip()))
                    break
        if not rows:
            self.message.emit("Không còn gợi ý nào của AI soát.", "ok")
            return
        from desktop.widgets.dialogs import BulkEditDialog

        dlg = BulkEditDialog(self, title=f"{len(rows)} gợi ý của AI soát",
                             intro="AI có thể sai (nhất là khi câu bị cắt giữa chừng) — đọc "
                                   "câu gốc rồi chọn những gợi ý bạn đồng ý.",
                             rows=rows, checked=False,
                             mark_rest="Đánh dấu “Đã xem” các câu không chọn (mình đã đọc, giữ nguyên)")
        if dlg.exec():
            self._apply_bulk(dlg.chosen(), "Đã dùng gợi ý AI cho", reviewed=dlg.unticked())

    def _apply_bulk(self, chosen: list[tuple[int, str]], verb: str, *,
                    reviewed: list[int] = ()) -> None:
        chosen = [(i, t) for i, t in chosen if 0 <= i < len(self.cues) and self.cues[i].vi != t]
        reviewed = [i for i in reviewed if 0 <= i < len(self.cues)]
        if not chosen and not reviewed:
            return
        self._snapshot()
        for i, t in chosen:
            self.cues[i].vi = t
        for i in reviewed:
            cuemodel.mark_reviewed(self.cues[i])
        self._reload()
        tail = f" · {len(reviewed)} câu giữ nguyên đã đánh dấu Đã xem" if reviewed else ""
        self.message.emit(f"{verb} {len(chosen)} câu{tail} — Hoàn tác (Ctrl+Z) để bỏ.", "ok")

    def _split_clauses(self) -> None:
        """A line holding "A, B, punchline." shows the punchline before it is said: cut it
        into its spoken clauses (Chinese and Vietnamese together, no re-translation), each
        timed from the word track. One undo step; policy in app.pipeline.clauses (pure)."""
        import json

        from app.pipeline import clauses

        try:
            words = json.loads((self.job.work_dir / "asr_words.json").read_text(
                encoding="utf-8"))["words"]
        except (AttributeError, OSError, ValueError, KeyError, TypeError):
            words = None
        if not words:
            self.message.emit("Dự án này không có mốc thời gian từng từ (phụ đề từ OCR / tệp "
                              ".srt đã theo nhịp phụ đề gốc) — không cần tách.", "info")
            return
        new, split = [], 0
        for c in self.cues:
            parts = None if c.locked else clauses.split_pair(c.zh, c.vi, c.start, c.end, words)
            if not parts:
                new.append(c)
                continue
            split += 1
            for zh, vi, s, e in parts:
                piece = cuemodel.copy_cue(c)
                piece.zh, piece.vi, piece.start, piece.end = zh, vi, round(s, 3), round(e, 3)
                new.append(piece)
        if not split:
            self.message.emit("Không có câu nào tách vế được (câu một vế, hoặc bản dịch có "
                              "số vế khác bản gốc).", "ok")
            return
        self._snapshot()
        self.cues = new
        self._reload()
        self.message.emit(f"Đã tách {split} câu thành từng vế theo nhịp nói — Hoàn tác (Ctrl+Z) "
                          "để bỏ.", "ok")

    def _tidy_timing(self) -> None:
        """Lines too short to read (ASR fragments) join their neighbour; overlaps get a
        shared edge. One undo step; policy in cuemodel.tidy_timing (pure, tested)."""
        from app.pipeline.review import MIN_SECONDS

        new, merged, fixed = cuemodel.tidy_timing(self.cues, min_len=MIN_SECONDS)
        if not merged and not fixed:
            self.message.emit("Không có câu quá ngắn hay chồng lấn nào gộp được.", "ok")
            return
        self._snapshot()
        self.cues = new
        self._reload()
        self.message.emit(f"Đã gộp {merged} câu quá ngắn vào câu bên cạnh · gỡ {fixed} chỗ "
                          f"chồng lấn — Hoàn tác (Ctrl+Z) để bỏ.", "ok")

    def _timing_pass(self) -> None:
        """Netflix-style timing tidy-up on every unlocked line (2-frame gaps, short gaps
        chained, lines at least 20 frames). One undo step; policy in pipeline.timing_pass."""
        from app.pipeline import timing_pass

        if not self.cues:
            return
        src = self.job.source_video if self.job is not None else None
        fps = timing_pass.probe_fps(src) if src is not None and src.exists() else None
        times, rep = timing_pass.plan(self.cues, fps, duration=self._duration())
        if not rep.changed:
            self.message.emit("Thời gian các câu đã đúng chuẩn — không đổi gì.", "ok")
            return
        self._snapshot()
        for k in rep.changed:
            self.cues[k].start, self.cues[k].end = times[k]
        self._reload()
        extra = f" · bỏ qua {len(rep.locked)} chỗ có câu khóa" if rep.locked else ""
        self.message.emit(f"Đã chỉnh thời gian: {rep.summary()}{extra} — Ctrl+Z để hoàn tác.", "ok")

    def _vi_lint(self) -> None:
        """"Soát lỗi tiếng Việt": safe typography fixes on every line (one undo step) and the
        lint flags refreshed. Policy in pipeline.vi_lint (pure, tested)."""
        from app.pipeline import vi_lint

        if not self.cues:
            return
        fixes = [vi_lint.fix_text(c.vi) for c in self.cues]
        counts: dict[str, int] = {}
        for _t, fired in fixes:
            for k in fired:
                counts[k] = counts.get(k, 0) + 1
        if counts:
            self._snapshot()
            for c, (text, fired) in zip(self.cues, fixes):
                if fired:
                    c.vi = text
            self._reload()
        else:
            self._refresh_issues()
        flagged = sum(1 for iss in self.issues if {"han", "num", "pron", "latin"} & set(iss))
        msg = (f"Đã sửa {sum(counts.values())} chỗ: {vi_lint.describe(counts)} — Ctrl+Z để hoàn tác."
               if counts else "Không có lỗi trình bày để sửa.")
        if flagged:
            msg += f" {flagged} câu cần xem tay (chữ Trung, lệch số, từ chưa dịch, xưng hô)."
        self.message.emit(msg, "ok")

    def _rate_str(self) -> str:
        """The voice speed as the TTS/render options spell it ("+30%")."""
        return f"{int(round((self._voice_rate() - 1) * 100)):+d}%"

    def _voice_rate(self) -> float:
        """The dub speed picked on the render page for this project (voice.json)."""
        from desktop.views.render_view import saved_rate

        return (saved_rate(self.job) if self.job is not None else None) or 1.0

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
        if 0 <= self._sel < len(self.cues):                  # F-6: speed follows the text
            c = self.cues[self._sel]
            self.inspector.set_reading(c.vi, c.end - c.start)

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

    def _on_voice(self, _i: int) -> None:
        """Per-line voice tag ("f" / "m" / "" = the picked voice) — one undo step."""
        if 0 <= self._sel < len(self.cues):
            tag = self.inspector.voice_combo.currentData() or ""
            if self.cues[self._sel].voice != tag:
                self._snapshot()
                self.cues[self._sel].voice = tag
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
            self._update_undo_buttons()
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
    def _compute_issues(self) -> list[list[str]]:
        ocr, term = self._spans
        lint = None
        if self.job is not None:
            try:
                from app.pipeline import vi_lint

                lint = vi_lint.context(self.job)       # glossary + series ban, cached by stamps
            except Exception:  # noqa: BLE001 - the lint flags are hints
                lint = None
        return review.cue_issues(self.cues, ocr_spans=ocr, term_spans=term,
                                 guess_spans=self._guess, voice=self._speech,
                                 qa_findings=self._qa, late=getattr(self, "_late", []),
                                 rate=self._voice_rate(), lint=lint)

    def reload_fit_report(self) -> None:
        """F-10: after a render (or a quick preview), flag lines whose dub came in late."""
        if self.job is None:
            return
        self._late = review.load_late(self.job, rate=self._rate_str())
        if self.cues:
            self._refresh_issues()

    def _set_speech(self, spans: list | None, why: str = "") -> None:
        had = self._speech
        self._speech = spans or None
        self.timeline.set_speech(spans or [])
        if self._speech != had and self.cues:
            self._refresh_issues()              # "Không nghe thấy giọng" needs the voice
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

        self._run_async(lambda: vad.voice_spans(job, log=notes.append), done,
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
        hint = self._no_voice_hint(len(rep.no_voice))
        if not rep.changed:
            self.message.emit("Mọi câu đã khớp giọng nói (hoặc không thấy giọng) — không đổi gì."
                              + hint, "ok")
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
        self.message.emit(" · ".join(parts) + " — Ctrl+Z để hoàn tác." + hint, "ok")

    def _no_voice_hint(self, n: int) -> str:
        """Several lines without voice and no word track yet: usually singing under loud
        music, which local VAD can't hear — the word track (Đo lại thời gian) can."""
        from app.pipeline import vad

        if n < 3 or self.job is None or vad.saved_words(self.job):
            return ""
        return (" Nhạc nền to che giọng hát? ··· → “Đo lại thời gian từ âm thanh” để AI nghe "
                "kỹ từng chữ, rồi Tự canh lại.")

    def _retime(self) -> None:
        """E-1: re-time the Chinese lines against the audio (text kept), then move the
        Vietnamese lines when they still pair 1:1. One undo step."""
        from PySide6.QtWidgets import QMessageBox

        from app.pipeline import asr

        job = self.job
        if job is None or not job.cn_srt.exists():
            return
        saved = (job.work_dir / "asr_words.json").exists()
        if QMessageBox.question(
                self, "Đo lại thời gian",
                "Đo lại mốc thời gian của mọi câu từ âm thanh, giữ nguyên chữ và bản dịch.\n"
                + ("Dùng mốc từng từ đã lưu — không gửi gì lên mạng." if saved else
                   "Cần gửi âm thanh lên máy chủ để lấy mốc từng từ (một lần, vài xu).")
                + "\n\nHoàn tác được bằng Ctrl+Z.") != QMessageBox.StandardButton.Yes:
            return
        self.flush()
        self.message.emit("Đang đo lại thời gian từ âm thanh…", "busy")

        def done(_moved):
            if self.job is not job:
                return
            times = asr.vi_time_map(job, [c.zh for c in self.cues],
                                    [(c.start, c.end) for c in self.cues])
            from app.pipeline import review

            self._guess = review.load_guess_spans(job)
            if times is None:
                self._refresh_issues()
                self.message.emit("Đã đo lại tiếng Trung, nhưng bạn đã tách / gộp câu nên không "
                                  "áp được thời gian mới cho tiếng Việt.", "warn")
                return
            self._snapshot()
            for c, (s, e) in zip(self.cues, times):
                c.start, c.end = s, e
            self._reload()
            self.message.emit("Đã đo lại thời gian — câu “Timing đoán” cần xem lại. "
                              "Ctrl+Z để hoàn tác.", "ok")

        self._run_async(lambda: asr.retime(job), done,
                        lambda m: self.message.emit(f"Không đo lại được: {m}", "err"))

    def _fit_selection(self) -> None:
        if 0 <= self._sel < len(self.cues):
            c = self.cues[self._sel]
            self.timeline_panel.fit_span(c.start, c.end)

    # --- review ---------------------------------------------------------------
    def _refresh_issues(self) -> None:
        self.issues = self._compute_issues()
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
        self.timeline.marks_changed()
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

    def show_pending(self) -> None:
        """"Xem N câu" in the approve dialog: only the lines still to look at, the first
        one selected, the keyboard on the list (D / F8 / ↑↓ carry on from there)."""
        self.cue_list.set_review_only(True)
        rows = self.cue_list.pending_rows()
        if rows:
            self.cue_list.selectRow(rows[0])
        self.cue_list.view.setFocus()

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
        """Closing the window: results of calls still running (AI, dub preview) are dropped.
        The calls run on daemon threads, so nothing is joined and the exit is immediate."""
        self._closing_tasks = True

    def _run_async(self, fn, on_done, on_fail=None) -> None:
        task = _Task(fn)
        alive = lambda: not getattr(self, "_closing_tasks", False)   # noqa: E731
        connect_on_ui(task.done, guarded(on_done, alive), self)     # closures touch widgets → GUI thread
        if on_fail:
            connect_on_ui(task.failed, guarded(on_fail, alive), self)
        spawn(task.run, self._tasks, task)

    # --- load / save ---------------------------------------------------------
    def load(self, job) -> None:
        self.flush()   # job switch: save the OLD job's pending edit before replacing it
        self.job = job
        self.cues = cuemodel.load_pair(job)
        try:
            self._spans = review.load_flag_spans(job)
            self._guess = review.load_guess_spans(job)
            from app.pipeline import qa

            self._qa = qa.load(job)
            self._late = review.load_late(job, rate=self._rate_str())
        except Exception:  # noqa: BLE001 - flags are hints; never block opening a job
            self._spans, self._guess, self._qa, self._late = ([], []), [], [], []
        self._meta = {"title": _job_title(job), "platform": job.metadata.get("platform") or
                      _job_field(job, "platform") or "", "dur": 0.0, "dims": (0, 0)}
        self._update_project()
        self._aspect = 0.0               # the new video reports its size when it loads
        self.video.load(job.source_video)
        self.timeline.set_peaks([])
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._sel = -1
        self._reload(dirty=False)
        from app.pipeline import approval

        st = self._load_style(job)
        if st:
            self.style_panel.set_from(st)
        elif (_has_hardsubs(job) and self.style_panel.box_style.currentText() != COVER_STYLE
              and not approval.is_approved(job)):
            # burned-in Chinese under an outline-only line shows through → cover it. Never
            # on an approved project: the saved style.json would revoke the approval just
            # by opening it (seen 2026-10-29 on an approved, rendered episode)
            self.style_panel.box_style.setCurrentText(COVER_STYLE)
            QTimer.singleShot(0, self, lambda: self.message.emit(   # dropped with the view
                "Video có chữ Trung in sẵn — đã chọn nền “Hộp đặc” để che. Đổi ở tab Kiểu chữ.",
                "info"))
        self._set_global_from_panel()
        self.video.set_style(self._g_style, self._g_size, self._g_font)
        self._update_scope_hint()
        self.inspector.set_context(*_brief_and_note(job))
        from app.pipeline import memory

        self.inspector.set_style_rules(memory.style_rules())
        if self.cues:
            self.cue_list.selectRow(0)
        self._load_waveform(job)
        self._load_speech(job)
        self._load_commentary(job)
        self._load_music(job)

    # --- background music (music.json) -------------------------------------------------
    def _load_music(self, job) -> None:
        from app.pipeline import music

        clips = music.load(job)
        self.music.set_clips(clips, self._duration() or 0.0)
        self.timeline.set_music([(c.start, c.end) for c in clips])
        self._music_warning()

    def _save_music(self) -> None:
        """Saved at once. Not part of the approved revision (approval.py): the render
        reads the current music.json, so no re-approval is needed."""
        from app.pipeline import music

        if self.job is None:
            return
        clips = self.music.clips()
        music.save(self.job, clips)
        self.timeline.set_music([(c.start, c.end) for c in clips])
        self._music_warning()

    def _music_warning(self) -> None:
        from app.pipeline import music

        c = self.music.selected()
        probs = music.validate(self.job, [c]) if c is not None and self.job is not None else []
        self.music.set_warning(probs[0].split(": ", 1)[-1] if probs else "")

    def _pick_music_file(self) -> Path | None:
        from PySide6.QtWidgets import QFileDialog

        from app.pipeline import music

        pats = " ".join(f"*{e}" for e in music.EXTS)
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn nhạc nền", "", f"Âm thanh ({pats})")
        return Path(fn) if fn else None

    def _add_music(self, kind: str) -> None:
        """"all": the whole video (loops); "here": from the playhead to the end."""
        from app.pipeline import music

        if self.job is None:
            return
        self.video.pause()
        src = self._pick_music_file()
        if src is None:
            return
        try:
            rel = music.import_file(self.job, src)
        except OSError as e:
            self._toast(f"Không dùng được file nhạc: {e}")
            return
        start = 0.0 if kind == "all" else round(self.video.current_time(), 2)
        self.inspector.set_tab(self.music_tab)
        self.music.add(music.Clip(file=rel, start=start, end=None, name=src.name))

    def _replace_music_file(self) -> None:
        from app.pipeline import music

        if self.job is None or self.music.selected() is None:
            return
        src = self._pick_music_file()
        if src is None:
            return
        try:
            rel = music.import_file(self.job, src)
        except OSError as e:
            self._toast(f"Không dùng được file nhạc: {e}")
            return
        self.music.update_selected(file=rel, name=src.name)

    def _preview_music(self) -> None:
        c = self.music.selected()
        if c is None or self.job is None or self._dub_player is None:
            return
        from PySide6.QtCore import QUrl

        self.video.pause()
        self._dub_audio.setVolume(min(1.0, c.volume))
        self._dub_player.setSource(QUrl.fromLocalFile(str(self.job.work_dir / c.file)))
        self._dub_player.play()

    # --- F-4: the user's own commentary ------------------------------------------------
    def _load_commentary(self, job) -> None:
        from app.pipeline import commentary

        notes = commentary.load(job)
        self.commentary.set_notes(notes, self._duration() or 0.0)
        self.timeline.set_notes([(n.at, n.mode) for n in notes])
        tts_name, dub_voice = self._ctx.get("tts", "edge"), self._ctx.get("voice")

        def voices():
            from app.providers.registry import get_tts

            return list(get_tts(tts_name).voices() or [])

        self._run_async(voices, lambda vs: self.job is job and
                        self.commentary.set_voices(vs, dub_voice), lambda _m: None)
        self._note_warning()

    def _save_commentary(self) -> None:
        """Saved at once (not on the autosave timer): it is a separate file, and it
        counts towards the approved revision, so the render page re-locks on its own."""
        from app.pipeline import commentary

        if self.job is None:
            return
        notes = self.commentary.notes()
        commentary.save(self.job, notes)
        self.timeline.set_notes([(n.at, n.mode) for n in notes])
        self._note_warning()

    def _note_time_here(self) -> float:
        """The playhead — moved to the end of the sentence being spoken, so a pause never
        cuts a line in half."""
        t = self.video.current_time()
        for c in self.cues:
            if c.start < t < c.end:
                return c.end
        return t

    def _add_note(self, kind: str) -> None:
        from app.pipeline import commentary

        if self.job is None:
            return
        self.video.pause()
        dur = self._duration() or 0.0
        at = {"intro": 0.0, "outro": dur}.get(kind)
        if at is None:
            at = self._note_time_here()
        self.inspector.set_tab(self.commentary_tab)
        self.commentary.add(commentary.Note(at=round(at, 2), text="", mode="pause"))

    def _note_to_playhead(self) -> None:
        self.commentary.update_selected(at=round(self._note_time_here(), 2))

    def _note_warning(self) -> None:
        """A note talking over a line, or freezing the picture in the middle of one."""
        n = self.commentary.selected()
        msg = ""
        if n is not None:
            end = n.at + max(1.0, n.est_seconds())
            for i, c in enumerate(self.cues, 1):
                if n.mode == "over" and c.start < end and c.end > n.at:
                    msg = (f"Nói đè lên câu {i} ({fmt_time(c.start)}–{fmt_time(c.end)}) — chọn "
                           "Dừng hình, hoặc dời sang chỗ không có lời thoại.")
                    break
                if n.mode == "pause" and c.start + 0.05 < n.at < c.end - 0.05:
                    msg = (f"Video sẽ dừng giữa câu {i} — bấm “Đặt = vị trí phát” để đưa về "
                           "cuối câu.")
                    break
        self.commentary.set_warning(msg)

    def _preview_note(self) -> None:
        from app.pipeline import commentary

        n = self.commentary.selected()
        if n is None or self.job is None or self._dub_player is None:
            return
        job, tts_name = self.job, self._ctx.get("tts", "edge")
        dub_voice, rate = self._ctx.get("voice"), self._ctx.get("rate")
        if n.audio:
            from PySide6.QtCore import QUrl

            self._dub_audio.setVolume(1.0)
            self._dub_player.setSource(QUrl.fromLocalFile(str(job.work_dir / n.audio)))
            self._dub_player.play()
            return
        if not n.text.strip():
            return
        note = commentary.Note(at=n.at, text=n.text, mode=n.mode, voice=n.voice)
        self.commentary.listen_btn.setEnabled(False)

        def work():
            voiced = commentary.synthesize(job, [note], provider=tts_name, dub_voice=dub_voice,
                                           rate=rate)
            return str(voiced[0].path) if voiced else ""

        def done(path):
            from PySide6.QtCore import QUrl

            self.commentary.listen_btn.setEnabled(True)
            if path:
                self._dub_audio.setVolume(1.0)
                self._dub_player.setSource(QUrl.fromLocalFile(path))
                self._dub_player.play()

        def fail(msg):
            self.commentary.listen_btn.setEnabled(True)
            self._toast(f"Lỗi nghe thử bình luận: {msg}")

        self._run_async(work, done, fail)

    def _import_note_audio(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from app.pipeline import commentary

        if self.job is None or self.commentary.selected() is None:
            return
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn file ghi âm bình luận", "",
                                            "Âm thanh (*.wav *.mp3 *.m4a *.aac *.ogg *.flac)")
        if not fn:
            return
        try:
            rel = commentary.import_audio(self.job, Path(fn))
        except OSError as e:
            self._toast(f"Không dùng được file: {e}")
            return
        self.commentary.update_selected(audio=rel)

    def _load_waveform(self, job) -> None:
        from app.pipeline import waveform

        def done(peaks):
            if self.job is job:
                self.timeline.set_peaks(peaks, waveform.PER_SEC)

        self._run_async(lambda: waveform.peaks(job), done, lambda _m: None)

    def _reload(self, *, dirty: bool = True) -> None:
        self._update_undo_buttons()       # also catches a popped no-op snapshot
        self.issues = self._compute_issues()
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
        self.commentary.set_duration(d)
        self.music.set_duration(d)
        self.timeline.set_duration(d)
        self._update_project()

    def _on_dims(self, w: int, h: int) -> None:
        self._meta["dims"] = (w, h)
        self._update_project()
        self._aspect = w / h if w > 0 and h > 0 else 0.0
        if not self._user_split:
            self._fit_split()           # a vertical video gives its spare width to the list

    def _update_project(self) -> None:
        m = self._meta
        parts = [{"douyin": "Douyin", "bilibili": "Bilibili", "local": "Tệp trên máy"}
                 .get(m["platform"], m["platform"] or "")]
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
        self._update_undo_buttons()

    def _update_undo_buttons(self) -> None:
        if hasattr(self, "undo_btn"):
            self.undo_btn.setEnabled(bool(self._undo_stack))
            self.redo_btn.setEnabled(bool(self._redo_stack))

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
                self._update_undo_buttons()
                self._set_global_from_panel()
                self.video.set_style(self._g_style, self._g_size, self._g_font)
            else:
                self._sync_global_glass()
        else:
            self._set_global_from_panel()                # update the global (Default) style
            self.video.set_style(self._g_style, self._g_size, self._g_font)
        self._refresh_lines_badge()
        self._mark_dirty()

    def _sync_global_glass(self) -> None:
        """The frosted glass is one blur band for the whole video, so its two sliders
        always set the global look — even while "Áp dụng cho" targets some lines."""
        s = self.style_panel.get_style()
        g = self._g_style
        if not (g and g.get("glass") and s.get("glass")):
            return
        new = {**g, "glass_tint": s["glass_tint"], "glass_blur": s["glass_blur"]}
        if new != g:
            self._g_style = new
            self.video.set_style(self._g_style, self._g_size, self._g_font)

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
        atomic_write_text((self.job.work_dir / "style.json"),
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

    # --- CSV for proof-reading (app/pipeline/subcsv.py) ---------------------------------
    def export_csv_to(self, path) -> Path:
        """index,start,end,zh,vi — UTF-8 with a BOM, so Excel shows Vietnamese correctly."""
        from app.pipeline import subcsv

        self.flush()
        return subcsv.write_file(path, self.cues)

    def _export_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = store.load_ui_state().get("export_dir") or (
            str(self.job.work_dir) if self.job is not None else "")
        name = "".join(ch if ch not in '\\/:*?"<>|' else " " for ch in (
            self._meta.get("title") or "phu-de")).strip()[:60] or "phu-de"
        fn, _ = QFileDialog.getSaveFileName(self, "Xuất bảng phụ đề (CSV)",
                                            str(Path(folder) / f"{name}.csv"), "CSV (*.csv)")
        if not fn:
            return
        try:
            out = self.export_csv_to(fn)
        except OSError as exc:
            self.message.emit(f"Chưa xuất được CSV: {exc}", "warn")
            return
        self.message.emit(f"Đã xuất {len(self.cues)} câu ra {out.name} — mở bằng Google Sheets "
                          "hoặc Excel, sửa cột vi rồi nhập lại.", "ok")

    def plan_csv_import(self, text: str, *, with_times: bool = False):
        """(rows in cue order, rows whose text changes, rows whose times change) for a CSV of
        THIS project; subcsv.CsvError says why when it is not one."""
        from app.pipeline import subcsv

        ordered = subcsv.match(self.cues, subcsv.read_rows(text))
        text_rows, time_rows = subcsv.changes(self.cues, ordered, with_times=with_times)
        return ordered, text_rows, time_rows

    def apply_csv_import(self, ordered, text_rows, time_rows, *, with_times: bool) -> int:
        """One undo step: the Vietnamese of ``text_rows`` (and, on request, the times of
        ``time_rows``) from the file. Returns how many cues changed."""
        touched = set(text_rows) | (set(time_rows) if with_times else set())
        if not touched:
            return 0
        self._snapshot()
        for i in text_rows:
            self.cues[i].vi = ordered[i]["vi"]
        if with_times:
            from app.pipeline import subcsv

            for i in time_rows:
                self.cues[i].start = round(subcsv.parse_ts(ordered[i]["start"]), 3)
                self.cues[i].end = round(subcsv.parse_ts(ordered[i]["end"]), 3)
        self._reload()
        return len(touched)

    def _import_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from app.pipeline import subcsv
        from desktop.widgets.dialogs import CsvImportDialog

        fn, _ = QFileDialog.getOpenFileName(self, "Nhập bảng phụ đề đã sửa (CSV)", "",
                                            "CSV (*.csv *.txt)")
        if not fn:
            return
        try:
            ordered, texts, _t = self.plan_csv_import(subcsv.read_text(fn))
        except subcsv.CsvError as exc:
            QMessageBox.warning(self, "Không nhập được file CSV", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "Không đọc được file", str(exc))
            return
        times, time_problem = [], ""
        try:                                   # the times only matter when the box is ticked
            times = subcsv.changes(self.cues, ordered, with_times=True)[1]
        except subcsv.CsvError as exc:
            time_problem = str(exc)
        dlg = CsvImportDialog(self, texts=len(texts), times=len(times), total=len(self.cues),
                              time_problem=time_problem)
        if not dlg.exec():
            return
        n = self.apply_csv_import(ordered, texts, times, with_times=dlg.with_times.isChecked())
        self.message.emit(f"Đã nhập {n} câu từ CSV — Ctrl+Z để hoàn tác.", "ok")

    # --- right-click menu (list row / timeline block) --------------------------------
    def _cue_menu(self, row: int):
        """The menu for line ``row``: only existing actions, each acting on the current
        line (the row was selected by the click). Destructive ones snapshot first, so
        Ctrl+Z brings them back."""
        from PySide6.QtWidgets import QMenu

        c = self.cues[row]
        m = QMenu(self)
        m.addAction("Tách tại vị trí phát	S", self._split)
        merge = m.addAction("Gộp với câu sau	M", self._merge)
        merge.setEnabled(row < len(self.cues) - 1)
        m.addAction("Thêm câu sau câu này", self._add)
        m.addSeparator()
        m.addAction("Dịch lại (3 phương án)	Alt+R", self._retranslate)
        m.addAction("Rút gọn cho vừa thời lượng", self._shorten)
        m.addAction("Tự canh theo giọng nói	A", self._auto_snap_selected)
        lock = m.addAction("Khóa timing")
        lock.setCheckable(True)
        lock.setChecked(c.locked)
        lock.triggered.connect(self._on_lock)
        m.addSeparator()
        seen = m.addAction("Đã xem, sang câu cần xem lại tiếp	D", self._mark_reviewed)
        seen.setEnabled(bool(self.cue_list.model.issues_at(row)) and not cuemodel.is_reviewed(c))
        m.addSeparator()
        m.addAction("Xóa câu này (Ctrl+Z hoàn tác)", self._delete)
        return m

    def _show_cue_menu(self, row: int, global_pos) -> None:
        if 0 <= row < len(self.cues):
            m = self._cue_menu(row)
            m.setAttribute(Qt.WA_DeleteOnClose)
            m.popup(global_pos)

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

def _has_hardsubs(job) -> bool:
    import json

    try:
        md = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(md.get("has_hardsubs"))


def _job_field(job, key: str):
    import json

    try:
        return json.loads((job.work_dir / "job.json").read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError):
        return None


def _job_title(job) -> str:
    """Douyin titles are the whole post caption — keep the part before the hashtags."""
    raw = (job.metadata.get("title") or _job_field(job, "title") or "").strip()
    if not raw or raw == job.id:                  # job.json stores the id as a stand-in
        src = (_job_field(job, "opts") or {}).get("file")   # a local file: its name
        raw = Path(src).stem if src else job.id
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
