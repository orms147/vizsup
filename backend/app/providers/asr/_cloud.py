"""Shared plumbing for the cloud ASR providers (cloud_stt, audio_llm).

Both UPLOAD the audio — they are opt-in by being chosen, the same rule as the
Google-Lens OCR mode. Pieces here:

- endpoint/client: base URL + key default to OpenRouter (one key covers it).
- chunking: long audio is cut into ~N-minute mp3 pieces, at SILENCES near each
  target boundary, so a sentence is rarely split across two uploads. Upload
  limits (25 MB on most /audio/transcriptions APIs) and long-audio drift both
  argue for chunks; 48 kbps mono mp3 is ~2.2 MB for 6 minutes.
- text-only engines (audio LLMs, gpt-4o-transcribe) return no timestamps: their
  text is split into subtitle-sized sentences, given proportional fallback times
  inside the chunk, then snapped onto local faster-whisper WORD timestamps by the
  existing ``pipeline.align.fuse_transcript_timing`` — text from the engine that
  hears Mandarin best, timing from Whisper (the funasr_aligned recipe).
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.models import Cue
from app.providers.base import _installed

log = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
PARALLEL_CHUNKS = 3
MAX_SENTENCE_CHARS = 22        # longer lines get split at a comma — subtitle-sized
_SEARCH_SEC = 20.0             # how far from a target boundary to look for a silence
_SILENCE = re.compile(r"silence_(start|end): ([\d.]+)")
_HARD_END = re.compile(r"(?<=[。！？；!?;…])")
_SOFT = "，、,："
_CJK = re.compile(r"[一-鿿]")


# --- endpoint -----------------------------------------------------------------

def endpoint() -> tuple[str, str]:
    base = (settings.stt_api_base_url.strip() or OPENROUTER_BASE).rstrip("/")
    key = settings.stt_api_key.strip()
    if not key and is_openrouter(base):
        key = settings.openrouter_api_key
    return base, key


def is_openrouter(base: str) -> bool:
    return "openrouter.ai" in base


_CLIENTS: dict[tuple[str, str], object] = {}


def client():
    """One cached OpenAI-compatible client per (base, key). max_retries=0: with_retry
    below is the single retry authority. Long read timeout — a minutes-long chunk."""
    import httpx
    from openai import OpenAI

    base, key = endpoint()
    c = _CLIENTS.get((base, key))
    if c is None:
        c = OpenAI(api_key=key or "none", base_url=base, max_retries=0,
                   timeout=httpx.Timeout(connect=10.0, read=300.0, write=120.0, pool=30.0))
        _CLIENTS[(base, key)] = c
    return c


def status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None),
                                                         "status_code", None)


def with_retry(fn: Callable, *, attempts: int = 3, sleep: Callable[[float], None] = time.sleep):
    """Retry network blips, 429 and 5xx; a 4xx is a real answer and is raised at once."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            st = status(exc)
            permanent = st is not None and st < 500 and st != 429
            if permanent or i == attempts - 1:
                raise
            sleep(2.0 * (2 ** i))
    raise RuntimeError("unreachable")


def test_models_endpoint() -> tuple[bool, str]:
    import httpx

    base, key = endpoint()
    if not key:
        return False, "Chưa có key (STT_API_KEY, hoặc OPENROUTER_API_KEY khi dùng OpenRouter)."
    try:
        r = httpx.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        r.raise_for_status()
        n = len(r.json().get("data") or [])
        return True, f"OK · {base} · {n} model"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# --- chunking -----------------------------------------------------------------

def choose_cuts(duration: float, silences: list[float], target: float,
                search: float = _SEARCH_SEC) -> list[tuple[float, float]]:
    """Split [0, duration] into ~``target``-second spans, cutting at the silence
    nearest each ideal boundary (within ±search), else exactly at the boundary.
    A short tail is folded into the last span instead of becoming its own upload."""
    if duration <= target * 1.15:
        return [(0.0, duration)]
    cuts = [0.0]
    while duration - cuts[-1] > target * 1.15:
        ideal = cuts[-1] + target
        cands = [s for s in silences
                 if abs(s - ideal) <= search and s > cuts[-1] + target * 0.5 and s < duration]
        cuts.append(min(cands, key=lambda s: abs(s - ideal)) if cands else ideal)
    cuts.append(duration)
    return list(zip(cuts, cuts[1:]))


