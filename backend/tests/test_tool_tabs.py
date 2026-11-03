"""«Tạo audio» and «Video audio» are tabs of the main window: a build goes on while another tab is
open, and the start page stays free for a translation."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VIZSUP_NO_UPDATE_CHECK", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from desktop import main as m

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    w = m.MainWindow()
    w.remote.timer.stop()
    w._notify = lambda *a, **k: None
    yield w
    w._closing = True
    w.close()


class Slow:
    name = "slow"
    parallel = 1

    def available(self):
        return True

    def voices(self):
        return ["v"]

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        import subprocess

        from app.ffmpegutil import ffmpeg_bin

        time.sleep(0.4)
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=0.8", str(out_path)], check=True,
                       capture_output=True)
        return out_path


def test_the_tabs_switch_and_a_build_runs_on_while_another_tab_is_open(win, qapp, monkeypatch):
    from app.providers import registry
    from desktop import main as m

    monkeypatch.setitem(registry.TTS_PROVIDERS, "slow", Slow())
    monkeypatch.setattr(win, "_start_tts", lambda: "slow")
    win.audio_page._tts_now = lambda: "slow"
    win.nav.audio_btn.click()
    assert win.stack.currentIndex() == m.AUDIO and win.nav.audio_btn.isChecked()
    win.audio_page.script.setPlainText("Câu thứ nhất của kịch bản dài. Câu thứ hai cũng dài đó.")
    win.audio_page.go_btn.click()
    assert win.audio_page.is_busy() and "đang chạy" in win.nav.audio_btn.toolTip()
    win.nav.steps.clicked.emit(m.INPUT)                       # back to «Bắt đầu» meanwhile
    assert win.stack.currentIndex() == m.INPUT and win.audio_page.is_busy()
    win.nav.video_btn.click()
    assert win.stack.currentIndex() == m.SCRIPTVIDEO and not win.nav.audio_btn.isChecked()
    win.nav.video_btn.click()                                  # a second click leaves the tool
    assert win.stack.currentIndex() == m.INPUT
    end = time.time() + 40
    while time.time() < end and win.audio_page.is_busy():
        qapp.processEvents()
        time.sleep(0.05)
    assert not win.audio_page.is_busy() and win.audio_page.result_path.exists()
    assert "đang chạy" not in win.nav.audio_btn.toolTip()
    win.audio_page.use_btn.click()                             # the mp3 goes to the video page
    assert win.stack.currentIndex() == m.SCRIPTVIDEO and win.script_page._from_audio()
