"""ENG-2: split the source audio into vocals + instrumental (Demucs) so the dub can
REPLACE the original speech while KEEPING the background music.

Demucs runs OUT-OF-PROCESS (`python -m demucs`), so its torch state never shares the
venv with faster-whisper/onnxruntime — the 8 GB card runs it inside heavy_task(), and
the result is cached as <job>/bg.wav (the no-vocals stem, 48 kHz stereo) so a
re-render is free. All post-gate; the human edit gate is untouched.

The same run also keeps <job>/vocals.wav (the speech stem, 16 kHz mono): word timings
taken on it are right where loud music made Whisper stamp a whole stretch with one
time (2026-10-28: "我操喂…" landed 3 s early; on the vocals it was exact, run after run).
``speech_stem`` makes it before the ASR step when a GPU makes that cheap (~4 s per
minute of video).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin
from app.models import Job
from app.providers.base import _installed


VOCALS = "vocals.wav"
_GPU: bool | None = None


def available() -> bool:
    """Demucs importable? (It's an optional `.[audio]` extra, not in the base venv.)"""
    return _installed("demucs")


def gpu_available() -> bool:
    """CUDA for Demucs (asked once, out of process: torch stays out of the GUI)."""
    global _GPU
    if _GPU is None:
        try:
            r = subprocess.run([sys.executable, "-c",
                                "import torch;print(torch.cuda.is_available())"],
                               capture_output=True, text=True, timeout=60)
            _GPU = r.stdout.strip().endswith("True")
        except Exception:  # noqa: BLE001
            _GPU = False
    return _GPU


def _fresh(path: Path, src: Path) -> bool:
    return path.exists() and src.exists() and path.stat().st_mtime >= src.stat().st_mtime


def speech_stem(job: Job, *, model: str = "htdemucs", log=None,
                require_gpu: bool = True) -> Path | None:
    """``<job>/vocals.wav`` (speech only, 16 kHz mono) for timing the subtitles, or None
    (no Demucs, no GPU while ``require_gpu``, or the separation failed — the caller
    then times on the original sound). Also leaves bg.wav for "Giữ nhạc nền"."""
    vocals = job.work_dir / VOCALS
    if _fresh(vocals, job.source_video):
        return vocals
    if not available() or not job.source_video.exists() or (require_gpu and not gpu_available()):
        return None
    try:
        if log:
            log("Tách giọng nói khỏi nhạc nền để canh thời gian chính xác (Demucs)…")
        _split(job, model)
    except Exception as exc:  # noqa: BLE001 - an aid: timing falls back to the mix
        if log:
            log(f"⚠ Không tách được giọng ({type(exc).__name__}) — canh trên âm thanh gốc")
        return None
    return vocals if vocals.exists() else None


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

    _split(job, model)
    if log:
        log("✓ Đã tách nhạc nền (bg.wav)")
    return bg


def _split(job: Job, model: str) -> None:
    """One Demucs run on the source → bg.wav (48 kHz stereo) + vocals.wav (16 kHz mono),
    each written to a temp and swapped in."""
    src = job.source_video
    bg = job.work_dir / "bg.wav"
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
    voc = next(iter(stems.glob(f"{model}/*/vocals.wav")), None)
    if voc is not None:
        vtmp = job.work_dir / "vocals.wav.tmp"
        subprocess.run([ffmpeg_bin(), "-y", "-i", str(voc), "-ar", "16000", "-ac", "1",
                        "-f", "wav", str(vtmp)], check=True, capture_output=True)
        os.replace(vtmp, job.work_dir / VOCALS)
