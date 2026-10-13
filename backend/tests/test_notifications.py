"""E-12 follow-up: notices reach the user whether or not vizsup is in front — a
Windows notification when it isn't, an in-app toast when it is."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Tray:
    def __init__(self):
        self.shown = []

    def showMessage(self, title, msg, icon, ms):  # noqa: N802 - Qt API
        self.shown.append((title, msg, icon))


def test_toast_in_front_tray_behind(qapp, monkeypatch):
    from desktop import main as main_mod

    win = main_mod.MainWindow()
    tray = _Tray()
    win._tray = tray
    monkeypatch.setattr(win, "isActiveWindow", lambda: True)
    win._notify("Đã dựng xong video", "output.mp4")
    assert tray.shown == [] and win._toast.isVisibleTo(win)
    assert win._toast.title.text() == "Đã dựng xong video"

    win._toast.hide()
    monkeypatch.setattr(win, "isActiveWindow", lambda: False)
    win._notify("Chưa dựng được video", "ffmpeg lỗi", "err")
    assert tray.shown[0][0] == "Chưa dựng được video"
    assert tray.shown[0][2] == main_mod.QSystemTrayIcon.MessageIcon.Warning
    assert not win._toast.isVisibleTo(win)


def test_toast_without_a_tray_and_click_dismisses(qapp):
    from desktop import main as main_mod

    win = main_mod.MainWindow()
    win._tray = None                                  # no system tray on this desktop
    win._notify("Phụ đề đã sẵn sàng", "39 câu · 2 câu cần xem lại")
    assert win._toast.isVisibleTo(win)
    win._toast.mousePressEvent(None)
    assert not win._toast.isVisibleTo(win)
