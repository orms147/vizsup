"""Closing the app while a background call is still blocked (the daily model-catalog check:
two 10 s timeouts; the AI title suggestion; a connection test) must neither abort the process
("QThread: Destroyed while thread is still running", exit code != 0) nor delay the exit.

The reproduction runs in a SUBPROCESS: the abort kills the interpreter, so it can only be seen
from outside. Before the fix: BgRunner and the editor's _run_async exited with a crash code
after the 2 s join; Settings' _bg took 3.5 s. Calls now run on daemon threads."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

SCRIPT = textwrap.dedent('''
    import os, sys, tempfile, threading, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {backend!r}); sys.path.insert(0, {root!r})
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    block, started = threading.Event(), threading.Event()
    def slow():                       # a request that hangs far longer than the 2 s join
        started.set(); block.wait(60); return "late"
    t0 = time.time()
    kind = sys.argv[1]
    if kind == "bgrunner":
        from desktop.ui_thread import BgRunner
        owner = QObject(); bg = BgRunner(owner)
        bg.run(slow, lambda r: print("DELIVERED"))
        started.wait(5); t0 = time.time(); bg.shutdown(); del bg; owner.deleteLater(); del owner
    elif kind == "editor":
        from desktop.views.editor_view import EditorView
        v = EditorView(); v._run_async(slow, lambda r: print("DELIVERED"))
        started.wait(5); t0 = time.time(); v.shutdown(); v.deleteLater(); del v
    else:
        from pathlib import Path
        import desktop.settings_dialog as sd
        sd._ENV_PATH = Path(tempfile.mkdtemp()) / ".env"
        s = sd.SettingsView(); s._bg(slow, lambda r: print("DELIVERED"))
        started.wait(5); t0 = time.time(); s.shutdown(); s.deleteLater(); del s
    app.processEvents()
    print("closing %.2f" % (time.time() - t0), flush=True)
    sys.exit(0)
''')


@pytest.mark.parametrize("kind", ["bgrunner", "editor", "settings"])
def test_closing_with_a_blocked_background_call_exits_cleanly_and_at_once(kind, tmp_path):
    script = tmp_path / "close_app.py"
    script.write_text(SCRIPT.format(backend=str(ROOT / "backend"), root=str(ROOT)), encoding="utf-8")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-I", str(script), kind], capture_output=True, text=True,
                       timeout=90)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out                       # no abort at interpreter exit
    assert "Destroyed while thread is still running" not in out
    assert "DELIVERED" not in out                       # the late result is dropped, not shown
    closing = float(out.split("closing ")[1].split()[0])
    assert closing < 1.5, out                           # shutdown does not wait for the call
    assert time.time() - t0 < 30


# --- in-process behaviour ----------------------------------------------------------------------

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop import ui_thread  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump(qapp, until, timeout=5.0):
    end = time.time() + timeout
    while not until() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


def test_bgrunner_still_delivers_results_and_failures_and_releases_its_refs(qapp):
    import threading

    owner = QObject()
    bg = ui_thread.BgRunner(owner)
    seen: list = []
    gui = threading.get_ident()
    bg.run(lambda: 41 + 1, lambda r: seen.append(("done", r, threading.get_ident())),
           lambda m: seen.append(("fail", m, threading.get_ident())))
    bg.run(lambda: 1 / 0, lambda r: None, lambda m: seen.append(("fail", m[:17], threading.get_ident())))
    _pump(qapp, lambda: len(seen) == 2)
    assert sorted((k, v) for k, v, _t in seen) == [("done", 42), ("fail", "ZeroDivisionError")]
    assert all(t == gui for _k, _v, t in seen)           # still on the GUI thread
    _pump(qapp, lambda: not bg._tasks)
    assert bg._tasks == []                               # nothing kept after the calls end


def test_results_after_shutdown_are_dropped(qapp):
    import threading

    owner = QObject()
    bg = ui_thread.BgRunner(owner)
    release, seen = threading.Event(), []
    bg.run(lambda: release.wait(5) and "late", lambda r: seen.append(r))
    bg.shutdown()                                        # returns at once, whatever is running
    release.set()
    _pump(qapp, lambda: not bg._tasks)
    qapp.processEvents()
    assert seen == []


def test_guarded_and_emit_quietly():
    calls = []
    live = {"on": True}
    f = ui_thread.guarded(lambda x: calls.append(x) or x, lambda: live["on"])
    assert f(1) == 1
    live["on"] = False
    assert f(2) is None and calls == [1]

    class Dead:
        def emit(self, _v):
            raise RuntimeError("Signal source has been deleted")

    ui_thread.emit_quietly(Dead(), 1)                    # must not raise in the worker thread


def test_a_deleted_task_object_does_not_kill_the_worker_thread(qapp):
    """The window (and with it the task QObject's C++ side) can be gone when a late call ends."""
    from shiboken6 import delete

    task = ui_thread._BgTask(lambda: "x")
    delete(task)
    task.run()                                           # emit on a deleted object: swallowed
