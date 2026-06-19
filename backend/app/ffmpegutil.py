"""Resolve ffmpeg / ffprobe binaries without depending solely on PATH.

Order: settings.ffmpeg_path (an exe or its bin dir) → PATH (shutil.which) →
clear error. Lets the app work even when a freshly-installed ffmpeg hasn't been
picked up by the current shell's PATH yet.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.config import settings

_EXE = ".exe" if os.name == "nt" else ""


def _resolve(name: str) -> str:
    hint = settings.ffmpeg_path.strip()
    if hint:
        p = Path(hint)
        if p.is_dir():
            for cand in (p / f"{name}{_EXE}", p / name):
                if cand.exists():
                    return str(cand)
        elif p.exists():
            # hint points at one binary → find its sibling (e.g. ffprobe next to ffmpeg)
            sib = p.parent / f"{name}{_EXE}"
            if sib.exists():
                return str(sib)
            if name in p.stem:
                return str(p)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(
        f"'{name}' không tìm thấy. Cài ffmpeg và thêm vào PATH, hoặc đặt "
        f"FFMPEG_PATH trong .env (trỏ tới ffmpeg.exe hoặc thư mục bin của nó)."
    )


def ffmpeg_bin() -> str:
    return _resolve("ffmpeg")


def ffprobe_bin() -> str:
    return _resolve("ffprobe")
