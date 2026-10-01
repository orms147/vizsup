"""Real waveform peaks for the editor timeline (v2: "sóng âm thật", replacing the
synthetic one). ffmpeg decodes the source to 2 kHz mono PCM; each 20 ms window
becomes one 0..1 RMS value, normalised to the loudest window. Cached as
``waveform.json`` keyed by the source file's size + mtime so a re-open is instant.
"""
from __future__ import annotations

import json
import math
import subprocess
from array import array
from pathlib import Path

from app.models import Job

RATE = 2000          # Hz — plenty for a visual envelope, tiny to decode
PER_SEC = 50         # peaks per second (20 ms windows)
_CACHE = "waveform.json"


def _key(src: Path) -> str:
    st = src.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def compute(src: Path) -> list[float]:
    from app.ffmpegutil import ffmpeg_bin

    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(src), "-vn", "-ac", "1",
                          "-ar", str(RATE), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    samples = array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    win = RATE // PER_SEC
    out: list[float] = []
    for i in range(0, len(samples), win):
        chunk = samples[i:i + win]
        if chunk:
            out.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)))
    top = max(out, default=0.0) or 1.0
    return [round(v / top, 3) for v in out]


def peaks(job: Job) -> list[float]:
    """Cached peaks for the job's source (prefers the extracted source.wav)."""
    src = job.source_audio if job.source_audio.exists() else job.source_video
    if not src.exists():
        return []
    cache = job.work_dir / _CACHE
    key = _key(src)
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("key") == key and data.get("per_sec") == PER_SEC:
            return data["peaks"]
    except (OSError, ValueError, KeyError):
        pass
    p = compute(src)
    cache.write_text(json.dumps({"key": key, "per_sec": PER_SEC, "peaks": p}), encoding="utf-8")
    return p