def probe_silences(media: Path) -> list[float]:
    """Midpoints of silent stretches (ffmpeg silencedetect). Empty on any failure —
    chunking then just cuts at the exact boundaries."""
    from app.ffmpegutil import ffmpeg_bin

    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(media),
                            "-af", "silencedetect=noise=-35dB:d=0.35", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600)
    except Exception:  # noqa: BLE001
        return []
    mids, start = [], None
    for kind, val in _SILENCE.findall(r.stderr):
        if kind == "start":
            start = float(val)
        elif start is not None:
            mids.append((start + float(val)) / 2)
            start = None
    return mids


def plan_chunks(media: Path, target: float) -> list[tuple[float, float]]:
    from app.pipeline.assemble import probe_duration

    duration = probe_duration(media)
    silences = probe_silences(media) if duration > target * 1.15 else []
    return choose_cuts(duration, silences, target)


@contextmanager
def chunk_files(media: Path, spans: list[tuple[float, float]]):
    """Yield one mp3 per span (mono 16 kHz 48 kbps) in a temp dir, removed after."""
    from app.ffmpegutil import ffmpeg_bin

    tmp = Path(tempfile.mkdtemp(prefix="vizsup_stt_"))
    try:
        paths = []
        for i, (s, e) in enumerate(spans):
            out = tmp / f"chunk_{i:03d}.mp3"
            subprocess.run([ffmpeg_bin(), "-y", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}",
                            "-i", str(media), "-ac", "1", "-ar", "16000", "-b:a", "48k",
                            str(out)], check=True, capture_output=True)
            paths.append(out)
        yield paths
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_chunks(fn: Callable[[Path], object], paths: list[Path]) -> list:
    """Apply ``fn`` to every chunk, results in order. The FIRST chunk runs alone:
    it is the probe that fills the providers' memos (text-only model, mandatory
    reasoning) and fails fast on a bad key/model — instead of N parallel uploads
    each discovering the same thing. The rest then fan out ≤PARALLEL_CHUNKS wide."""
    if not paths:
        return []
    first = fn(paths[0])
    if len(paths) == 1:
        return [first]
    with ThreadPoolExecutor(max_workers=PARALLEL_CHUNKS) as ex:
        return [first, *ex.map(fn, paths[1:])]


# --- text-only engines: sentences + timing --------------------------------------

def _split_long(s: str) -> list[str]:
    if len(s) <= MAX_SENTENCE_CHARS:
        return [s]
    mid = len(s) // 2
    cut = max((i for i, ch in enumerate(s) if ch in _SOFT and i < len(s) - 1),
              key=lambda i: -abs(i - mid), default=None)
    if cut is None:
        return [s]                         # no comma to cut at — leave it whole
    return _split_long(s[:cut + 1].strip()) + _split_long(s[cut + 1:].strip())


def split_sentences(text: str) -> list[str]:
    """Transcript → subtitle-sized lines: split on newlines and sentence-final
    punctuation, then halve over-long sentences at the comma nearest the middle."""
    out: list[str] = []
    for para in (text or "").replace("\r", "").split("\n"):
        for s in _HARD_END.split(para):
            s = s.strip().strip("\"'“”「」*").strip()
            if s and (_CJK.search(s) or any(ch.isalnum() for ch in s)):   # drop bare punctuation
                out.extend(p for p in _split_long(s) if p)
    return out


def proportional_cues(sentences: list[str], start: float, end: float) -> list[Cue]:
    """Spread sentences over [start, end] by character count. Only a FALLBACK —
    fusion replaces these wherever it finds anchor words."""
    weights = [max(1, len(s)) for s in sentences]
    total = sum(weights) or 1
    span = max(0.0, end - start)
    cues, t = [], start
    for s, w in zip(sentences, weights):
        d = span * w / total
        cues.append(Cue(index=0, start=round(t, 3), end=round(t + d, 3), text=s))
        t += d
    return cues


def add_word_timing(media: Path, cues: list[Cue], language: str) -> list[Cue]:
    """Snap text-only cues onto local faster-whisper word timestamps. Without
    faster-whisper installed, the proportional times stand (logged)."""
    if not cues:
        return cues
    if not _installed("faster_whisper"):
        log.warning("faster-whisper not installed — cloud transcript keeps rough proportional "
                    "timing; install .[asr] for word-accurate cue times")
        return cues
    from app.pipeline.align import fuse_transcript_timing
    from app.providers.asr.funasr_aligned import _faster_whisper_words

    return fuse_transcript_timing(cues, _faster_whisper_words(media, language))


def reindex(cues: list[Cue]) -> list[Cue]:
    for i, c in enumerate(cues, 1):
        c.index = i
    return cues
