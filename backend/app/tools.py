"""Upkeep for the tools vizsup leans on, surfaced on the Settings page.

- ``update_ytdlp``   — ``pip install -U yt-dlp`` into the interpreter running vizsup.
  Douyin changes its anti-bot checks often, so a stale yt-dlp is the usual reason a
  download suddenly fails.
- ``cookie_age_days`` — how old the exported cookies.txt is. Douyin cookies last roughly
  three weeks, so the page warns once the file gets old.

Plain functions, no Qt: the GUI runs them on a worker thread and shows the result.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

_DAY = 86400.0
_PIP_TIMEOUT = 300  # seconds


def _fresh_ytdlp_version() -> str:
    """yt-dlp version as a NEW interpreter sees it. This process keeps the old module
    loaded after an update, so it cannot report the new version itself. "" if absent."""
    code = "import importlib.metadata as m;print(m.version('yt-dlp'))"
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except Exception:  # noqa: BLE001 - a broken interpreter must not crash the page
        return ""
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def _tail(r: subprocess.CompletedProcess, lines: int = 3) -> str:
    """Last few lines of pip's output; pip writes the actual error to stderr."""
    text = (r.stderr or "").strip() or (r.stdout or "").strip()
    return "\n".join(text.splitlines()[-lines:])


def update_ytdlp() -> tuple[bool, str]:
    """Upgrade yt-dlp in place. Returns (ok, message) and never raises."""
    old = _fresh_ytdlp_version() or "chưa cài"
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=_PIP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - timeout, missing pip, …
        return False, f"yt-dlp {old}: không chạy được pip ({type(exc).__name__}: {exc})"
    if r.returncode != 0:
        return False, f"yt-dlp {old}: cập nhật thất bại (mã {r.returncode})\n{_tail(r)}"
    new = _fresh_ytdlp_version() or "?"
    return True, f"yt-dlp {old} → {new}"


def cookie_age_days(path: str | None) -> float | None:
    """Days since the cookie file was written. None when no file is configured or it
    does not exist, so the caller can simply hide the warning."""
    if not path:
        return None
    p = Path(path).expanduser()
    try:
        if not p.is_file():
            return None
        age = time.time() - p.stat().st_mtime
    except OSError:
        return None
    return max(age, 0.0) / _DAY
