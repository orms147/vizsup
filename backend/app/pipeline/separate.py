"""ENG-2: split the source audio into vocals + instrumental (Demucs) so the dub can
REPLACE the original speech while KEEPING the background music.

Demucs runs OUT-OF-PROCESS (`python -m demucs`), so its torch state never shares the
venv with faster-whisper/onnxruntime — the 8 GB card runs it inside heavy_task(), and
the result is cached as <job>/bg.wav (the no-vocals stem, 48 kHz stereo) so a
re-render is free. All post-gate; the human edit gate is untouched.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin
from app.models import Job
from app.providers.base import _installed


def available() -> bool:
    """Demucs importable? (It's an optional `.[audio]` extra, not in the base venv.)"""
    return _installed("demucs")


def separate_background(job: Job, *, model: str = "htdemucs", log=None) -> Path:
    """Produce ``<job>/bg.wav`` — the instrumental (no-vocals) stem of the source.
    Cached: skipped when bg.wav is newer than source.mp4 (re-renders are free).
    Raises RuntimeError when Demucs isn't installed."""
    bg = job.work_dir / "bg.wav"
    src = job.source_video
    if bg.exists() and src.exists() and bg.stat().st_mtime >= src.stat().st_mtime:
        if log:
            log("↷ Dùng lại nhạc nền đã tách (bg.wav)")
        return bg
    if not available():
        raise RuntimeError('Demucs chưa cài. Chạy: pip install -e ".[audio]"')

    stems = job.work_dir / "stems"
    from app.heavy import heavy_task

    with heavy_task("tách nhạc (Demucs)"):
        # two-stems=vocals → vocals.wav + no_vocals.wav; out-of-process keeps torch isolated
        subprocess.run(
            [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", model,
             "-o", str(stems), str(src)],
            check=True, capture_output=True,
        )
    no_vocals = next(iter(stems.glob(f"{model}/*/no_vocals.wav")), None)
    if no_vocals is None:
        raise RuntimeError("Demucs không tạo được stem nhạc nền (no_vocals.wav).")
    # normalize to 48 kHz stereo so the assemble mix path can use it directly. Write to
    # a temp then atomically replace, so an interrupted encode can't leave a truncated
    # bg.wav that a later render would mistake for a valid (mtime-fresh) instrumental.
    tmp = bg.with_name("bg.wav.tmp")
    subprocess.run(
        [ffmpeg_bin(), "-y", "-i", str(no_vocals), "-ar", "48000", "-ac", "2",
         "-f", "wav", str(tmp)],                 # ".tmp" says nothing about the format
        check=True, capture_output=True,
    )
    os.replace(tmp, bg)
    if log:
        log("✓ Đã tách nhạc nền (bg.wav)")
    return bg
