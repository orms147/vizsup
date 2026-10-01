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
"""
from __future__ import annotations

from PySide6.QtCore import QObject


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
