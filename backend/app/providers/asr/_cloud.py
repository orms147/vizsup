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
  aligned chunk by chunk onto local faster-whisper WORD timestamps by
  ``pipeline.align.align_text_to_words`` (global alignment + interpolation between
  anchors) — text from the engine that hears Mandarin best, timing from Whisper.
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


def run_chunks(fn: Callable, items: list) -> list:
    """Apply ``fn`` to every chunk, results in order. The FIRST chunk runs alone:
    it is the probe that fills the providers' memos (text-only model, mandatory
    reasoning) and fails fast on a bad key/model — instead of N parallel uploads
    each discovering the same thing. The rest then fan out ≤PARALLEL_CHUNKS wide."""
    if not items:
        return []
    first = fn(items[0])
    if len(items) == 1:
        return [first]
    with ThreadPoolExecutor(max_workers=PARALLEL_CHUNKS) as ex:
        return [first, *ex.map(fn, items[1:])]


# --- text-only engines: sentences + timing --------------------------------------

def _split_long(s: str) -> list[str]:
    """Halve an over-long sentence at the comma nearest its middle; failing that at a
    space; failing that at the middle itself. Whisper APIs often return Chinese with
    NO punctuation at all — leaving such a run whole made one 30 s cue per segment
    (cue starts off by a median 47 s on a real vlog). Cut pieces are timed by the word
    alignment afterwards, so a cut needs no linguistic boundary to land well."""
    if len(s) <= MAX_SENTENCE_CHARS:
        return [s]
    mid = len(s) // 2
    for seps in (_SOFT, " "):
        cut = min((i for i, ch in enumerate(s) if ch in seps and 0 < i < len(s) - 1),
                  key=lambda i: abs(i - mid), default=None)
        if cut is not None:
            break
    else:
        cut = mid - 1                      # no separator at all: plain halving
    left, right = s[:cut + 1].strip(), s[cut + 1:].strip()
    if not left or not right:
        return [s]
    return _split_long(left) + _split_long(right)


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


Group = tuple[tuple[float, float], list[str]]   # (chunk span, its untimed sentences)
_WORD_MARGIN = 1.5    # seconds of word track borrowed from each neighbouring chunk


Word = tuple[str, float, float]
DEFAULT_TIMING_MODEL = "openai/whisper-large-v3"


def local_words_available() -> bool:
    """Local faster-whisper is optional: with it, the word track is computed on this
    machine; without it (the default since 2026-10-01 — it is ~3.4 GB of models), the
    word track comes from a cloud transcription model (cloud_words)."""
    return _installed("faster_whisper")


def timing_model() -> str:
    return settings.stt_timing_model.strip() or DEFAULT_TIMING_MODEL


def _words_of(resp, offset: float) -> list[Word]:
    out: list[Word] = []
    for w in (resp.get("words") if isinstance(resp, dict) else getattr(resp, "words", None)) or []:
        get = (lambda k: w.get(k)) if isinstance(w, dict) else (lambda k: getattr(w, k, None))
        text, s, e = get("word"), get("start"), get("end")
        if text and s is not None and e is not None:
            out.append((str(text), offset + float(s), offset + float(e)))
    return out


def cloud_words(items: list[tuple[Path, tuple[float, float]]], language: str) -> list[Word]:
    """Word timestamps for each uploaded chunk from a /audio/transcriptions model
    (verbose_json + word granularity), shifted to absolute time. Measured 2026-10-01
    on a real 137 s vlog: openai/whisper-large-v3 → 691 words in 4.6 s."""
    model = timing_model()

    def one(item) -> list[Word]:
        path, (lo, _hi) = item

        def send():
            with path.open("rb") as fh:
                return client().audio.transcriptions.create(
                    file=fh, model=model, language=language, response_format="verbose_json",
                    timestamp_granularities=["word", "segment"])
        return _words_of(with_retry(send), lo)

    return [w for chunk in run_chunks(one, items) for w in chunk]


