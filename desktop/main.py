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
    QLabel,
    QMainWindow,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from desktop import paths  # noqa: F401
from desktop import store
from desktop.cuemodel import fmt_time
from desktop.ui_thread import connect_on_ui
from desktop.settings_dialog import SettingsView
from desktop.theme import load_fonts, qss
from desktop.views.editor_view import EditorView
from desktop.views.input_view import InputView
from desktop.views.render_view import RenderView
from desktop.widgets import pointer
from desktop.widgets.chrome import AppNav, StatusBar, Toast
from desktop.worker import PipelineWorker

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.providers.registry import get_tts, list_providers  # noqa: E402

INPUT, EDITOR, RENDER, SETTINGS = 0, 1, 2, 3


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("vizsup")
        self.resize(1380, 880)
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
        self.stack.addWidget(self.input)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.render)
        self.settings_view = SettingsView()
        self.stack.addWidget(self.settings_view)
        self._before_settings = INPUT
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
        self.render.backRequested.connect(lambda: self.stack.setCurrentIndex(EDITOR))
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
        self.steps.clicked.connect(self._goto_step)
        self.stack.currentChanged.connect(self._on_page)
        self._on_page(INPUT)
        self._tray = self._make_tray()
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

    def _show_shortcuts(self) -> None:
        from desktop.widgets.shortcuts import ShortcutsDialog

        ShortcutsDialog(self).exec()

    def _on_page(self, idx: int) -> None:
        if idx != SETTINGS:
            self.steps.set_active(idx)
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

        self.render.show_done(path)
        if self.job is not None:
            self.render.set_group(_group_status(self.job))
            self.editor.reload_fit_report()             # F-10: late dub lines
        quick = Path(path).name == "preview.mp4"
        self._notify("Bản xem nhanh đã sẵn sàng" if quick else "Đã dựng xong video",
                     Path(path).name)

    def _on_render_failed(self, msg: str) -> None:
        self.render.show_error(msg)
        if not msg.strip().startswith("Đã hủy"):
            self._notify("Chưa dựng được video", msg[:160], "err")

    def _on_gate(self) -> None:
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
        except Exception as exc:  # noqa: BLE001 - never let editor-load kill the app
            import traceback as _tb
            store.debug(f"on_gate FAILED {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
            self.input.show_error(f"Lỗi mở trình sửa: {type(exc).__name__}: {exc}")

    def _resume(self, job_id: str) -> None:
        queued = self._queue_job is not None
        if queued and job_id == self._queue_job.id:
            self.status.set_status("Video này đang xử lý trong hàng đợi.", "warn")
            return
        # don't hijack self.job/self.opts from a running worker — a QUEUED run has its
        # own job, so a finished project can still be opened for editing meanwhile
        if (self._busy() and not queued) or self._render_busy():
            self.status.setText("● Đang bận — đợi tác vụ hiện tại xong rồi mở dự án khác.")
            return
        loaded = store.load_job(job_id)
        if not loaded:
            self.status.set_status("Không tìm thấy dự án này (thư mục đã bị xóa?).", "warn")
            self.input.refresh_recent()
            return
        job, opts = loaded
        if job.vi_srt.exists():
            self.job, self.opts = job, opts
            self._on_gate()  # reopen straight at the edit gate (no re-download/ASR)
        elif opts and self._busy():
            self.status.set_status("Đợi hàng đợi xong rồi tiếp tục dự án đang dở.", "warn")
        elif opts:
            # E-11: an unfinished run — continue from the first missing step
            self._run_gate(job, opts, f"▶ Tiếp tục dự án đang dở (job {job.id})")

    def _delete_job(self, job_id: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        if self._any_busy():
            self.status.setText("● Đang bận — không xóa dự án trong lúc đang chạy.")
            return
        if self.job is not None and job_id == self.job.id:
            # deleting the open job would make the next autosave write into a
            # removed directory and crash the save timer
            QMessageBox.information(self, "Xóa dự án",
                                    "Dự án này đang mở — quay lại màn hình đầu trước khi xóa.")
            return
        if QMessageBox.question(self, "Xóa dự án", f"Xóa toàn bộ tệp của dự án này?\n{job_id}") \
                == QMessageBox.StandardButton.Yes:
            store.delete_job(job_id)
            self.input.refresh_recent()

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
                voices = get_tts(tts_name).voices()
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
                    return
                approval.approve(self.job, cues=len(self.editor.cues))
                self.editor.refresh_approval()
                self._learn_from_approval()
                self.status.set_status("Đã duyệt phụ đề — chọn âm thanh rồi dựng video.", "ok")
            self.render.set_voices(voices)
            self.render.select_voice(_default_voice(self.job, voices, self.opts))
            self._sync_render_page()
            self.render.set_preview(self.job.source_video, self.editor.cues)  # show VI caption bar in step 3
            self.stack.setCurrentIndex(RENDER)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"● Lỗi: {exc}")

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

    def _delete_group(self, gid: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        from app.pipeline import split

        if self._any_busy():
            self.status.setText("● Đang bận — không xóa dự án trong lúc đang chạy.")
            return
        group = split.load_group(settings.vizsup_storage_dir / gid) or {"parts": []}
        ids = [p["id"] for p in group["parts"]]
        if self.job is not None and self.job.id in ids:
            QMessageBox.information(self, "Xóa video", "Một phần của video này đang mở — quay lại "
                                    "màn hình đầu trước khi xóa.")
            return
        if QMessageBox.question(self, "Xóa video",
                                f"Xóa video gốc và cả {len(ids)} phần (phụ đề, bản dựng)?") \
                == QMessageBox.StandardButton.Yes:
            self._queue = [o for o in self._queue if o.get("_job_id") not in ids]
            for jid in [*ids, gid]:
                store.delete_job(jid)
            self.input.refresh_recent()

    def _on_queued_gate(self, job: Job, opts: dict) -> None:
        self._queue_ok += 1
        self.input.set_done()
        store.save_job(job, opts, "await_edit")
        self.input.refresh_recent()
        title = (job.metadata.get("title") or job.id).split("#")[0].strip()
        self._notify("Phụ đề đã sẵn sàng", f"{title} — mở ở “Tiếp tục dự án”")

    def _on_queued_failed(self, job: Job, opts: dict, msg: str) -> None:
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

    def _on_finished(self) -> None:
        # gate failed/cancelled → re-enable Start but KEEP the log/error visible
        self.input.idle()
        # single unlock point for EVERY render exit path (done/failed/cancelled) —
        # per-handler unlocks would risk a soft-locked app on a missed path; a render in
        # the second slot unlocks itself (_on_render_finished)
        if not self._render_busy():
            self._set_render_locked(False)
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

    def closeEvent(self, e) -> None:
        store.debug(f"closeEvent (window closed) busy={self._busy()}")
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
            self.render.shutdown()  # voice sample synthesis
            self.settings_view.shutdown()  # connection tests / model list
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
    pointer.install(app, glow_enabled=store.load_ui_state().get("hover_glow", True) is not False)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

