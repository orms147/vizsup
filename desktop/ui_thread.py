"""Deliver worker-thread signals to plain callables ON THE GUI THREAD.

The rule: QWidgets may only be touched from the GUI thread. When a signal emitted
on a worker QThread is connected to a lambda / closure / plain function, Qt has no
receiver QObject to queue the call to, so it runs the callable on the WORKER thread.
If that callable touches a widget, memory gets corrupted and the app dies later at a
random place — 2026-10-01 the desktop app vanished seconds into every run, with an
access violation inside subprocess (ffmpeg) or pathlib, traced to exactly this.

``connect_on_ui(signal, fn, owner)`` routes the call through a UiCall — a QObject
living on the GUI thread — so Qt's auto-connection queues it there, whatever ``fn``
is. Use it for every connection from a worker signal to a non-QObject callable.

Background calls (``BgRunner``, the editor's ``_run_async``, Settings' ``_bg``) run on DAEMON
``threading.Thread``s, not QThreads. A QThread still inside a network call when the window
closes (the daily model-catalog check: two 10 s timeouts; the AI title suggestion; a
connection test) could not be joined in the 2 s the close allows, and Qt then aborted the
process with "QThread: Destroyed while thread is still running". A daemon thread cannot do
that and never delays the exit; its result is simply dropped (``guarded``).
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal


class UiCall(QObject):
    """Runs ``fn`` on the thread this object lives in — the GUI thread when created
    there. ``call`` is a bound method of a GUI-thread QObject, which is what makes
    Qt queue cross-thread emissions onto the GUI thread."""

    def __init__(self, fn, parent: QObject) -> None:
        super().__init__(parent)
        self._fn = fn

    def call(self, *args) -> None:
        self._fn(*args)


def connect_on_ui(signal, fn, owner: QObject) -> UiCall:
    """Connect ``signal`` so ``fn`` runs on ``owner``'s (GUI) thread. The relay is
    parented to ``owner`` (lives as long as it) and returned so callers can drop it."""
    relay = UiCall(fn, owner)
    signal.connect(relay.call)
    return relay


def spawn(run: Callable[[], None], keep: list, pair) -> threading.Thread:
    """Start ``run`` on a daemon thread. ``pair`` (whatever must stay referenced — the task
    QObject) sits in ``keep`` until the call ends."""
    def target() -> None:
        try:
            run()
        finally:
            try:
                keep.remove(pair)
            except ValueError:
                pass

    th = threading.Thread(target=target, name="vizsup-bg", daemon=True)
    pair = (th, pair)
    keep.append(pair)
    th.start()
    return th


def guarded(fn: Callable, alive: Callable[[], bool]) -> Callable:
    """``fn`` that does nothing once ``alive()`` is False (the window is closing)."""
    def call(*args):
        if alive():
            return fn(*args)
        return None
    return call


def emit_quietly(signal, value) -> None:
    """Emit from a worker thread; the receiver's window may already be gone."""
    try:
        signal.emit(value)
    except RuntimeError:
        pass


class _BgTask(QObject):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            emit_quietly(self.failed, f"{type(exc).__name__}: {exc}")
            return
        emit_quietly(self.done, result)


class BgRunner:
    """Run short blocking calls on daemon threads; results come back on the GUI thread (via
    connect_on_ui) unless ``shutdown`` was called — then they are dropped and the process can
    exit at once, whatever a thread is still waiting for."""

    def __init__(self, owner: QObject) -> None:
        self._owner = owner
        self._tasks: list = []
        self._closed = False

    def run(self, fn, on_done, on_fail=None) -> None:
        task = _BgTask(fn)
        connect_on_ui(task.done, guarded(on_done, lambda: not self._closed), self._owner)
        if on_fail:
            connect_on_ui(task.failed, guarded(on_fail, lambda: not self._closed), self._owner)
        spawn(task.run, self._tasks, task)

    def shutdown(self) -> None:
        self._closed = True
