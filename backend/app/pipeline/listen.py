"""Editor "AI nghe lại": a listening model writes down what is SAID in a few lines (their own
stretch of the sound, not the OCR) so a line the OCR got wrong, merged or missed can be checked
by ear. Uploads those short clips (audio_llm provider)."""
from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin
from app.models import Job

PAD = 0.25            # seconds of sound kept before / after a line (a cue edge is not exact)
MAX_LINES = 40        # one call hears at most this many lines
WORKERS = 4


def audio_source(job: Job) -> Path:
    """The speech stem when there is one (clean of music), else the extracted audio, else
    the video itself (ffmpeg reads the sound off it)."""
    for p in (job.work_dir / "vocals.wav", job.source_audio, job.source_video):
        if p.exists():
            return p
    raise FileNotFoundError("Dự án này không còn âm thanh gốc để nghe lại.")


def _clip(src: Path, start: float, end: float, dest: Path) -> None:
    t0 = max(0.0, start - PAD)
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{t0:.3f}", "-t",
                    f"{end + PAD - t0:.3f}", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                    "-b:a", "48k", str(dest)], check=True, capture_output=True)


def hear(job: Job, spans: list[tuple[float, float]], *,
         cancel: Callable[[], bool] | None = None) -> list[str]:
    """What is said inside each ``(start, end)`` — one Simplified-Chinese string per span ("" when
    nothing was heard). Raises when the listening model is not available."""
    from app.providers.asr.audio_llm import INSTRUCTION, AudioLLM

    if len(spans) > MAX_LINES:
        raise ValueError(f"Nghe tối đa {MAX_LINES} câu một lần.")
    prov = AudioLLM()
    if not prov.available():
        raise RuntimeError("Chưa dùng được AI nghe âm thanh (thiếu khóa OpenRouter).")
    src = audio_source(job)

    with tempfile.TemporaryDirectory() as td:
        def one(k: int) -> str:
            if cancel and cancel():
                return ""
            a, b = spans[k]
            clip = Path(td) / f"l{k}.mp3"
            _clip(src, a, b, clip)
            return prov.ask(clip, INSTRUCTION + " Output only what is said in this short clip.",
                            max_tokens=300).strip()

        with ThreadPoolExecutor(WORKERS) as pool:
            return list(pool.map(one, range(len(spans))))


def same_text(a: str, b: str, *, threshold: float = 0.8) -> bool:
    """Do two Chinese reads say the same (bigram overlap, punctuation ignored)? Pure."""
    import re

    def grams(t: str) -> set[str]:
        h = "".join(re.findall(r"[一-鿿]", t or ""))
        return {h[i:i + 2] for i in range(len(h) - 1)} or set(h)

    ga, gb = grams(a), grams(b)
    if not ga and not gb:
        return True
    return len(ga & gb) / max(len(ga), len(gb), 1) >= threshold
