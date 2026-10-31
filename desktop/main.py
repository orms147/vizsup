"""vizsup desktop app entry. Run:  python -m desktop.main  (from repo root).

MainWindow = custom toolbar (logo + step indicator + Settings + Approve&Render)
over a QStackedWidget of the 3 views, plus a status bar. The pipeline runs in a
QThread via PipelineWorker; the UI stays responsive and cancellable.
"""
from __future__ import annotations

import sys
import uuid

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from desktop import paths  # noqa: F401
from desktop import store
from desktop.cuemodel import fmt_time
from desktop.ui_thread import BgRunner, connect_on_ui
from desktop.settings_dialog import SettingsView
from desktop.theme import load_fonts, qss
from desktop.views.editor_view import EditorView
from desktop.views.input_view import InputView
from desktop.views.library_view import LibraryView
from desktop.views.render_view import RenderView
from desktop.widgets import pointer
from desktop.widgets.chrome import AppNav, StatusBar, Toast
from desktop.worker import PipelineWorker

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.providers.registry import get_tts, list_providers  # noqa: E402

INPUT, EDITOR, RENDER, SETTINGS, LIBRARY = 0, 1, 2, 3, 4
# what the status bar says when a page has nothing running; the editor's line used to stay
# on every other page (Dựng video, Cài đặt, Thư viện, Bắt đầu)
EDITOR_IDLE = "Sửa phụ đề — duyệt khi xong"
PAGE_IDLE = {INPUT: "Sẵn sàng", EDITOR: EDITOR_IDLE, RENDER: "Chọn âm thanh rồi dựng video",
             SETTINGS: "Cài đặt", LIBRARY: "Thư viện — mọi dự án trên máy"}


