"""E-13 Settings upkeep: yt-dlp update, cookie age, and the new Settings groups.

pip is never really run here: subprocess.run is monkeypatched for every update test.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import tools

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DAY = 86400


def _cp(args, rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr=stderr)


def _fake_run(monkeypatch, versions: list[str], pip_rc: int = 0, pip_stderr: str = ""):
    """Stand-in for subprocess.run. Version probes (``python -c …``) pop the next value
    from ``versions``; the pip call returns ``pip_rc``. Returns the argv list it saw."""
    seen: list[list[str]] = []
    queue = iter(versions)

    def fake_run(args, **_kwargs):
        seen.append(list(args))
        if "-c" in args:
            return _cp(args, 0, stdout=next(queue) + "\n")
        return _cp(args, pip_rc, stderr=pip_stderr)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    return seen


# --- cookie age -----------------------------------------------------------------

def test_cookie_age_is_none_without_a_file(tmp_path):
    assert tools.cookie_age_days(None) is None
    assert tools.cookie_age_days("") is None
    assert tools.cookie_age_days(str(tmp_path / "missing.txt")) is None


def test_cookie_age_counts_days_since_the_file_was_written(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    thirty_days_ago = time.time() - 30 * DAY
    os.utime(cookies, (thirty_days_ago, thirty_days_ago))
    assert tools.cookie_age_days(str(cookies)) == pytest.approx(30.0, abs=0.01)


# --- yt-dlp update --------------------------------------------------------------

def test_update_ytdlp_reports_old_and_new_version(monkeypatch):
    seen = _fake_run(monkeypatch, versions=["2026.6.9", "2026.10.1"])
    ok, msg = tools.update_ytdlp()
    assert ok is True
    assert "2026.6.9" in msg and "2026.10.1" in msg
    assert [c for c in seen if "pip" in c] == [
        [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]]


def test_update_ytdlp_failure_returns_false_with_the_pip_tail(monkeypatch):
    _fake_run(monkeypatch, versions=["2026.6.9"], pip_rc=1,
              pip_stderr="ERROR: Could not find a version\nline two\nline three\nline four")
    ok, msg = tools.update_ytdlp()
    assert ok is False
    assert "2026.6.9" in msg
    assert "line three" in msg and "line four" in msg
    assert "ERROR: Could not find" not in msg  # only the last 3 lines are shown


def test_update_ytdlp_never_raises_when_pip_times_out(monkeypatch):
    def fake_run(args, **_kwargs):
        if "-c" in args:
            return _cp(args, 0, stdout="2026.6.9\n")
        raise subprocess.TimeoutExpired(args, 300)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    ok, msg = tools.update_ytdlp()
    assert ok is False
    assert "2026.6.9" in msg


# --- Settings page wiring (offscreen Qt) ---------------------------------------

@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_settings_page_has_six_groups_ending_in_the_check_page(qapp):
    sd = pytest.importorskip("desktop.settings_dialog")
    view = sd.SettingsView()
    assert len(view._nav_btns) == 6
    assert sd.GROUPS[-1][0] == "check" and sd.GROUPS[-2][0] == "accounts"


def test_check_group_can_be_shown_by_key(qapp):
    sd = pytest.importorskip("desktop.settings_dialog")
    view = sd.SettingsView()
    view.show_group("check")
    assert view._stack.currentIndex() == 5
    assert view._nav_btns[5].isChecked()


def test_update_edge_tts_runs_pip_for_edge_tts(monkeypatch):
    seen = _fake_run(monkeypatch, versions=["7.2.8", "7.3.1"])
    ok, msg = tools.update_edge_tts()
    assert ok and "7.2.8 → 7.3.1" in msg
    assert [c for c in seen if "pip" in c] == [
        [sys.executable, "-m", "pip", "install", "-U", "edge-tts"]]


def test_an_edge_tts_403_becomes_advice():
    from app.pipeline import tts

    class WSServerHandshakeError(Exception):
        pass

    out = tts._explain(WSServerHandshakeError("403, message='Invalid response status'"))
    assert isinstance(out, RuntimeError) and "Cập nhật edge-tts" in str(out)
    other = ValueError("boom")
    assert tts._explain(other) is other
