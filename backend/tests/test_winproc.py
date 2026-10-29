"""Child programs get no console window when the app has none (app/winproc.py): started
with pythonw.exe, every ffmpeg / ffprobe call flashed a black window during a download
(2026-10-29). Runs in a subprocess so the patch never leaks into the test session."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

PROBE = r"""
import subprocess, sys
sys.path.insert(0, {backend!r})
from app import winproc
seen = []
orig = subprocess.Popen._execute_child
def spy(self, args, *rest):
    seen.append(rest[{idx}])          # creationflags
    return orig(self, args, *rest)
subprocess.Popen._execute_child = spy
assert winproc.hide_child_consoles(force={force}) is {force}
subprocess.run([sys.executable, "-c", "pass"], check=True)
subprocess.run([sys.executable, "-c", "pass"], check=True,
               creationflags=subprocess.CREATE_NEW_CONSOLE)
print(seen)
"""


def _flags_index() -> int:
    import inspect

    params = list(inspect.signature(subprocess.Popen._execute_child).parameters)
    return params.index("creationflags") - 2          # minus self and args


@pytest.mark.skipif(sys.platform != "win32", reason="Windows consoles")
def test_children_get_no_window_unless_they_ask_for_a_console():
    code = PROBE.format(backend=str(BACKEND), idx=_flags_index(), force=True)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True).stdout.strip()
    plain, console = eval(out)  # noqa: S307 - our own printed list
    assert plain & 0x08000000                       # CREATE_NO_WINDOW added
    assert not console & 0x08000000                 # an explicit console is left alone


def test_not_patched_off_windows(monkeypatch):
    from app import winproc

    monkeypatch.setattr(winproc.sys, "platform", "linux")
    monkeypatch.setattr(winproc, "_patched", False)
    assert winproc.hide_child_consoles() is False