def _first_size(want: tuple[int, int] = (1380, 880)) -> tuple[int, int]:
    """The window's opening size: ``want``, clamped to the primary screen's usable area (a
    1366×768 laptop used to open it partly off the screen)."""
    scr = QApplication.primaryScreen()
    if scr is None:
        return want
    g = scr.availableGeometry()
    return min(want[0], max(900, g.width() - 16)), min(want[1], max(560, g.height() - 40))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("vizsup")
        self.resize(*_first_size())      # 1380 × 880, but never bigger than the screen it opens on
        self.providers = list_providers()
        self.job: Job | None = None
        self.opts: dict = {}
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        # a second slot, only for renders/joins while the QUEUE holds the main one — a
        # queued run has its own job, so part 1 can be rendered while part 2 transcribes
        self._rthread: QThread | None = None
        self._rworker: PipelineWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self._build_chrome()
        v.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.input = InputView(self.providers)
        self.editor = EditorView()
        self.render = RenderView()
        self.stack.addWidget(_scrolled(self.input))
        self.stack.addWidget(_scrolled(self.editor))
        self.stack.addWidget(_scrolled(self.render))
        self.settings_view = SettingsView()
        self.stack.addWidget(_scrolled(self.settings_view))
        self._before_settings = INPUT
        self.library = LibraryView()
        self.stack.addWidget(_scrolled(self.library))
        self._before_library = INPUT
        self._join_gid: str | None = None    # the split video a join is running for
        v.addWidget(self.stack, 1)
        v.addWidget(self.status)

        # wiring
        self.input.startRequested.connect(self._start_gate)
        self.input.cancelRequested.connect(self._cancel)
        self.input.resumeRequested.connect(self._resume)
        self.input.deleteRequested.connect(self._delete_job)
        self.input.groupResumeRequested.connect(self._resume_group)
        self.input.groupJoinRequested.connect(self._start_join)
        self.input.groupDeleteRequested.connect(self._delete_group)
        self.editor.dirtyChanged.connect(self._on_dirty)
        self.editor.saveFailed.connect(self._on_save_failed)
        self.editor.message.connect(self.status.set_status)
        self.editor.approveRequested.connect(self._approve)
        self.editor.shortcutsRequested.connect(self._show_shortcuts)
        self.render.renderRequested.connect(self._start_render)
        self.render.cancelRequested.connect(self._cancel_render)
        self.render.backRequested.connect(self._back_to_editor)
        self.render.showLinesRequested.connect(self._show_lines)
        self.render.approveRequested.connect(self._approve)
        self.render.joinRequested.connect(self._start_join)
        self.editor.previewStyleRequested.connect(self._preview_style)
        self.editor.glossaryRequested.connect(lambda: self._open_glossary(job=True))
        self.input.glossaryRequested.connect(lambda: self._open_glossary(job=False))
        self.input.settingsRequested.connect(self._open_settings)
        self.input.queueRequested.connect(self._start_queue)
        self._queue: list[dict] = []     # E-9: opts waiting to run to the gate
        self._queue_total = 0
        self._queue_ok = self._queue_failed = 0   # outcomes of this queue (for its summary)
        self._queue_cancelled = False
        self._queue_job: Job | None = None   # the queued job running now (not self.job)
        self.settings_view.saved.connect(self._on_settings_saved)
        self.settings_view.closed.connect(
            lambda: self.stack.setCurrentIndex(self._before_settings))
        # Thư viện: every project — open / watch / move to the Recycle Bin
        self.input.libraryRequested.connect(self._open_library)
        self.nav.libraryClicked.connect(self._open_library)
        self.library.closed.connect(lambda: self.stack.setCurrentIndex(self._before_library))
        self.library.openRequested.connect(self._resume)
        self.library.groupResumeRequested.connect(self._resume_group)
        self.library.changed.connect(self.input.refresh_recent)
        self.library.message.connect(self.status.set_status)
        self.library.set_guard(self._library_guard)
        self.library.compileRequested.connect(self._start_compile)
        self.library.compileCancelRequested.connect(self._cancel_compile)
        self._compile_ids: set[str] = set()   # sources + output of the running "Ghép tập"
        self._compile_worker = None
        # Dựng hàng loạt: approved projects rendered one by one in the SECOND slot, so the
        # editor stays usable for the next episode meanwhile
        self.library.batchRequested.connect(self._start_batch)
        self.library.fastApproveRequested.connect(self._fast_approve)
        self.library.batchCancelRequested.connect(self._cancel_batch)
        self.library.batch_queued = self._batch_ids
        self.render.renderLaterRequested.connect(self._render_later)
        self._batch: list[dict] = []          # {"id", "opts", "title"} waiting
        self._batch_job: Job | None = None    # rendering now
        self._batch_worker = None
        self._batch_total = 0
        self._batch_ok: list[str] = []
        self._batch_failed: list[str] = []
        self._batch_cancelled = False
        self._batch_last: tuple[float, str] = (0.0, "")
        self._main_method: str | None = None  # what the main slot runs (run_render, …)
        # desktop/session.py: the runs in flight, on disk, so a crash / power cut can resume
        self._gate_job: Job | None = None     # processed to the edit gate now (not queued)
        self._queue_opts: dict | None = None  # opts of the queued job running now
        self._render_item: dict | None = None  # a render on the render page (not batch)
        self._batch_item: dict | None = None
        self._closing = False
        self.steps.clicked.connect(self._goto_step)
        self.stack.currentChanged.connect(self._on_page)
        self._on_page(INPUT)
        self._tray = self._make_tray()
        QTimer.singleShot(1500, self._offer_recovery)
        self._bg = BgRunner(self)
        self._posting = False                  # autopilot: one post upload at a time
        self._post_timer = QTimer(self)
        self._post_timer.timeout.connect(self._post_due)
        self._post_timer.start(60_000)         # posting slots are minutes apart
        self._watching = False                 # autopilot: one creator check at a time
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watch_due)
        self._watch_timer.start(10 * 60_000)   # each series has its own interval (6 h)
        QTimer.singleShot(2 * 60_000, self._watch_due)
        QTimer.singleShot(8000, self._check_catalog)   # newer models, once a day
        self._app_shortcuts()
        self._toast = Toast(self)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)

    # --- E-12 notifications / E-10 clipboard --------------------------------------
    def _make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        from desktop.icons import icon
        from desktop.theme import V2

        tray = QSystemTrayIcon(icon("Captions", V2["purple_bright"], 32), self)
        tray.setToolTip("vizsup")
        tray.messageClicked.connect(self._bring_to_front)
        tray.activated.connect(lambda _r: self._bring_to_front())
        tray.show()
        return tray

    def _bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _check_catalog(self) -> None:
        """Once a day, quietly: the recommended model list (app/catalog.py). Says
        something only when a model changed or a newer vizsup exists."""
        from app import catalog

        if self._closing or not catalog.due():
            return
        self._bg.run(catalog.refresh, self._catalog_checked)

    def _catalog_checked(self, res) -> None:
        if not isinstance(res, dict) or not res.get("ok") or self._closing:
            return
        bits = []
        if res["changes"]:
            bits.append(f"Đã cập nhật {len(res['changes'])} model khuyên dùng (Cài đặt → AI và "
                        f"mô hình).")
        if res["update"]:
            bits.append(f"Có vizsup {res['latest']} mới: {res['url']}")
        if bits:
            self._notify("Cập nhật", " ".join(bits))

    def _notify(self, title: str, msg: str, kind: str = "ok") -> None:
        """A Windows notification when the user is looking elsewhere, an in-app toast
        when vizsup is in front (or the tray is unavailable)."""
        if self._tray is not None and not self.isActiveWindow():
            ic = (QSystemTrayIcon.MessageIcon.Warning if kind == "err"
                  else QSystemTrayIcon.MessageIcon.Information)
            self._tray.showMessage(title, msg, ic, 8000)
        else:
            self._toast.show_message(title, msg, kind)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if getattr(self, "_toast", None) is not None:
            self._toast.reposition()

    def _on_app_state(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive or self._busy():
            return
        if self.stack.currentIndex() != INPUT:
            return
        clip = QApplication.clipboard()
        if clip is not None:
            self.input.offer_clipboard(clip.text() or "", known=store.known_urls())

    # --- chrome (v2: appdesign/v2, desktop/widgets/chrome.py) ---------------
    def _build_chrome(self) -> None:
        self.nav = AppNav()
        self.steps = self.nav.steps                 # v1 name kept for the call sites
        self.nav.settingsClicked.connect(self._open_settings)
        self.nav.primaryClicked.connect(self._approve)
        self.nav.keysClicked.connect(self._show_shortcuts)
        self.status = StatusBar()                   # .setText("● …") still works

    def _app_shortcuts(self) -> None:
        """Whole-app keys (also listed in the Phím tắt dialog): Ctrl+L library, Ctrl+, settings,
        Ctrl+1/2/3 the three steps, Esc leaves Thư viện / Cài đặt."""
        from PySide6.QtGui import QKeySequence, QShortcut

        def sc(seq: str, fn) -> None:
            QShortcut(QKeySequence(seq), self, activated=fn)

        sc("Ctrl+L", self._open_library)
        sc("Ctrl+,", self._open_settings)
        sc("Ctrl+1", lambda: self._goto_step(INPUT))
        sc("Ctrl+2", lambda: self._goto_step(EDITOR))
        sc("Ctrl+3", lambda: self._goto_step(RENDER))
        # Esc only while Thư viện / Cài đặt is showing (_on_page): a window-wide Esc would
        # collide with the editor's own (find bar, full screen)
        self._esc_back = QShortcut(QKeySequence("Esc"), self, activated=self._leave_overlay_page)
        self._esc_back.setEnabled(False)

    def _leave_overlay_page(self) -> None:
        idx = self.stack.currentIndex()
        if idx == LIBRARY:
            self.library.closed.emit()
        elif idx == SETTINGS:
            self.settings_view.request_close()      # asks first when there are unsaved changes

    def _show_shortcuts(self) -> None:
        from desktop.widgets.shortcuts import ShortcutsDialog

        ShortcutsDialog(self).exec()

    def _on_page(self, idx: int) -> None:
        if idx in (SETTINGS, LIBRARY):
            self.steps.set_active(getattr(self, "_rail_idx", INPUT), dimmed=True)
        else:
            self._rail_idx = idx
            self.steps.set_active(idx)
        self.nav.library_btn.setChecked(idx == LIBRARY)
        self.nav.settings_btn.setChecked(idx == SETTINGS)
        if getattr(self, "_esc_back", None) is not None:
            self._esc_back.setEnabled(idx in (SETTINGS, LIBRARY))
        shown = self.status.text.text()
        if shown in PAGE_IDLE.values() and shown != PAGE_IDLE[idx]:
            self.status.set_status(PAGE_IDLE[idx])     # never over a message or a progress line
        self.nav.set_primary("Duyệt và dựng" if idx == EDITOR else None)
        self.nav.set_saved("saved" if idx == EDITOR else None)
        self.status.set_hints([("Space", "Phát / dừng"), ("[ ]", "Canh lời"), ("F8", "Xem lại")]
                              if idx == EDITOR else [])
        if idx != EDITOR:
            self.editor.video.pause()
        if idx != RENDER:
            self.render.preview.pause()
            self.render.result.pause()
        elif self.job is not None:
            self._sync_render_page()   # an edit after approval re-locks Dựng video

    # --- gate run ------------------------------------------------------------
    def _start_gate(self, opts: dict) -> None:
        if self._busy():
            return
        jid = uuid.uuid4().hex[:12]
        job = Job(id=jid, work_dir=settings.vizsup_storage_dir / jid, url=opts.get("url"))
        self._run_gate(job, opts, f"▶ Bắt đầu (job {jid})")

    def _run_gate(self, job: Job, opts: dict, first_line: str) -> None:
        """New run or E-11 resume: the worker skips every step whose file exists."""
        self.opts, self.job = opts, job
        self._gate_job = job
        store.save_job(job, opts, "running")
        store.debug(f"=== run_gate job={job.id} ===")
        self.stack.setCurrentIndex(INPUT)
        self.input.set_busy(True)
        self.input.append_log(first_line)
        self._run("run_to_gate", opts, {
            "stage_started": self._on_stage,
            "progress": self.input.set_progress,
            "log": self.input.append_log,
            "failed": self._on_gate_failed,
            "gate_reached": self._on_gate,
            "split_done": self._on_split_done,
        })

    def _on_gate_failed(self, msg: str) -> None:
        self._gate_job = None
        self.input.show_error(msg)
        if not msg.strip().startswith("Đã hủy"):
            self._notify("Chưa xong video", msg[:160], "err")

    def _on_stage(self, s: str) -> None:
        self.input.append_log(f"• {s}")
        self.input.set_stage(s)
        running = self._queue_job or self.job
        if s != "download" and running is not None:
            md = dict(running.metadata)     # copy: the worker thread owns the dict
            plat = {"douyin": "Douyin", "bilibili": "Bilibili"}.get(md.get("platform") or "", "")
            try:
                dur = fmt_time(float(md.get("duration") or 0), 0) if md.get("duration") else ""
            except (TypeError, ValueError):
                dur = ""
            self.input.set_title(md.get("title") or "", " · ".join(p for p in (dur, plat) if p))

    def _on_render_done(self, path: str) -> None:
        from pathlib import Path

        self._render_item = None
        self.render.show_done(path)
        if self.job is not None:
            self.render.set_group(_group_status(self.job))
            self.editor.reload_fit_report()             # F-10: late dub lines
        quick = Path(path).name == "preview.mp4"
        self._notify("Bản xem nhanh đã sẵn sàng" if quick else "Đã dựng xong video",
                     Path(path).name)

    def _on_render_failed(self, msg: str) -> None:
        self._render_item = None
        self.render.show_error(msg)
        if not msg.strip().startswith("Đã hủy"):
            self._notify("Chưa dựng được video", msg[:160], "err")

    def _on_gate(self) -> None:
        self._gate_job = None
        store.debug("on_gate: editor.load begin")
        try:
            if self._queue_job is None:          # the queue's progress card is not ours
                self.input.set_done()
            from app.pipeline import split

            split.inherit_style(self.job, settings.vizsup_storage_dir)
            self.editor.load(self.job)
            self.editor.set_context(self.opts)  # tts/voice/translator for preview & shorten
            store.debug("on_gate: editor.load done")
            store.save_job(self.job, self.opts, "await_edit")  # title now known → resumable
            self.stack.setCurrentIndex(EDITOR)
            self.status.setText("● Sửa phụ đề — duyệt khi xong")
            self._notify("Phụ đề đã sẵn sàng",
                         f"{len(self.editor.cues)} câu · {self.editor.pending_count()} câu cần xem lại")
            store.debug("on_gate: switched to editor")
            if self._autopilot_plan(self.job)[1] is not None:
                # Tự động đăng: straight to the one-click review (paste link → approve)
                QTimer.singleShot(400, lambda jid=self.job.id: self._fast_approve(jid))
        except Exception as exc:  # noqa: BLE001 - never let editor-load kill the app
            import traceback as _tb
            store.debug(f"on_gate FAILED {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
            self.input.show_error(f"Lỗi mở trình sửa: {type(exc).__name__}: {exc}")

    def _resume(self, job_id: str) -> None:
        queued = self._queue_job is not None
        if queued and job_id == self._queue_job.id:
            self.status.set_status("Video này đang xử lý trong hàng đợi.", "warn")
            return
        if self._batch_job is not None and job_id == self._batch_job.id:
            self.status.set_status("Video này đang được dựng (hàng loạt) — đợi xong rồi mở.",
                                   "warn")
            return
        # don't hijack self.job/self.opts from a running worker — a QUEUED run has its
        # own job, so a finished project can still be opened for editing meanwhile; a
        # batch render owns only its own job, so others open too
        if (self._busy() and not queued) or (self._render_busy() and not self._batch_active()):
            self.status.setText("● Đang bận — đợi tác vụ hiện tại xong rồi mở dự án khác.")
            return
        loaded = store.load_job(job_id)
        if not loaded:
            self.status.set_status("Không tìm thấy dự án này (thư mục đã bị xóa?).", "warn")
            self.input.refresh_recent()
            return
        job, opts = loaded
        if job.vi_srt.exists() and not job.source_video.exists() and opts \
                and _source_refetchable(job, opts):
            # "Chỉ xóa file nặng" took the source: fetch it again, every other step is kept
            # (cn.srt / vi.srt exist → no OCR/ASR/translate), then the editor opens as usual
            if self._busy():
                self.status.set_status("Đợi hàng đợi xong rồi mở dự án này (cần tải lại video "
                                       "gốc).", "warn")
                return
            self._run_gate(job, {**opts, "extract_terms": False},
                           f"▶ Tải lại video gốc đã dọn (job {job.id}) — phụ đề giữ nguyên")
        elif job.vi_srt.exists():
            self.job, self.opts = job, opts
            self._on_gate()  # reopen straight at the edit gate (no re-download/ASR)
        elif opts and self._busy():
            self.status.set_status("Đợi hàng đợi xong rồi tiếp tục dự án đang dở.", "warn")
        elif opts:
            # E-11: an unfinished run — continue from the first missing step
            self._run_gate(job, opts, f"▶ Tiếp tục dự án đang dở (job {job.id})")

    # --- Thư viện (every project) -----------------------------------------------------
    def _open_library(self) -> None:
        idx = self.stack.currentIndex()
        if idx == LIBRARY:
            self.nav.library_btn.setChecked(True)      # the click toggled it off
            return
        self._before_library = self._before_settings if idx == SETTINGS else idx
        self.library.refresh()
        self.stack.setCurrentIndex(LIBRARY)

    def _library_guard(self, item: dict) -> str | None:
        """Why ``item`` (a library project / split video) must not be deleted now, or None.
        The open project (the editor autosaves into its folder), the queued and running
        ones, and a split video being joined are off-limits."""
        ids = {item["id"], *(p["id"] for p in item.get("parts") or [])}
        if self.job is not None and self.job.id in ids:
            return "đang mở"
        if self._queue_job is not None and self._queue_job.id in ids:
            return "đang xử lý"
        if ids & {o.get("_job_id") for o in self._queue if o.get("_job_id")}:
            return "đang chờ trong hàng đợi"
        if self._join_gid in ids and self._any_busy():
            return "đang ghép video"
        if ids & self._compile_ids and self._any_busy():
            return "đang ghép video"
        if self._batch_job is not None and self._batch_job.id in ids:
            return "đang dựng"
        if ids & {b["id"] for b in self._batch}:
            return "đang chờ dựng"
        return None

    def _delete_job(self, job_id: str) -> None:
        """"Xóa dự án…" on a recent card: the library's flow (guard → confirm → Recycle Bin)."""
        item = store.library_item(job_id)
        if item is None:
            self.status.set_status("Không tìm thấy dự án này (thư mục đã bị xóa?).", "warn")
            self.input.refresh_recent()
            return
        self.library.delete_items([item])

    def _goto_step(self, i: int) -> None:
        if i == INPUT:
            self.stack.setCurrentIndex(INPUT)
        elif i == EDITOR and self.job is not None and self.editor.cues:
            self.stack.setCurrentIndex(EDITOR)
        elif i == RENDER and self.job is not None and self.editor.cues and self.job.vi_srt.exists():
            self._approve()

    # --- approve -> render ---------------------------------------------------
    def _approve(self) -> None:
        """The human gate: one explicit confirmation, recorded as the approved
        revision (app.pipeline.approval). Render refuses anything else."""
        from app.pipeline import approval

        if self.job is None or not self.editor.cues:
            self.status.set_status("Hãy tạo phụ đề cho một video trước.", "warn")
            return
        try:
            self.editor.save_now()
            tts_name = self.opts.get("tts") or settings.vizsup_default_tts
            try:
                from app.providers.registry import local_voice_choices

                voices = list(get_tts(tts_name).voices()) + local_voice_choices(tts_name)
            except Exception:  # noqa: BLE001
                voices = []
            if not approval.is_approved(self.job):
                from desktop.widgets.dialogs import ApproveDialog

                voice = _default_voice(self.job, voices, self.opts) or tts_name
                dur = self.editor.video.duration() or max((c.end for c in self.editor.cues),
                                                          default=0.0)
                dlg = ApproveDialog(self, cues=len(self.editor.cues),
                                    voice=str(voice).replace("vi-VN-", "").replace("Neural", ""),
                                    duration=fmt_time(dur, 0),
                                    pending=self.editor.pending_count())
                if not dlg.exec():
                    if dlg.review_requested:
                        self.stack.setCurrentIndex(EDITOR)
                        self.editor.show_pending()
                    return
                approval.approve(self.job, cues=len(self.editor.cues))
                self.editor.refresh_approval()
                self._learn_from_approval()
                self.status.set_status("Đã duyệt phụ đề — chọn âm thanh rồi dựng video.", "ok")
                self._autopilot_after_approve(self.job)
            self.render.set_voices(voices)
            self._sync_render_page()                    # set_job first: voice.json is per job
            self.render.select_voice(_default_voice(self.job, voices, self.opts))
            self.render.set_preview(self.job.source_video, self.editor.cues)  # show VI caption bar in step 3
            self.stack.setCurrentIndex(RENDER)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"● Lỗi: {exc}")

    # --- autopilot (docs/plan/J-autopilot.md): approve once -> render -> Short -> post ----------
    def _autopilot_plan(self, job):
        from app.pipeline import autopilot, series

        p = series.profile_of(job)
        plan = autopilot.load_plan(p.slug) if p is not None else None
        return p, (plan if plan and plan.get("on") else None)

    def _fast_approve(self, iid: str) -> None:
        """Thư viện -> «Duyệt nhanh»: the flagged lines only + one button. The gate is this click."""
        from app.pipeline import approval, autopilot
        from app.pipeline.compilation import _job_title, clean_title
        from app.pipeline.srt import parse_srt
        from desktop.widgets.autopilot_dialogs import FastApproveDialog

        loaded = store.load_job(iid)
        if loaded is None or not loaded[0].vi_srt.exists():
            self.status.set_status("Dự án này chưa có phụ đề.", "warn")
            return
        job = loaded[0]
        try:
            flagged = store.flagged_lines(job)
        except Exception:  # noqa: BLE001 - flags are a help; the owner can still open the editor
            flagged = []
        p, plan = self._autopilot_plan(job)
        if plan is None:
            after = ("Bộ phim này chưa bật Tự động đăng (Thư viện -> Tự động đăng): duyệt xong "
                     "bạn tự dựng như thường.")
        elif autopilot.render_opts(job) is None:
            after = ("Duyệt xong app CHƯA tự dựng được: bộ phim chưa có tập nào dựng — dựng tay "
                     "một tập để app học cài đặt.")
        else:
            after = ("Duyệt xong app tự dựng theo cài đặt của bộ phim"
                     + (", tự cắt 1 Short" if plan.get("auto_short") else "")
                     + " rồi " + autopilot.summary(p.slug).replace("Tự đăng: ", "đăng lên ") + ".")
        title = clean_title(_job_title(job.work_dir), 80)
        dlg = FastApproveDialog(self, title=title, cues=len(parse_srt(job.vi_srt)),
                                flagged=flagged, after=after)
        ok = dlg.exec()
        if dlg.edit_requested:
            if self.job is None or self.job.id != iid:
                self._resume(iid)
            self.stack.setCurrentIndex(EDITOR)
            return
        if not ok:
            return
        if self.job is not None and self.job.id == job.id:
            self.editor.save_now()             # the open editor's text is what gets approved
        approval.approve(job)
        if self.job is not None and self.job.id == job.id:
            self.editor.refresh_approval()
        self.status.set_status("Đã duyệt " + title[:40], "ok")
        self._autopilot_after_approve(job)

    def _autopilot_after_approve(self, job) -> None:
        """A series with Tự động đăng on: queue the episode's render with its settings."""
        from app.pipeline import autopilot

        _p, plan = self._autopilot_plan(job)
        if plan is None:
            return
        opts = autopilot.render_opts(job)
        if opts is None:
            self.status.set_status("Tự động đăng: bộ phim chưa có tập nào dựng — dựng tay một "
                                   "tập để app học cài đặt.", "warn")
            return
        title = job.metadata.get("title") or job.id
        if self._batch_add([{"id": job.id, "opts": opts, "title": title, "autopilot": "long"}]):
            self.status.set_status("Tự động: đang dựng rồi lên lịch đăng — " + title[:40], "busy")

    def _autopilot_rendered(self, item: dict) -> None:
        """A batch item made by the autopilot finished: queue its posts; after an episode, cut
        the Short (carrying the approval only when its lines are exactly approved lines)."""
        import time as _t

        from app.pipeline import approval, autopilot

        loaded = store.load_job(item["id"])
        if loaded is None:
            return
        job = loaded[0]
        kind = item.get("autopilot") or "long"
        new = autopilot.plan_posts(job, kind)
        if new:
            first = min(p["slot"] for p in new)
            self._notify("Đã lên lịch đăng", f"{len(new)} bài · bài đầu lúc "
                         f"{_t.strftime('%H:%M %d/%m', _t.localtime(first))}")
        _p, plan = self._autopilot_plan(job)
        if kind != "long" or plan is None or not plan.get("auto_short"):
            return

        def done(child):
            if child is None:
                return
            if not approval.is_approved(child):
                self._notify("Short cần duyệt", "Short tự cắt có câu khác bản đã duyệt — mở "
                             "Thư viện để duyệt.", "err")
                return
            opts = autopilot.render_opts(child, "short")
            if opts:
                self._batch_add([{"id": child.id, "opts": opts,
                                  "title": child.metadata.get("title") or child.id,
                                  "autopilot": "short"}])

        self._bg.run(lambda: autopilot.auto_short(job), done,
                     lambda m: self.status.set_status(f"Short tự cắt lỗi: {m}", "warn"))

    def _watch_due(self) -> None:
        """While the app is open: check the followed creators whose time has come (watch.py
        decides) and queue their new episodes with the start screen's options. Skipped when
        the Windows background task is installed (it does the same) or nothing is followed."""
        from app.pipeline import series

        if self._watching or self._closing or not series.followed():
            return
        self._watching = True

        def work():
            from app import schedtask
            from app.pipeline import watch

            if schedtask.task_installed():
                return []
            found: list[tuple[str, str]] = []
            watch.check_all(run=lambda url, slug: found.append((url, slug)) or "hàng đợi của app")
            return found

        def done(found):
            self._watching = False
            if not found:
                return
            base = self.input._collect_opts()
            items = [{**base, "url": url, "file": None, "series": slug} for url, slug in found]
            self._start_queue(items)
            self._notify("Có tập mới", f"{len(items)} tập đang tải và dịch — xong sẽ hỏi bạn duyệt")

        def failed(msg):
            self._watching = False
            store.debug(f"watch failed: {msg}")

        self._bg.run(work, done, failed)

    def _post_due(self) -> None:
        """Every minute: publish the next post whose slot has come (one at a time, off the GUI
        thread); results go to the post queue and a notification."""
        from app.pipeline import autopilot

        if self._posting or self._closing:
            return
        due = autopilot.due()
        if not due:
            return
        post = due[0]
        if not autopilot.claim(post["id"]):         # the scheduled task has it
            return
        self._posting = True

        def done(res):
            from desktop.widgets.autopilot_dialogs import PLATFORM_NAMES

            self._posting = False
            autopilot.record(post["id"], res)
            where = PLATFORM_NAMES.get(post["platform"], post["platform"])
            self._notify(f"{where}: " + ("đã đăng" if res.ok else "đăng lỗi"),
                         res.message or res.url or post["job"], "ok" if res.ok else "err")

        def failed(msg):
            from app.providers.base import PostResult

            done(PostResult(False, "failed", message=msg))

        self._bg.run(lambda: autopilot.publish(post), done, failed)

    def _show_lines(self, rows: list, why: str) -> None:
        """Render page's "Xem N câu": back to the editor with only those lines listed."""
        self.editor.reload_fit_report()
        self.stack.setCurrentIndex(EDITOR)
        self.editor.show_lines(rows, why)

    def _back_to_editor(self) -> None:
        # the voice speed may have changed on the render page: "Quá dài" and the late
        # flags are judged at it, so recompute them before the user looks
        self.editor.reload_fit_report()
        self.stack.setCurrentIndex(EDITOR)

    def _learn_from_approval(self) -> None:
        """E-7: approved lines → translation memory; changed lines → style edits; every
        few approvals the style rules are condensed again (off the GUI thread)."""
        from app.pipeline import memory

        pairs = [(c.zh, c.vi) for c in self.editor.cues if c.zh]
        try:
            memory.remember(pairs)
            since = memory.record_edits(self.job, pairs)
        except Exception as exc:  # noqa: BLE001 - learning never blocks approving
            store.debug(f"memory: {exc}")
            return
        if since >= memory.STYLE_EVERY:
            provider = self.opts.get("refine") or self.opts.get("translator") or "openrouter"
            self.editor._run_async(lambda: memory.update_style(provider),
                                   lambda rules: rules and self.editor.inspector.set_style_rules(rules),
                                   lambda _m: None)

    def _sync_render_page(self) -> None:
        """Render page mirrors the editor's project + the backend approval state."""
        from app.pipeline import approval

        if self.job is None:
            return
        self.render.set_project(self.editor.project.title.toolTip() or self.job.id,
                                self.editor.project.meta.text())
        self.render.set_job(self.job, cues=len(self.editor.cues),
                            tts=self.opts.get("tts") or settings.vizsup_default_tts)
        self.render.set_approved(approval.is_approved(self.job))
        from app.pipeline import speakers

        self.render.suggest_original(speakers.singing(self.job))
        self.render.set_group(_group_status(self.job))
        self.render.set_style(self.editor.get_style(), self.editor.get_size_px(),
                              self.editor.get_font())

    def _on_render_stage(self, s: str) -> None:
        self.render.append_log(f"• {s}")
        self.render.set_stage(s)

    def _preview_style(self) -> None:
        """Burn the editor's chosen style onto one frame (off the UI thread) and
        show it in a popup. (Phase B will move this into the live video preview.)"""
        if self.job is None:
            return
        from app.pipeline import assemble
        sp = self.editor.style_panel
        job, cues = self.job, self.editor.cues
        size = int(round(self.editor.get_size_px() * 3))  # UI px → .ass units (1080 canvas)
        font, style = self.editor.get_font(), self.editor.get_style()
        sp.preview_btn.setEnabled(False)
        sp.preview_btn.setText("⏳  Đang tạo…")

        def reset():
            sp.preview_btn.setEnabled(True)
            sp.preview_btn.setText("🖼  Xem thử kiểu trên video")

        def work():
            return str(assemble.style_preview_frame(
                job, cues, font=font, size=size, cover_hardsubs=False, style=style))

        def done(path):
            self._show_style_popup(path)
            reset()

        def fail(msg):
            self.status.setText(f"● Lỗi xem trước: {msg}")
            reset()

        self.editor._run_async(work, done, fail)  # reuse the editor's joined task runner

    def _show_style_popup(self, path: str) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QDialog, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Xem thử kiểu phụ đề")
        lay = QVBoxLayout(dlg)
        lbl = QLabel()
        pm = QPixmap(str(path))
        if not pm.isNull():
            lbl.setPixmap(pm.scaledToHeight(760, Qt.SmoothTransformation))
        lay.addWidget(lbl)
        dlg.exec()

    def _render_slot(self) -> str | None:
        """Where a render/join can run now: "main", "render" (the queue holds main), or
        None (busy with something that owns self.job)."""
        if self._render_busy():
            return None
        if not self._busy():
            return "main"
        return "render" if self._queue_job is not None else None

    def _start_render(self, ropts: dict) -> None:
        if self._batch_active() and not ropts.get("preview"):
            self._render_later(ropts)           # one render at a time: join the batch
            return
        slot = self._render_slot()
        if slot is None:
            self.status.set_status("Đang bận — dựng video khi tác vụ hiện tại xong.", "warn")
            return
        from app.pipeline.srt import parse_srt

        self.editor.save_now()  # vi.srt == exactly what the user approved
        merged = {
            "tts": self.opts.get("tts") or settings.vizsup_default_tts,
            "style": self.editor.get_style(),
            "font": self.editor.get_font(),
            "size": self.editor.get_size_px(),
            **ropts,
            # snapshot the approved cues: the burned subs always match the text
            # TTS just spoke, even if vi.srt were somehow rewritten mid-render
            "burn_cues": parse_srt(self.job.vi_srt),
        }
        if not merged.get("preview"):
            self._render_item = {"id": self.job.id, "title": self.job.metadata.get("title") or "",
                                 "opts": {k: v for k, v in merged.items() if k != "burn_cues"}}
        self._set_render_locked(True)
        self._run("run_render", merged, {
            "stage_started": self._on_render_stage,
            "progress": self.render.set_progress,
            "log": self.render.append_log,
            "failed": self._on_render_failed,
            "render_done": self._on_render_done,
        }, slot=slot)

    def _set_render_locked(self, on: bool) -> None:
        """One switch for everything that could mutate vi.srt or hijack the job
        while a render worker is reading it."""
        self.render.set_rendering(on)
        self.steps.setEnabled(not on)     # no FlowSteps navigation mid-render
        self.editor.setEnabled(not on)    # no edits landing in autosave mid-render

    def _cancel_render(self) -> None:
        worker = self._rworker if self._render_busy() else (self._worker if self._busy() else None)
        if worker is not None and worker is self._batch_worker:
            return                                # the batch has its own Hủy (Thư viện)
        if worker is not None and (worker is self._rworker or self._queue_job is None):
            worker.cancel()
            self.render.set_cancelling()

    # --- worker plumbing -----------------------------------------------------
    # --- E-9: several videos, one after another ------------------------------------
    def _start_queue(self, items: list[dict], *, front: bool = False) -> None:
        if front:                                 # a split video's parts run next
            self._queue[:0] = items
        else:
            self._queue.extend(items)
        self._queue_total += len(items)
        self._persist()
        self.status.set_status(f"Hàng đợi: {len(self._queue)} video chờ xử lý", "busy")
        if not self._busy():
            self._run_next_queued()

    def _run_next_queued(self) -> None:
        if self._busy():
            QTimer.singleShot(500, self._run_next_queued)
            return
        if not self._queue:
            ok, failed = self._queue_ok, self._queue_failed
            if self._queue_cancelled:
                self.status.set_status(f"Đã dừng hàng đợi — {ok} mục đã tới bước duyệt", "warn")
            elif self._queue_total and (ok or failed):
                tail = f" · {failed} mục lỗi" if failed else ""
                self.status.set_status(f"Xong hàng đợi: {ok} mục tới bước duyệt{tail} — mở ở "
                                       f"“Tiếp tục dự án”", "warn" if failed else "ok")
                self._notify("Xong hàng đợi", f"{ok} mục đã tới bước duyệt{tail}",
                             "err" if failed and not ok else "ok")
            self._queue_total, self._queue_job = 0, None
            self._queue_ok = self._queue_failed = 0
            self._queue_cancelled = False
            return
        opts = self._queue.pop(0)
        loaded = store.load_job(opts["_job_id"]) if opts.get("_job_id") else None
        if opts.get("_job_id") and loaded is None:   # a part deleted by hand: skip it
            self.input.append_log(f"⚠ Bỏ qua một phần đã bị xóa ({opts['_job_id']})")
            self._queue_failed += 1
            QTimer.singleShot(0, self._run_next_queued)
            return
        if loaded is not None:                    # a part of a split video: folder exists
            job = loaded[0]
        else:
            jid = uuid.uuid4().hex[:12]
            job = Job(id=jid, work_dir=settings.vizsup_storage_dir / jid, url=opts.get("url"))
        self._queue_job = job
        self._queue_opts = {**opts, "_job_id": job.id}
        store.save_job(job, opts, "running")
        k = self._queue_total - len(self._queue)
        self.input.set_busy(True)
        what = job.metadata.get("title") if opts.get("_job_id") else None
        self.input.append_log(f"▶ Hàng đợi {k}/{self._queue_total}: "
                              f"{what or opts.get('url') or opts.get('file')}")
        self.status.set_status(f"Hàng đợi {k}/{self._queue_total} — bạn vẫn mở được video đã xong",
                               "busy")
        self._run("run_to_gate", opts, {
            "stage_started": self._on_stage,
            "progress": self.input.set_progress,
            "log": self.input.append_log,
            "failed": lambda m, j=job, o=opts: self._on_queued_failed(j, o, m),
            "gate_reached": lambda j=job, o=opts: self._on_queued_gate(j, o),
            "split_done": self._on_split_done,
        }, job=job)

    def _on_split_done(self, items: list) -> None:
        """A long video became parts: queue them first, keep the app free meanwhile."""
        self.input.set_done()
        self.input.refresh_recent()
        self._start_queue(list(items), front=True)
        self.status.set_status(f"Đã chia {len(items)} phần — đang xử lý lần lượt, phần nào xong "
                               f"sẽ báo để bạn duyệt", "busy")
        self._notify("Đã chia video dài", f"{len(items)} phần ~30 phút — phần 1 xong sẽ báo")

    def _resume_group(self, gid: str) -> None:
        """Queue every part of a split video that has not reached the gate yet."""
        from app.pipeline import split

        group = split.load_group(settings.vizsup_storage_dir / gid)
        if not group:
            return
        queued = {o.get("_job_id") for o in self._queue}
        running = self._queue_job.id if self._queue_job is not None else None
        items = []
        for p in sorted(group["parts"], key=lambda x: x["index"]):
            loaded = store.load_job(p["id"])
            if loaded is None or loaded[0].vi_srt.exists() or p["id"] in queued or p["id"] == running:
                continue
            opts = dict(loaded[1] or group.get("opts") or {})   # a part saved without options
            opts["_job_id"] = p["id"]
            items.append(opts)
        if not items:
            self.status.set_status("Mọi phần đã tới bước duyệt (hoặc đang chạy).", "ok")
            return
        self._start_queue(items)

    def _start_join(self, gid: str) -> None:
        """Join a split video's rendered parts into one MP4 (+ merged .srt)."""
        from app.pipeline import split

        slot = self._render_slot()
        if slot is None:
            self.status.set_status("Đang bận — ghép video khi tác vụ hiện tại xong.", "warn")
            return
        group = split.load_group(settings.vizsup_storage_dir / gid)
        if not group:
            return
        ready = split.readiness(group, settings.vizsup_storage_dir)
        missing = [str(r["index"]) for r in ready if not r["rendered"]]
        if missing:
            self.status.set_status("Chưa ghép được — còn phần chưa duyệt/dựng: " + ", ".join(missing),
                                   "warn")
            return
        n = len(ready)
        self._join_gid = gid
        self.render.set_project(group.get("title") or gid, f"Video đầy đủ · {n} phần")
        self.stack.setCurrentIndex(RENDER)
        self.render.start_join(n)
        self._set_render_locked(True)
        self._run("run_join", {"_group": gid}, {
            "stage_started": self._on_render_stage,
            "progress": self.render.set_progress,
            "log": self.render.append_log,
            "failed": self._on_render_failed,
            "render_done": lambda path, t=group.get("title") or gid: self._on_join_done(path, t),
        }, job=Job(id=gid, work_dir=settings.vizsup_storage_dir / gid), slot=slot)

    def _on_join_done(self, path: str, title: str) -> None:
        self.render.show_joined(path, title)
        self.input.refresh_recent()
        self._notify("Đã ghép xong video đầy đủ", title[:120])

    # --- Ghép tập: rendered episodes → one long video (app/pipeline/compilation.py) ----
    def _start_compile(self, spec: dict) -> None:
        slot = self._render_slot()
        if slot is None:
            self.library.compile_finished(None, "Đang bận (dựng / xử lý video) — ghép lại khi "
                                                "tác vụ hiện tại xong.")
            self.status.set_status("Đang bận — ghép video khi tác vụ hiện tại xong.", "warn")
            return
        cid = uuid.uuid4().hex[:12]
        job = Job(id=cid, work_dir=settings.vizsup_storage_dir / cid)
        title = spec.get("title") or "Video ghép"
        self._compile_ids = {*spec.get("ids", []), cid}
        self.library.compile_started(title, len(spec.get("ids") or []))
        self.status.set_status(f"Đang ghép “{title}”…", "busy")
        self._run("run_compile", {"_compile": spec}, {
            "progress": self.library.set_compile_progress,
            "failed": self._on_compile_failed,
            "render_done": lambda path, t=title: self._on_compile_done(path, t),
        }, job=job, slot=slot)
        self._compile_worker = self._rworker if slot == "render" else self._worker

    def _cancel_compile(self) -> None:
        try:
            if self._compile_worker is not None:
                self._compile_worker.cancel()
        except RuntimeError:              # the worker already finished and was deleted
            pass

    def _on_compile_done(self, path: str, title: str) -> None:
        self._compile_ids, self._compile_worker = set(), None
        self.library.compile_finished(path, "✓ Đã ghép xong — xem, hoặc mở thư mục để lấy "
                                            "video, phụ đề .srt và mục lục chương")
        self.status.set_status(f"Đã ghép xong “{title}”", "ok")
        self._notify("Đã ghép xong video", title[:120])

    def _on_compile_failed(self, msg: str) -> None:
        self._compile_ids, self._compile_worker = set(), None
        cancelled = msg.strip().startswith("Đã hủy")
        self.library.compile_finished(None, "Đã hủy ghép video." if cancelled else f"⚠ {msg}")
        self.status.set_status("Đã hủy ghép video." if cancelled else "Chưa ghép được video.",
                               "warn")
        if not cancelled:
            self._notify("Chưa ghép được video", msg[:160], "err")

    # --- Dựng hàng loạt (app/pipeline/batch.py) ----------------------------------------
    def _batch_active(self) -> bool:
        return self._batch_job is not None or bool(self._batch)

    def _batch_ids(self) -> set[str]:
        ids = {b["id"] for b in self._batch}
        if self._batch_job is not None:
            ids.add(self._batch_job.id)
        return ids

    def _render_later(self, ropts: dict) -> None:
        """"Dựng sau" on the render page: queue the open (approved) project with the
        settings chosen there; the user can go on to the next episode."""
        from app.pipeline import approval, batch

        if self.job is None or ropts.get("preview"):
            return
        self.editor.save_now()
        if not approval.is_approved(self.job):
            self.status.set_status("Duyệt phụ đề trước khi thêm vào hàng dựng.", "warn")
            return
        opts = {"tts": self.opts.get("tts") or settings.vizsup_default_tts,
                "style": self.editor.get_style(), "font": self.editor.get_font(),
                "size": self.editor.get_size_px(), **ropts}
        batch.save_opts(self.job.work_dir, opts)
        title = self.editor.project.title.toolTip() or self.job.id
        added = self._batch_add([{"id": self.job.id, "opts": opts, "title": title}])
        n = len(self._batch_ids())
        self.status.set_status(
            (f"Đã thêm vào hàng dựng ({n} video) — mở tập khác để sửa tiếp trong lúc chờ"
             if added else "Video này đã có trong hàng dựng."), "ok" if added else "warn")

    def _start_batch(self, spec: dict) -> None:
        """Thư viện → Dựng hàng loạt: the picked projects with the template's settings."""
        from app.pipeline import batch

        tid = spec.get("template")
        template = batch.template_from(settings.vizsup_storage_dir / tid) if tid else None
        if not template:
            self.status.set_status("Chưa có cài đặt mẫu — dựng một video trước.", "warn")
            return
        items = []
        for jid in spec.get("ids") or []:
            loaded = store.load_job(jid)
            if loaded is None:
                continue
            job, jopts = loaded
            opts = batch.opts_for(job.work_dir, template)
            opts["tts"] = template.get("tts") or jopts.get("tts") or settings.vizsup_default_tts
            items.append({"id": jid, "opts": opts,
                          "title": job.metadata.get("title") or jid})
        added = self._batch_add(items)
        self.status.set_status(f"Đã thêm {added} video vào hàng dựng", "busy")

    def _batch_add(self, items: list[dict]) -> int:
        have = self._batch_ids()
        fresh = [it for it in items if it["id"] not in have]
        if not fresh:
            return 0
        starting = not self._batch_active()
        if starting:
            self._batch_total = 0
            self._batch_ok, self._batch_failed = [], []
            self._batch_cancelled = False
        self._batch.extend(fresh)
        self._batch_total += len(fresh)
        self._persist()
        self.render.set_batch_active(True)
        if starting:
            self.library.batch_started(self._batch_total)
            self._batch_next()
        else:
            self._batch_progress(*self._batch_last)
        return len(fresh)

    def _heavy_busy(self) -> bool:
        """A render / join / compile holds a slot (a batch item waits for it)."""
        main_heavy = self._busy() and self._main_method in ("run_render", "run_join", "run_compile")
        return self._render_busy() or main_heavy

    def _batch_next(self) -> None:
        if self._batch_job is not None:
            return
        if not self._batch:
            self._batch_finish()
            return
        if self._heavy_busy():
            QTimer.singleShot(1000, self._batch_next)
            return
        from app.pipeline.srt import parse_srt

        item = self._batch.pop(0)
        self._batch_item = item
        loaded = store.load_job(item["id"])
        if loaded is None or not loaded[0].vi_srt.exists():
            self._batch_failed.append(f"{item['title']}: không còn trên máy")
            QTimer.singleShot(0, self._batch_next)
            return
        job = loaded[0]
        self._batch_job = job
        if self.job is not None and self.job.id == job.id:
            self.editor.save_now()
            self.editor.setEnabled(False)       # vi.srt must not change while it is voiced
        opts = {**item["opts"], "burn_cues": parse_srt(job.vi_srt)}
        self._batch_progress(0.0, "Bắt đầu…")
        self._run("run_render", opts, {
            "progress": self._batch_progress,
            "failed": lambda m, it=item: self._batch_item_failed(it, m),
            "render_done": lambda path, it=item: self._batch_item_done(it, path),
        }, job=job, slot="render")
        self._batch_worker = self._rworker

    def _batch_progress(self, frac: float, msg: str) -> None:
        if frac < 0:                       # only a message (waiting for the network)
            frac = self._batch_last[0]
        self._batch_last = (frac, msg)
        total = max(1, self._batch_total)
        k = len(self._batch_ok) + len(self._batch_failed) + (1 if self._batch_job else 0)
        title = ""
        if self._batch_job is not None:
            from app.pipeline.compilation import clean_title

            title = clean_title(self._batch_job.metadata.get("title") or self._batch_job.id, 40)
        text = f"Video {k}/{total}" + (f" · {title}" if title else "") + (f" — {msg}" if msg else "")
        self.library.set_batch_progress((max(0, k - 1) + frac) / total, text)
        self.status.set_status(f"Dựng hàng loạt {k}/{total} · {int(frac * 100)}%", "busy")

    def _batch_item_done(self, item: dict, path: str) -> None:
        self._batch_ok.append(item["title"])
        self._batch_after(item)
        if item.get("autopilot"):
            self._autopilot_rendered(item)
        shown = getattr(self.render, "_job", None)
        if self.job is not None and self.job.id == item["id"] and shown is not None                 and getattr(shown, "id", None) == item["id"]:
            self.render.show_done(path)
            self.render.set_group(_group_status(self.job))

    def _batch_item_failed(self, item: dict, msg: str) -> None:
        if msg.strip().startswith("Đã hủy"):
            self._batch_cancelled = True
            self._batch.clear()
        else:
            self._batch_failed.append(f"{item['title']}: {msg}")
        self._batch_after(item)

    def _batch_after(self, item: dict) -> None:
        self._batch_item = None
        if self.job is not None and self.job.id == item["id"]:
            self.editor.setEnabled(True)
        self._batch_job, self._batch_worker = None, None
        self.input.refresh_recent()
        QTimer.singleShot(300, self._batch_next)

    def _batch_finish(self) -> None:
        ok, failed = len(self._batch_ok), len(self._batch_failed)
        total = self._batch_total
        self.render.set_batch_active(False)
        if not total:
            return
        if self._batch_cancelled:
            msg = f"Đã dừng hàng dựng — {ok} video đã dựng xong"
        else:
            msg = f"✓ Đã dựng {ok}/{total} video" + (f" · {failed} lỗi" if failed else "")
        if failed:
            msg += "\n" + "\n".join(f"⚠ {f[:140]}" for f in self._batch_failed[:5])
        self.library.batch_finished(msg)
        self.status.set_status(msg.splitlines()[0], "warn" if failed or self._batch_cancelled
                               else "ok")
        if not self._batch_cancelled:
            self._notify("Xong dựng hàng loạt", msg.splitlines()[0], "err" if failed and not ok
                         else "ok")
        self._batch_total = 0

    def _cancel_batch(self) -> None:
        self._batch_cancelled = True
        self._batch.clear()
        try:
            if self._batch_worker is not None:
                self._batch_worker.cancel()
                return
        except RuntimeError:
            pass
        if self._batch_job is None:
            self._batch_finish()

    # --- crash / power-cut recovery (desktop/session.py) ----------------------------------
    def _session_state(self) -> dict:
        queue = list(self._queue)
        if self._queue_opts is not None:
            queue.insert(0, self._queue_opts)
        batch = list(self._batch)
        if self._batch_item is not None:
            batch.insert(0, self._batch_item)
        gate = None
        if self._gate_job is not None:
            gate = {"job_id": self._gate_job.id, "opts": self.opts}
        return {"gate": gate, "queue": queue, "render": self._render_item, "batch": batch}

    def _persist(self) -> None:
        if self._closing:
            return
        from desktop import session

        session.save(self._session_state())

    def _confirm_close(self) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, "Đang xử lý video",
                          "vizsup đang dịch / dựng video. Đóng bây giờ thì việc đang chạy dừng "
                          "lại — phần đã làm được giữ, lần sau mở app sẽ hỏi để làm tiếp.",
                          parent=self)
        close = box.addButton("Đóng", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Ở lại", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is close

    def _offer_recovery(self) -> None:
        """The previous run ended (crash, forced close, power cut) with work unfinished:
        offer to continue it. Only for a visible window — never pops up in tests."""
        from desktop import session

        state = session.load()
        if not state or not self.isVisible():
            return

        def title_of(jid) -> str:
            loaded = store.load_job(str(jid)) if jid else None
            return (loaded[0].metadata.get("title") if loaded else None) or str(jid)

        lines = session.describe(state, title_of)
        if self._ask_recovery(lines):
            self.resume_session(state)
        else:
            session.clear()

    def _ask_recovery(self, lines: list[str]) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, "Làm tiếp việc còn dở?",
                          "Lần trước vizsup đóng khi còn việc chưa xong:\n\n"
                          + "\n".join(f"• {ln}" for ln in lines)
                          + "\n\nLàm tiếp từ chỗ đã dừng? (phần đã xong không làm lại)",
                          parent=self)
        go = box.addButton("Làm tiếp", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Bỏ qua", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is go

    def resume_session(self, state: dict) -> None:
        """Re-start what the previous run left: the gate run, the gate queue, the render
        and the batch (renders only for projects still approved)."""
        from app.pipeline import batch

        base = settings.vizsup_storage_dir
        gate = state.get("gate") or {}
        if gate.get("job_id"):
            loaded = store.load_job(str(gate["job_id"]))
            if loaded is not None and not loaded[0].vi_srt.exists():
                self._run_gate(loaded[0], gate.get("opts") or loaded[1] or {},
                               f"▶ Làm tiếp video đang dịch dở (job {loaded[0].id})")
        queue = []
        for o in state.get("queue") or []:
            jid = o.get("_job_id")
            loaded = store.load_job(str(jid)) if jid else None
            if jid and (loaded is None or loaded[0].vi_srt.exists()):
                continue                      # gone, or reached the gate after all
            queue.append(dict(o))
        if queue:
            self._start_queue(queue)
        renders = ([state["render"]] if state.get("render") else []) + list(state.get("batch") or [])
        ok = [r for r in renders if r.get("id") and batch.render_state(base / str(r["id"]))]
        if ok:
            self._batch_add([{"id": r["id"], "opts": r.get("opts") or {},
                              "title": r.get("title") or r["id"]} for r in ok])
        self._persist()

    def _delete_group(self, gid: str) -> None:
        """A split video from the recent list: every part + the parent folder, through
        the library's flow (guard → confirm → Recycle Bin)."""
        self._delete_job(gid)

    def _on_queued_gate(self, job: Job, opts: dict) -> None:
        self._queue_opts = None
        self._queue_ok += 1
        self.input.set_done()
        store.save_job(job, opts, "await_edit")
        self.input.refresh_recent()
        title = (job.metadata.get("title") or job.id).split("#")[0].strip()
        self._notify("Phụ đề đã sẵn sàng", f"{title} — mở ở “Tiếp tục dự án”")

    def _on_queued_failed(self, job: Job, opts: dict, msg: str) -> None:
        self._queue_opts = None
        if msg.strip().startswith("Đã hủy"):
            self._queue_cancelled = True
        else:
            self._queue_failed += 1
        self.input.show_error(msg)
        self.input.refresh_recent()
        if not msg.strip().startswith("Đã hủy"):
            self._notify("Một video trong hàng đợi lỗi", msg[:160], "err")

    def _run(self, method: str, opts: dict, connections: dict, *, job: Job | None = None,
             slot: str = "main") -> None:
        """Build worker+thread, connect ALL signals BEFORE start (no emit races),
        then start. One run at a time (callers guard with _busy()).

        Every slot goes through a UiCall (desktop/ui_thread.py) that LIVES ON THE GUI
        THREAD. A worker signal
        connected straight to a lambda runs that lambda on the WORKER thread (Qt has no
        receiver object to queue to), and the lambda then touched a QWidget — that
        corrupted memory and killed the app at random later points (an access
        violation inside subprocess for ffmpeg, or in pathlib), 2026-10-01. With the
        relay, bound methods and lambdas alike are queued onto the GUI thread."""
        if slot == "main":
            self._main_method = method
        thread = QThread()
        worker = PipelineWorker(job or self.job, opts)
        worker.moveToThread(thread)
        thread.started.connect(getattr(worker, method))
        relays = "_r_ui_calls" if slot == "render" else "_ui_calls"
        for relay in getattr(self, relays, []):        # drop that slot's previous relays
            relay.deleteLater()
        setattr(self, relays, [connect_on_ui(getattr(worker, name), fn, self)
                               for name, fn in connections.items()])
        if slot == "render":
            self._rthread, self._rworker = thread, worker
            worker.finished.connect(self._on_render_finished)   # a QObject slot: GUI thread
        else:
            self._thread, self._worker = thread, worker
            worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        self._persist()
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _busy(self) -> bool:
        """The main slot is running (a gate run, the queue, or a render)."""
        try:
            return bool(self._thread and self._thread.isRunning())
        except RuntimeError:  # C++ thread object already deleted
            return False

    def _render_busy(self) -> bool:
        """The second slot is rendering/joining (only while the queue runs)."""
        try:
            return bool(self._rthread and self._rthread.isRunning())
        except RuntimeError:
            return False

    def _any_busy(self) -> bool:
        return self._busy() or self._render_busy()

    def _on_render_finished(self) -> None:
        self._set_render_locked(False)
        self._persist()

    def _on_finished(self) -> None:
        # gate failed/cancelled → re-enable Start but KEEP the log/error visible
        self.input.idle()
        # single unlock point for EVERY render exit path (done/failed/cancelled) —
        # per-handler unlocks would risk a soft-locked app on a missed path; a render in
        # the second slot unlocks itself (_on_render_finished)
        if not self._render_busy():
            self._set_render_locked(False)
        self._persist()
        if self._queue_job is not None or self._queue:   # E-9: on to the next queued video
            QTimer.singleShot(400, self._run_next_queued)

    def _cancel(self) -> None:
        # cooperative: flag it and keep the UI in a "cancelling" state; the actual
        # reset happens on the worker's failed/finished signal (cancel is only
        # honored between stages, so the current stage runs to completion).
        if self._busy() and self._worker:
            if self._queue or self._queue_job is not None:
                self._queue_cancelled = True
            if self._queue:                       # E-9: Hủy stops the whole queue
                self.input.append_log(f"⏹ Bỏ {len(self._queue)} video còn chờ trong hàng đợi")
                self._queue.clear()
            self._worker.cancel()
            self.input.set_cancelling()
            self._persist()

    def closeEvent(self, e) -> None:
        store.debug(f"closeEvent (window closed) busy={self._busy()}")
        if self._any_busy() and self.isVisible() and not self._confirm_close():
            e.ignore()
            return
        self._persist()
        self._closing = True              # the cancels below must not erase the plan
        # flush a pending debounced edit BEFORE teardown — but never while a render
        # worker might be reading vi.srt mid-run
        try:
            if not self._any_busy():
                self.editor.flush()
        except Exception:  # noqa: BLE001 - closing must never get stuck on a save
            pass
        # never let Python tear down a still-running QThread (hard crash) — cancel + join
        for thread, worker in ((self._thread, self._worker), (self._rthread, self._rworker)):
            try:
                if thread and thread.isRunning():
                    if worker:
                        worker.cancel()
                    thread.quit()
                    if not thread.wait(5000):
                        thread.terminate()
                        thread.wait()
            except RuntimeError:
                pass
        try:
            self.editor.shutdown()  # join any preview/shorten task threads
            self.input.shutdown()   # recent-project summaries (thumbnails)
            self.library.shutdown()  # library sizes / thumbnails
            self.render.shutdown()  # voice sample synthesis
            self.settings_view.shutdown()  # connection tests / model list
            self._bg.shutdown()            # the daily model-catalog check
        except Exception:  # noqa: BLE001
            pass
        e.accept()

    def _on_save_failed(self, msg: str) -> None:
        self.nav.set_saved("error")
        self.status.set_status(f"Chưa lưu được ({msg}) — đang thử lại…", "err")

    def _on_dirty(self, dirty: bool) -> None:
        self.nav.set_saved("saving" if dirty else "saved")

    def _open_glossary(self, *, job: bool) -> None:
        from desktop.glossary_dialog import GlossaryDialog

        dlg = GlossaryDialog(self, job=self.job if job else None)
        if dlg.exec():
            self.status.setText("● Đã lưu bảng thuật ngữ")

    def _open_settings(self, group: str = "keys") -> None:
        if self.stack.currentIndex() == SETTINGS:
            self.nav.settings_btn.setChecked(True)
            return
        self._before_settings = self.stack.currentIndex()
        self.settings_view.load_values()
        self.settings_view.show_group(group if isinstance(group, str) else "keys")
        self.stack.setCurrentIndex(SETTINGS)

    def _on_settings_saved(self) -> None:
        self.providers = list_providers()
        self.input.refresh_providers(self.providers)  # newly-keyed providers become selectable
        self.status.set_status("Đã lưu cài đặt", "ok")


def _install_excepthook() -> None:
    """Log unhandled exceptions to storage/vizsup-error.log + show a dialog, so a
    Python error never silently closes the window."""
    import traceback

    from PySide6.QtWidgets import QMessageBox

    log_path = settings.vizsup_storage_dir / "vizsup-error.log"

    def hook(exctype, value, tb):
        text = "".join(traceback.format_exception(exctype, value, tb))
        sys.stderr.write(text)
        try:
            settings.vizsup_storage_dir.mkdir(parents=True, exist_ok=True)
            log_path.write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            QMessageBox.critical(None, "vizsup — lỗi không mong đợi",
                                 f"{exctype.__name__}: {value}\n\nChi tiết đã ghi vào:\n{log_path}")
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = hook


def _install_qt_log_filter() -> None:
    """Drop known-harmless Qt/ffmpeg console spam so real errors stand out."""
    from PySide6.QtCore import qInstallMessageHandler

    noise = ("setpointsize", "moov atom", "invalid data found", "low score",
             "illegal icc", "illegal iid", "env_facs_q", "sbr extensions",
             "could not update timestamps", "expected to read", "ps bits",
             "reserved sbr", "ffmpeg-devel", "skipped samples")

    def handler(_mode, _ctx, message):
        if not any(n in message.lower() for n in noise):
            sys.stderr.write(message + "\n")

    qInstallMessageHandler(handler)


def _install_exit_tracing(app) -> None:
    """Make every way the process can end leave a line in vizsup-debug.log.

    2026-10-01: the window vanished ~2 s into a run with NO trace — no Python
    traceback, nothing in vizsup-error.log, no Windows crash event — and the same
    job ran fine in a QThread outside the GUI. Next time the log says which it was:
    a native crash (faulthandler → vizsup-crash.log, all threads), Ctrl+C / Ctrl+Break
    in the launching console (which a stray copy-shortcut sends), a window close, or
    a normal exit."""
    import atexit
    import faulthandler
    import os
    import signal

    try:
        settings.vizsup_storage_dir.mkdir(parents=True, exist_ok=True)
        crash = open(settings.vizsup_storage_dir / "vizsup-crash.log", "a",  # noqa: SIM115
                     encoding="utf-8")
        crash.write(f"\n=== vizsup start pid={os.getpid()} ===\n")
        crash.flush()
        faulthandler.enable(file=crash, all_threads=True)
        app._crash_log = crash            # keep the file object alive for the process
    except Exception:  # noqa: BLE001 - tracing must never stop the app starting
        pass

    store.debug(f"=== app start pid={os.getpid()} ===")

    def on_signal(signum, _frame):
        store.debug(f"!!! received signal {signal.Signals(signum).name} — quitting "
                    f"(Ctrl+C / Ctrl+Break in the console that launched the app?)")
        app.quit()

    for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)
    # Python runs signal handlers only between bytecodes on the main thread; while
    # Qt's C++ event loop idles none run. A no-op tick lets them fire within 0.5 s.
    from PySide6.QtCore import QTimer

    tick = QTimer(app)
    tick.timeout.connect(lambda: None)
    tick.start(500)
    app.aboutToQuit.connect(lambda: store.debug("app aboutToQuit"))
    atexit.register(lambda: store.debug("process exiting (atexit)"))


