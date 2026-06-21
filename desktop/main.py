"""vizsup desktop app entry. Run:  python -m desktop.main  (from repo root).

MainWindow = custom toolbar (logo + step indicator + Settings + Approve&Render)
over a QStackedWidget of the 3 views, plus a status bar. The pipeline runs in a
QThread via PipelineWorker; the UI stays responsive and cancellable.
"""
from __future__ import annotations

import sys
import uuid

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import paths  # noqa: F401
from desktop import store
from desktop.settings_dialog import SettingsDialog
from desktop.theme import C, load_fonts, qss
from desktop.views.editor_view import EditorView
from desktop.views.input_view import InputView
from desktop.views.render_view import RenderView
from desktop.widgets.flow_steps import FlowSteps
from desktop.worker import PipelineWorker

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.providers.registry import get_tts, list_providers  # noqa: E402

INPUT, EDITOR, RENDER = 0, 1, 2


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

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._toolbar())

        self.stack = QStackedWidget()
        self.input = InputView(self.providers)
        self.editor = EditorView()
        self.render = RenderView()
        self.stack.addWidget(self.input)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.render)
        v.addWidget(self.stack, 1)
        v.addWidget(self._statusbar())

        # wiring
        self.input.startRequested.connect(self._start_gate)
        self.input.cancelRequested.connect(self._cancel)
        self.input.resumeRequested.connect(self._resume)
        self.input.deleteRequested.connect(self._delete_job)
        self.editor.dirtyChanged.connect(self._on_dirty)
        self.render.renderRequested.connect(self._start_render)
        self.render.backRequested.connect(lambda: self.stack.setCurrentIndex(EDITOR))
        self.editor.previewStyleRequested.connect(self._preview_style)
        self.steps.clicked.connect(self._goto_step)
        self.stack.currentChanged.connect(self._on_page)
        self._on_page(INPUT)

    # --- chrome --------------------------------------------------------------
    def _toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(54)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(12)
        logo = QLabel("◢ vizsup")
        logo.setStyleSheet(f"color:{C['text2']};font-weight:600;font-size:14px;")
        self.steps = FlowSteps()
        h.addWidget(logo)
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{C['line3']};")
        h.addWidget(sep)
        h.addWidget(self.steps)
        h.addStretch(1)
        self.saved_lbl = QLabel("")
        self.saved_lbl.setObjectName("muted")
        h.addWidget(self.saved_lbl)
        settings_btn = QPushButton("⚙  Cài đặt")
        settings_btn.setObjectName("ghost")
        settings_btn.clicked.connect(self._open_settings)
        h.addWidget(settings_btn)
        self.approve_btn = QPushButton("🛡  Duyệt & Dựng video")
        self.approve_btn.setObjectName("primary")
        self.approve_btn.clicked.connect(self._approve)
        h.addWidget(self.approve_btn)
        return bar

    def _statusbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("statusbar")
        bar.setFixedHeight(28)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        self.status = QLabel("● Sẵn sàng")
        self.status.setStyleSheet(f"color:{C['muted2']};font-size:11px;")
        h.addWidget(self.status)
        h.addStretch(1)
        return bar

    def _on_page(self, idx: int) -> None:
        self.steps.set_active(idx)
        self.approve_btn.setVisible(idx == EDITOR)

    # --- gate run ------------------------------------------------------------
    def _start_gate(self, opts: dict) -> None:
        if self._busy():
            return
        self.opts = opts
        jid = uuid.uuid4().hex[:12]
        self.job = Job(id=jid, work_dir=settings.vizsup_storage_dir / jid, url=opts.get("url"))
        store.save_job(self.job, opts, "running")
        store.debug(f"=== _start_gate job={jid} ===")
        self.input.set_busy(True)
        self.input.append_log(f"▶ Bắt đầu (job {jid})")
        self._run("run_to_gate", opts, {
            "stage_started": lambda s: self.input.append_log(f"• {s}"),
            "progress": self.input.set_progress,
            "log": self.input.append_log,
            "failed": self.input.show_error,
            "gate_reached": self._on_gate,
        })

    def _on_gate(self) -> None:
        store.debug("on_gate: editor.load begin")
        try:
            self.editor.load(self.job)
            self.editor.set_context(self.opts)  # tts/voice/translator for preview & shorten
            store.debug("on_gate: editor.load done")
            store.save_job(self.job, self.opts, "await_edit")  # title now known → resumable
            self.stack.setCurrentIndex(EDITOR)
            self.status.setText("● Sửa phụ đề — duyệt khi xong")
            store.debug("on_gate: switched to editor")
        except Exception as exc:  # noqa: BLE001 - never let editor-load kill the app
            import traceback as _tb
            store.debug(f"on_gate FAILED {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
            self.input.show_error(f"Lỗi mở trình sửa: {type(exc).__name__}: {exc}")

    def _resume(self, job_id: str) -> None:
        loaded = store.load_job(job_id)
        if not loaded:
            return
        self.job, self.opts = loaded
        if self.job.vi_srt.exists():
            self._on_gate()  # reopen straight at the edit gate (no re-download/ASR)

    def _delete_job(self, job_id: str) -> None:
        from PySide6.QtWidgets import QMessageBox

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
        try:
            self.editor.save_now()
            tts_name = self.opts.get("tts") or "edge"
            try:
                voices = get_tts(tts_name).voices()
            except Exception:  # noqa: BLE001
                voices = []
            self.render.set_voices(voices)
            self.render.set_preview(self.job.source_video, self.editor.cues)  # show VI caption bar in step 3
            self.stack.setCurrentIndex(RENDER)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"● Lỗi: {exc}")

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
        from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Xem thử kiểu phụ đề")
        lay = QVBoxLayout(dlg)
        lbl = QLabel()
        pm = QPixmap(str(path))
        if not pm.isNull():
            lbl.setPixmap(pm.scaledToHeight(760, Qt.SmoothTransformation))
        lay.addWidget(lbl)
        dlg.exec()

    def _start_render(self, ropts: dict) -> None:
        if self._busy():
            return
        merged = {
            "tts": self.opts.get("tts") or "edge",
            "style": self.editor.get_style(),
            "font": self.editor.get_font(),
            "size": self.editor.get_size_px(),
            **ropts,
        }
        self._run("run_render", merged, {
            "stage_started": lambda s: self.render.append_log(f"• {s}"),
            "progress": self.render.set_progress,
            "log": self.render.append_log,
            "failed": self.render.show_error,
            "render_done": self.render.show_done,
        })

    # --- worker plumbing -----------------------------------------------------
    def _run(self, method: str, opts: dict, connections: dict) -> None:
        """Build worker+thread, connect ALL signals BEFORE start (no emit races),
        then start. One run at a time (callers guard with _busy())."""
        self._thread = QThread()
        self._worker = PipelineWorker(self.job, opts)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(getattr(self._worker, method))
        for name, slot in connections.items():
            getattr(self._worker, name).connect(slot)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _busy(self) -> bool:
        try:
            return bool(self._thread and self._thread.isRunning())
        except RuntimeError:  # C++ thread object already deleted
            return False

    def _on_finished(self) -> None:
        # gate failed/cancelled → re-enable Start but KEEP the log/error visible
        if self.stack.currentIndex() == INPUT:
            self.input.start.setEnabled(True)

    def _cancel(self) -> None:
        # cooperative: flag it and keep the UI in a "cancelling" state; the actual
        # reset happens on the worker's failed/finished signal (cancel is only
        # honored between stages, so the current stage runs to completion).
        if self._busy() and self._worker:
            self._worker.cancel()
            self.input.status.setText("Đang hủy… (đợi bước hiện tại kết thúc)")
            self.input.append_log("⏳ Đang hủy…")

    def closeEvent(self, e) -> None:
        # never let Python tear down a still-running QThread (hard crash) — cancel + join
        try:
            if self._thread and self._thread.isRunning():
                if self._worker:
                    self._worker.cancel()
                self._thread.quit()
                if not self._thread.wait(5000):
                    self._thread.terminate()
                    self._thread.wait()
        except RuntimeError:
            pass
        try:
            self.editor.shutdown()  # join any preview/shorten task threads
        except Exception:  # noqa: BLE001
            pass
        e.accept()

    def _on_dirty(self, dirty: bool) -> None:
        self.saved_lbl.setText("● Đang lưu…" if dirty else "✓ Đã lưu nháp")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.providers = list_providers()
            self.input.refresh_providers(self.providers)  # newly-keyed providers become selectable
            self.status.setText("● Đã lưu cài đặt")


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


def main() -> int:
    _install_qt_log_filter()
    app = QApplication(sys.argv)
    app.setApplicationName("vizsup")
    _install_excepthook()
    load_fonts(app)
    app.setStyleSheet(qss())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
