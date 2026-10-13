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


def read_exact(stream, n: int) -> bytes:
    """Up to ``n`` bytes from a pipe — a read may return fewer before the end."""
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def pcm_blocks(src: Path, rate: int, block_samples: int):
    """Mono s16le PCM of ``src`` at ``rate`` as int16 numpy blocks of ``block_samples``
    (the last one may be shorter), streamed from ffmpeg so memory does not grow with
    the file — a 3-hour track at 16 kHz is ~1 GB as float32. Raises
    CalledProcessError when ffmpeg fails, like ``subprocess.run(check=True)`` did."""
    import subprocess

    import numpy as np

    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
           "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            buf = read_exact(proc.stdout, block_samples * 2)
            if len(buf) < 2:
                break
            yield np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16)
            if len(buf) < block_samples * 2:
                break
        rc = proc.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