def _group_status(job: Job) -> dict | None:
    """``{id, index, total, rendered, approved}`` for a part of a split video, else None."""
    from app.pipeline import split

    g = split.group_info(job)
    if not g:
        return None
    group = split.load_group(settings.vizsup_storage_dir / str(g["id"]))
    if not group:
        return None
    ready = split.readiness(group, settings.vizsup_storage_dir)
    return {"id": g["id"], "index": g.get("index"), "total": len(ready),
            "rendered": sum(r["rendered"] for r in ready),
            "approved": sum(r["approved"] for r in ready)}


def _source_refetchable(job: Job, opts: dict) -> bool:
    """A missing source.mp4 can come back: a link to download (not a split part — its
    source is a cut of the original), or the local file it was copied from."""
    from pathlib import Path

    from app.pipeline import split

    if split.group_info(job):
        return False
    if job.url or opts.get("url"):
        return True
    return bool(opts.get("file")) and Path(opts["file"]).exists()


def _scrolled(page: QWidget) -> QScrollArea:
    """A page keeps its own minimum size and scrolls when the window is smaller. Without
    this the window's minimum is the largest page's (1346 px wide, taller once scaled by
    the display's DPI), so on a smaller screen it could not shrink and ran off the
    display — "phóng to" and the render page looked cut off (2026-10-28 report)."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(page)
    return area


def _default_voice(job, voices: list[str], opts: dict) -> str | None:
    """The dub voice to show first: this project's own pick (render page), the run's
    option, a voice matching the speaker the AI heard (male reviewer → NamMinh), else
    the first one."""
    from app.pipeline import speakers

    from desktop.views.render_view import saved_voice

    for v in (saved_voice(job), opts.get("voice")):
        if v and v in voices:
            return v
    return speakers.voice_for(voices, speakers.speaker_gender(job)) or (voices[0] if voices
                                                                       else None)


def main() -> int:
    from app.winproc import hide_child_consoles

    hide_child_consoles()                     # pythonw / packaged: no black ffmpeg windows
    _install_qt_log_filter()
    app = QApplication(sys.argv)
    app.setApplicationName("vizsup")
    from desktop.qt_vi import ViTranslator

    app._vi = ViTranslator(app)               # keep a reference: Qt does not own it
    app.installTranslator(app._vi)
    _install_excepthook()
    _install_exit_tracing(app)
    load_fonts(app)
    app.setStyleSheet(qss())
    # hand cursor on everything clickable + the hover glow ("hover_glow": false in
    # storage/ui_state.json turns the glow off)
    from desktop.widgets import no_wheel

    no_wheel.install(app)                    # the wheel scrolls; it never changes a setting
    pointer.install(app, glow_enabled=store.load_ui_state().get("hover_glow", True) is not False)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

