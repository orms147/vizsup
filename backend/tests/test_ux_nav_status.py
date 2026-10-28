"""App chrome (UX audit X1, S1, X2): the status bar no longer keeps the editor's line on other
pages, the step rail is dimmed (and "Cài đặt" lit) while Thư viện / Cài đặt is open, and the
whole-app keys work: Ctrl+L, Ctrl+, , Ctrl+1/2/3, Esc out of Thư viện / Cài đặt."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture()
def win(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from desktop import main as main_mod

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    app = QApplication.instance() or QApplication([])
    w = main_mod.MainWindow()
    w.show()
    w.activateWindow()
    app.processEvents()
    yield w
    w.close()


def _status(w) -> str:
    return w.status.text.text()


def _lit(w) -> list[str]:
    return [b.objectName() for b in w.steps._btns]


def test_editor_status_line_does_not_follow_you_to_other_pages(win):
    from desktop import main as m

    win.status.setText("● " + m.EDITOR_IDLE)               # what the gate sets
    win.stack.setCurrentIndex(m.RENDER)
    assert _status(win) == m.PAGE_IDLE[m.RENDER]
    win.stack.setCurrentIndex(m.SETTINGS)
    assert _status(win) == m.PAGE_IDLE[m.SETTINGS]
    win.stack.setCurrentIndex(m.LIBRARY)
    assert _status(win) == m.PAGE_IDLE[m.LIBRARY]
    win.stack.setCurrentIndex(m.EDITOR)
    assert _status(win) == m.EDITOR_IDLE


def test_a_message_or_progress_line_is_never_replaced(win):
    from desktop import main as m

    win.status.set_status("Dựng hàng loạt 2/5 · 40%", "busy")
    for page in (m.LIBRARY, m.SETTINGS, m.INPUT):
        win.stack.setCurrentIndex(page)
        assert _status(win) == "Dựng hàng loạt 2/5 · 40%"


def test_step_rail_is_dimmed_on_library_and_settings(win):
    from desktop import main as m

    win.stack.setCurrentIndex(m.RENDER)
    assert _lit(win) == ["stepDone", "stepDone", "stepActive"]
    win.stack.setCurrentIndex(m.SETTINGS)
    assert "stepActive" not in _lit(win) and _lit(win)[:2] == ["stepDone", "stepDone"]
    assert win.nav.settings_btn.isChecked() and not win.nav.library_btn.isChecked()
    win.stack.setCurrentIndex(m.LIBRARY)
    assert "stepActive" not in _lit(win)
    assert win.nav.library_btn.isChecked() and not win.nav.settings_btn.isChecked()
    win.stack.setCurrentIndex(m.RENDER)                      # back: the step is lit again
    assert _lit(win) == ["stepDone", "stepDone", "stepActive"]
    assert not win.nav.settings_btn.isChecked()


def test_clicking_the_open_page_button_again_keeps_it_lit(win):
    from desktop import main as m

    win.nav.settings_btn.click()
    assert win.stack.currentIndex() == m.SETTINGS and win.nav.settings_btn.isChecked()
    win.nav.settings_btn.click()                              # already there
    assert win.stack.currentIndex() == m.SETTINGS and win.nav.settings_btn.isChecked()
    win.nav.library_btn.click()
    win.nav.library_btn.click()
    assert win.stack.currentIndex() == m.LIBRARY and win.nav.library_btn.isChecked()


def test_app_keys_open_and_leave_library_and_settings(win):
    from desktop import main as m

    QTest.keyClick(win, Qt.Key_L, Qt.ControlModifier)
    assert win.stack.currentIndex() == m.LIBRARY
    QTest.keyClick(win, Qt.Key_Escape)
    assert win.stack.currentIndex() == m.INPUT
    QTest.keyClick(win, Qt.Key_Comma, Qt.ControlModifier)
    assert win.stack.currentIndex() == m.SETTINGS
    QTest.keyClick(win, Qt.Key_Escape)
    assert win.stack.currentIndex() == m.INPUT
    assert not win._esc_back.isEnabled()                      # Esc is the editor's again
    win.stack.setCurrentIndex(m.LIBRARY)
    QTest.keyClick(win, Qt.Key_1, Qt.ControlModifier)
    assert win.stack.currentIndex() == m.INPUT
