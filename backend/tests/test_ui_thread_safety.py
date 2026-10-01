"""Worker-thread results must reach the GUI on the GUI thread.

2026-10-01: the desktop app vanished seconds into every run — a native access
violation at random places (inside subprocess for ffmpeg, in pathlib). Cause: a
worker signal connected to a LAMBDA that touched a QWidget. With no receiver QObject,
Qt runs such a callable on the emitting (worker) thread, and widget access off the
GUI thread corrupts memory. The same pattern sat in the editor's async helper
(Nghe thử / Rút gọn / style preview) and the Settings "Test kết nối" button.
desktop/ui_thread.connect_on_ui routes every such connection through a GUI-thread
QObject; these tests drive the REAL code paths and check the thread each callback
ran on.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
ui_thread = pytest.importorskip("desktop.ui_thread")

from PySide6.QtCore import QObject, QThread, Signal, Slot  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(qapp, until, timeout=5.0):
    end = time.time() + timeout
    while not until() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


class _Worker(QObject):
    text = Signal(str)
    pair = Signal(float, str)
    bare = Signal()

    @Slot()
    def run(self) -> None:
        self.text.emit("x")
        self.pair.emit(0.5, "half")
        self.bare.emit()


def test_connect_on_ui_runs_any_callable_on_the_gui_thread(qapp):
    seen: list = []
    gui = threading.get_ident()
    host = QObject()
    th = QThread()
    w = _Worker()
    w.moveToThread(th)
    ui_thread.connect_on_ui(w.text, lambda s: seen.append(("text", s, threading.get_ident())), host)
    ui_thread.connect_on_ui(w.pair, lambda f, s: seen.append(("pair", (f, s), threading.get_ident())),
                            host)
    ui_thread.connect_on_ui(w.bare, lambda: seen.append(("bare", None, threading.get_ident())), host)
    th.started.connect(w.run)
    th.start()
    _pump(qapp, lambda: len(seen) == 3)
    th.quit()
    th.wait()

    assert [(k, v) for k, v, _t in seen] == [("text", "x"), ("pair", (0.5, "half")),
                                              ("bare", None)]
    assert all(t == gui for _k, _v, t in seen), "a callback ran off the GUI thread"


def test_editor_async_callbacks_run_on_the_gui_thread(qapp):
    """Nghe thử / Rút gọn / style preview hand closures that touch widgets."""
    editor_view = pytest.importorskip("desktop.views.editor_view")
    v = editor_view.EditorView()
    gui = threading.get_ident()
    seen: list = []
    v._run_async(lambda: "result", lambda r: seen.append(("done", r, threading.get_ident())),
                 lambda m: seen.append(("fail", m, threading.get_ident())))
    v._run_async(lambda: 1 / 0, lambda r: None,
                 lambda m: seen.append(("fail", m[:17], threading.get_ident())))
    _pump(qapp, lambda: len(seen) == 2)
    v.shutdown()

    assert sorted(k for k, _r, _t in seen) == ["done", "fail"]
    assert all(t == gui for _k, _r, t in seen)


def test_settings_background_callback_runs_on_the_gui_thread(qapp, tmp_path, monkeypatch):
    """'Test kết nối' passes a lambda that updates a QLabel."""
    sd = pytest.importorskip("desktop.settings_dialog")
    monkeypatch.setattr(sd, "_ENV_PATH", tmp_path / ".env")
    dlg = sd.SettingsDialog()
    gui = threading.get_ident()
    seen: list = []
    dlg._bg(lambda: (True, "ok"), lambda res: seen.append((res, threading.get_ident())))
    _pump(qapp, lambda: bool(seen))
    dlg.close()

    assert seen and seen[0][0] == (True, "ok") and seen[0][1] == gui


def test_mainwindow_run_routes_every_connection_through_the_relay(qapp, monkeypatch):
    main_mod = pytest.importorskip("desktop.main")
    routed: list = []

    class FakeSignal:
        def __init__(self, name):
            self.name = name

        def connect(self, slot):
            pass

    class FakeWorker:
        def __init__(self, job, opts):
            for n in ("log", "stage_started", "finished"):
                setattr(self, n, FakeSignal(n))

        def moveToThread(self, _th):
            pass

        def deleteLater(self):
            pass

        def run_to_gate(self):
            pass

    class FakeThread:
        def __init__(self):
            self.started = FakeSignal("started")
            self.finished = FakeSignal("thread.finished")

        def start(self):
            pass

        def quit(self):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_mod, "PipelineWorker", FakeWorker)
    monkeypatch.setattr(main_mod, "QThread", FakeThread)
    monkeypatch.setattr(main_mod, "connect_on_ui",
                        lambda sig, fn, owner: routed.append(sig.name) or QObject())

    class Host:
        job = None
        _on_finished = staticmethod(lambda: None)

    h = Host()
    main_mod.MainWindow._run(h, "run_to_gate", {},
                             {"log": lambda m: None, "stage_started": lambda s: None})
    assert sorted(routed) == ["log", "stage_started"]
