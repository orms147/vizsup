"""Upkeep for the tools vizsup leans on, surfaced on the Settings page.

- ``update_ytdlp``   — ``pip install -U yt-dlp`` into the interpreter running vizsup.
  Douyin changes its anti-bot checks often, so a stale yt-dlp is the usual reason a
  download suddenly fails.
- ``update_edge_tts`` — the same for edge-tts: Microsoft changes the free voice service's
  handshake now and then and old clients get HTTP 403 (seen 2026-10-16: 7.2.8 → 7.3.1).
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


def _fresh_version(dist: str) -> str:
    """A package's version as a NEW interpreter sees it. This process keeps the old
    module loaded after an update, so it cannot report the new version itself. "" if
    absent."""
    code = f"import importlib.metadata as m;print(m.version('{dist}'))"
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


def update_package(dist: str) -> tuple[bool, str]:
    """``pip install -U <dist>`` in place. Returns (ok, message) and never raises."""
    old = _fresh_version(dist) or "chưa cài"
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", dist],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=_PIP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - timeout, missing pip, …
        return False, f"{dist} {old}: không chạy được pip ({type(exc).__name__}: {exc})"
    if r.returncode != 0:
        return False, f"{dist} {old}: cập nhật thất bại (mã {r.returncode})\n{_tail(r)}"
    new = _fresh_version(dist) or "?"
    return True, f"{dist} {old} → {new}"


def update_ytdlp() -> tuple[bool, str]:
    return update_package("yt-dlp")


def update_edge_tts() -> tuple[bool, str]:
    return update_package("edge-tts")


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