def time_groups(media: Path, groups: list[Group], language: str,
                words: list[Word] | None = None) -> list[Cue]:
    """Time text-only transcripts chunk by chunk against a WORD track, aligning each
    chunk on just its own words (±margin) — a bounded problem, and a chunk's mistakes
    cannot leak into another. ``words`` is the cloud word track when the caller fetched
    one; otherwise local faster-whisper computes it; with neither, sentences are spread
    over their chunk by length (rough — logged)."""
    from app.pipeline.align import _spread, align_text_to_words

    groups = [(span, sents) for span, sents in groups if sents]
    if not groups:
        return []
    if words is None:
        if not local_words_available():
            log.warning("no word-timing track (no faster-whisper, no cloud words) — cues get "
                        "rough per-chunk timing")
            return [c for (lo, hi), sents in groups for c in _spread(sents, lo, hi)]
        from app.providers.asr.funasr_aligned import _faster_whisper_words

        words = _faster_whisper_words(media, language)
    out: list[Cue] = []
    for (lo, hi), sents in groups:
        local = [w for w in words if lo - _WORD_MARGIN <= w[1] <= hi + _WORD_MARGIN]
        heard = sum(len(_CJK_CHARS.findall(w[0])) for w in local if lo <= w[1] <= hi)
        got = sum(len(_CJK_CHARS.findall(s)) for s in sents)
        if heard >= _DROPOUT_MIN_HEARD and got < _DROPOUT_RATIO * heard:
            # The engine returned (almost) nothing for speech Whisper clearly heard —
            # mimo-v2.5 did exactly that on a noisy real vlog (2026-10-01). Keep the
            # chunk's content with Whisper's own text rather than silently losing it.
            log.warning("cloud transcript for %.0f-%.0fs has %d hanzi where whisper heard %d — "
                        "using whisper's text for this chunk", lo, hi, got, heard)
            sents = split_sentences("".join(w[0] for w in local if lo <= w[1] <= hi))
        out.extend(align_text_to_words(sents, local, (lo, hi)))
    return out


_CJK_CHARS = re.compile(r"[一-鿿]")
_DROPOUT_MIN_HEARD = 20     # below this, near-silence/music — an empty reply is plausible
_DROPOUT_RATIO = 0.3


# --- output sanity for audio LLMs ---------------------------------------------
# Measured on a real Douyin vlog (2026-10-01): mimo-v2.5 answered with a KOREAN
# translation prefixed "0.03-12.51 | SPEAKER_00:", and mimo-v2.6-pro looped to 4.2k
# chars for ~800 spoken. Garbage must fail loudly here, not surface at the edit gate.
_ANNOTATION = re.compile(
    r"^\s*(?:\[?\d{1,2}(?::\d{2}){0,2}(?:[.,]\d+)?\s*[-–~>]+\s*\d{1,2}(?::\d{2}){0,2}"
    r"(?:[.,]\d+)?\]?\s*\|?\s*)?(?:(?:SPEAKER_?\d+|说话人\s*\d+|Speaker\s*\d+)\s*[:：]\s*)?",
    re.M)
MAX_CHARS_PER_SEC = 12.0   # Mandarin speech runs 4–6 hanzi/s, fast ~8


def clean_transcript(text: str) -> str:
    """Strip per-line timestamp / speaker prefixes some models add unasked."""
    return _ANNOTATION.sub("", text or "").strip()


def check_transcript(text: str, seconds: float, *, model: str) -> None:
    # One hanzi is a word; one Latin letter is not — so Latin counts per WORD (a Douyin
    # line full of "Costco / iPhone / vlog" is still Chinese), other scripts (Hangul,
    # kana, …) per character, which is what a translated reply is made of.
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin_words = len(re.findall(r"[A-Za-z]+", text))
    other = sum(1 for ch in text if ch.isalpha() and not ch.isascii()
                and not "一" <= ch <= "鿿")
    if (latin_words + other) and cjk < latin_words + other:
        raise RuntimeError(f"{model}: reply is not a Chinese transcript (translated or wrong "
                           f"language?) — pick another AUDIO_LLM_MODEL")
    if cjk > MAX_CHARS_PER_SEC * max(seconds, 5.0):
        raise RuntimeError(f"{model}: {cjk} hanzi for {seconds:.0f}s of audio — the model is "
                           f"looping/hallucinating; pick another AUDIO_LLM_MODEL")


def reindex(cues: list[Cue]) -> list[Cue]:
    for i, c in enumerate(cues, 1):
        c.index = i
    return cues
