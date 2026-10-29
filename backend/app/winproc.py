"""No console windows for child programs on Windows (2026-10-29).

Started without a console (pythonw.exe, a packaged build), every console program the app
runs — ffmpeg, ffprobe, yt-dlp, Demucs' helpers, nvidia-smi — opens its own black window
for as long as it runs: during a download / render they flash on screen one after another.
``hide_child_consoles()`` makes every ``subprocess.Popen`` (and so ``run`` / ``check_output``
/ asyncio subprocesses) start with CREATE_NO_WINDOW, unless the caller asked for a console.
With a console (python.exe from a terminal) children share it, so nothing is changed.
"""
from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
_CONSOLE_FLAGS = 0x00000010 | 0x00000008 | CREATE_NO_WINDOW   # NEW_CONSOLE | DETACHED | NO_WINDOW
_patched = False


def has_console() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:  # noqa: BLE001
        return True


def hide_child_consoles(*, force: bool = False) -> bool:
    """Patch ``subprocess.Popen`` once (Windows, no console of our own — or ``force``).
    Returns True when the patch is active."""
    global _patched
    if _patched:
        return True
    if sys.platform != "win32" or (has_console() and not force):
        return False
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & _CONSOLE_FLAGS:          # the caller did not choose a console itself
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = __init__
    _patched = True
    return True
