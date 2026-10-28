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

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TypeVar

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


# --- uploads the network keeps dropping ----------------------------------------
# 2026-10-14, this machine: requests to OpenRouter of ≤ 54 KB always went through, from
# ~217 KB they were cut almost every time (same 1.4 MB to another host: fine) — the
# 4-minute audio chunks (~1.4 MB) all failed and ASR was unusable. A chunk whose upload
# is dropped is cut in two and sent again, down to MIN_SPLIT_SEC; the size that got
# through is remembered for the session, so later chunks are cut up front.
MIN_SPLIT_SEC = 15.0          # ≈ 60 KB at the 32 kbps used for split pieces
_DROP_NAMES = {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout",
               "ReadError", "WriteError", "WriteTimeout", "RemoteProtocolError",
               "ConnectionError", "ConnectionResetError", "ConnectionAbortedError"}
_upload_limit: int | None = None          # bytes; None until a drop was seen


def is_upload_drop(exc: BaseException) -> bool:
    """The request got no HTTP answer at all (the connection was cut), as opposed to an
    error the server returned."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if status(exc) is not None:
            return False
        if type(exc).__name__ in _DROP_NAMES:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _cut(media: Path, span: tuple[float, float], out: Path) -> Path:
    from app.ffmpegutil import ffmpeg_bin

    s, e = span
    subprocess.run([ffmpeg_bin(), "-y", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(media),
                    "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out)],   # smaller: it's a retry
                   check=True, capture_output=True)
    return out


def split_on_drop(media: Path | None, span: tuple[float, float], path: Path,
                  call: Callable[[Path, tuple[float, float]], object], *,
                  min_sec: float = MIN_SPLIT_SEC, log: Callable[[str], None] | None = None) -> list:
    """``call(path, span)`` for one uploaded chunk → ``[(span, result), …]`` in time order.
    When the network drops the upload (or the chunk is bigger than what got through
    earlier), the span is halved and each half sent on its own, recursively. Without
    ``media`` (no source to cut from) it is a plain call."""
    global _upload_limit
    lo, hi = span
    can_split = media is not None and hi - lo >= 2 * min_sec
    size = path.stat().st_size if path.exists() else 0
    if not (can_split and _upload_limit is not None and size > _upload_limit):
        try:
            return [(span, call(path, span))]
        except Exception as exc:
            if not (can_split and is_upload_drop(exc)):
                raise
            _upload_limit = min(_upload_limit or size, max(1, size // 2))
            if log:
                log(f"… mạng ngắt khi gửi {size // 1024} KB — chia đoạn {lo:.0f}–{hi:.0f} s "
                    f"làm đôi và gửi lại")
    mid = round((lo + hi) / 2, 3)
    out: list = []
    with tempfile.TemporaryDirectory(prefix="vizsup_split_") as tmp:
        for k, part in enumerate(((lo, mid), (mid, hi))):
            piece = _cut(media, part, Path(tmp) / f"half_{k}.mp3")
            out += split_on_drop(media, part, piece, call, min_sec=min_sec, log=log)
    return out


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


# --- per-chunk cache ----------------------------------------------------------
# A 45-chunk video that fails at chunk 40 must not re-buy chunks 1-39. Each finished
# chunk is stored as soon as it returns (inside the per-chunk function, so sibling
# threads still store theirs when one chunk raises). Keys name everything that changes
# the answer — never the temp chunk path, which is different on every run.

T = TypeVar("T")
_MISS = object()


def audio_identity(media: Path) -> str:
    """What the audio is, for cache keys: name, size and mtime."""
    st = media.stat()
    return f"{media.name}:{st.st_size}:{int(st.st_mtime)}"


def cache_for(media: Path) -> tuple[Path | None, str]:
    """(``asr_cache`` dir beside the job's audio, audio identity). ``(None, "")`` when the
    audio is not on disk — then nothing is cached."""
    if not media.is_file():
        return None, ""
    return media.parent / "asr_cache", audio_identity(media)


def span_tag(span: tuple[float, float]) -> str:
    return f"{span[0]:.3f}-{span[1]:.3f}"


def text_tag(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def chunk_cache(cache_dir: Path | None, kind: str, key: str, compute: Callable[[], T],
                keep: Callable[[T], bool] | None = None) -> T:
    """Return the value stored under ``key``, or compute it, store it and return it.

    One JSON file per result. A failing ``compute`` raises and stores nothing, so the
    chunk is simply asked again next time. An empty answer (``""``, ``[]``) is a real
    result and IS stored — unless ``keep(value)`` says it is not worth keeping (a word
    track that came back empty for speech). ``cache_dir=None`` just computes.
    """
    if cache_dir is None:
        return compute()
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    path = cache_dir / f"{kind}_{digest}.json"
    hit = _read_entry(path, key)
    if hit is not _MISS:
        return hit
    value = compute()
    if keep is None or keep(value):
        _write_entry(cache_dir, path, key, value)
    return value


def _read_entry(path: Path, key: str) -> object:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _MISS
    if isinstance(data, dict) and data.get("key") == key and "value" in data:
        return data["value"]
    return _MISS


def _write_entry(cache_dir: Path, path: Path, key: str, value: object) -> None:
    """Best effort: the answer is paid for and in hand, so a failed write (full disk,
    locked file) is logged and the run goes on. Temp file + os.replace = never half-written."""
    tmp: str | None = None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "value": value}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as exc:
        if tmp is not None:
            with suppress(OSError):
                Path(tmp).unlink()
        log.warning("chunk cache: could not store %s (%s)", path.name, exc)


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


def record_stt(model: str, resp, seconds: float) -> None:
    """E-14: put one /audio/transcriptions call into the job's usage.json — the response's
    own ``usage.cost`` (OpenRouter: ``usage {seconds, cost}``), else the audio length
    (``duration``, else the uploaded span) × STT_PRICE_PER_MIN."""
    from app import usage

    get = (resp.get if isinstance(resp, dict) else lambda k: getattr(resp, k, None))
    try:
        seconds = float(get("duration") or seconds)
    except (TypeError, ValueError):
        pass
    usage.record_audio(model, get("usage"), seconds, settings.stt_price_per_min)


def _field(obj, name: str):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _words_from_segments(resp, offset: float, max_len: float | None = None) -> list[Word]:
    """Word-like timings from the response's SEGMENTS (each character spread over its
    segment) — for an answer whose word list came back empty. Far closer than spreading
    whole sentences over a 4-minute chunk. ``max_len`` skips segments longer than that
    (a 13 s "segment" spread evenly is a guess, not a timing). Pure."""
    out: list[Word] = []
    for seg in _field(resp, "segments") or []:
        text = "".join(str(_field(seg, "text") or "").split())
        s, e = _field(seg, "start"), _field(seg, "end")
        if not text or s is None or e is None or float(e) <= float(s):
            continue
        if max_len is not None and float(e) - float(s) > max_len:
            continue
        step = (float(e) - float(s)) / len(text)
        for k, ch in enumerate(text):
            out.append((ch, offset + float(s) + k * step, offset + float(s) + (k + 1) * step))
    return out


COLLAPSE_MIN = 3        # words sharing one start time: Whisper's fake anchors


def collapsed_holes(words: list[Word]) -> list[tuple[float, float]]:
    """Spans whose words Whisper stamped all with ONE time (seen 2026-10-28: "居然起雾了
    我操喂怎么突然冒出个人完了" all at 43.10 s over loud music): from the last good word
    before such a cluster to the first good word after it. Pure."""
    from collections import Counter

    starts = Counter(round(w[1], 2) for w in words)
    bad = {t for t, n in starts.items() if n >= COLLAPSE_MIN}
    if not bad:
        return []
    good = sorted((w[1], w[2]) for w in words if round(w[1], 2) not in bad)
    holes = []
    for t in sorted(bad):
        before = [e for s_, e in good if e <= t]
        after = [s_ for s_, _e in good if s_ > t]
        lo = max(before) if before else None
        hi = min(after) if after else None
        holes.append((lo, hi, t))
    out = []
    for lo, hi, t in holes:
        out.append((lo if lo is not None else max(0.0, t - 30.0),
                    hi if hi is not None else t + 1.0))
    return out


SEG_MAX = 8.0          # a segment longer than this is not a usable timing
HOLE_MAX = 90.0        # a collapsed stretch longer than this is not asked again


def _hole_words(resp, offset: float, span: tuple[float, float]) -> list[Word]:
    """The usable timings a (re-asked) response gives inside ``span``: its words when
    they are not collapsed themselves, else its short segments. Pure."""
    lo, hi = span
    words = [w for w in _words_of(resp, offset) if lo - 0.05 <= w[1] < hi]
    if words and not collapsed_holes(words):
        return words
    return [w for w in _words_from_segments(resp, offset, SEG_MAX) if lo - 0.05 <= w[1] < hi]


def repair_collapsed(words: list[Word], resp, offset: float, reask=None
                     ) -> tuple[list[Word], int]:
    """Replace the words of a collapsed cluster (every word at one time) with real
    timings for that stretch: ``reask(span)`` sends just that stretch again (a short
    clip got proper times where the 4-min chunk did not: 起雾了 28.8–30.6 s, 我操喂…
    33.0–34.8 s, while every word sat at 43.1 s), else the chunk answer's own short
    segments. Returns (words, how many collapsed words were replaced)."""
    holes = collapsed_holes(words)
    if not holes:
        return words, 0
    from collections import Counter

    fills: list[Word] = []
    for lo, hi in holes:
        got: list[Word] = []
        if reask is not None and hi - lo <= HOLE_MAX:
            try:
                r = reask((lo, hi))
                got = _hole_words(r, lo, (lo, hi)) if r is not None else []
            except Exception:  # noqa: BLE001 - a repair must never fail the chunk
                got = []
        if not got:
            got = _hole_words(resp, offset, (lo, hi))
        fills += got
    if not fills:
        return words, 0
    starts = Counter(round(w[1], 2) for w in words)
    kept = [w for w in words if starts[round(w[1], 2)] < COLLAPSE_MIN]
    return sorted(kept + fills, key=lambda w: w[1]), len(words) - len(kept)


def _words_of(resp, offset: float) -> list[Word]:
    out: list[Word] = []
    for w in (resp.get("words") if isinstance(resp, dict) else getattr(resp, "words", None)) or []:
        get = (lambda k: w.get(k)) if isinstance(w, dict) else (lambda k: getattr(w, k, None))
        text, s, e = get("word"), get("start"), get("end")
        if text and s is not None and e is not None:
            out.append((str(text), offset + float(s), offset + float(e)))
    return out


def cloud_words(items: list[tuple[Path, tuple[float, float]]], language: str,
                cache_dir: Path | None = None, audio_id: str = "",
                media: Path | None = None, log: Callable[[str], None] | None = None) -> list[Word]:
    """Word timestamps for each uploaded chunk from a /audio/transcriptions model
    (verbose_json + word granularity), shifted to absolute time. Measured 2026-10-01
    on a real 137 s vlog: openai/whisper-large-v3 → 691 words in 4.6 s.

    With ``cache_dir`` and the source's ``audio_id`` (see :func:`cache_for`), each chunk's
    words are kept so a rerun does not pay for them again."""
    model = timing_model()
    cache = cache_dir if audio_id else None

    def one(item) -> list[Word]:
        path, (lo, _hi) = item

        def fetch() -> list[Word]:
            def reask(span: tuple[float, float]):
                """Send just ``span`` again (cut from the source) for its timings."""
                if media is None:
                    return None
                import tempfile

                with tempfile.TemporaryDirectory() as tmp:
                    clip = _cut(media, (max(0.0, span[0]), span[1]), Path(tmp) / "hole.mp3")

                    def send_clip():
                        with clip.open("rb") as fh:
                            return client().audio.transcriptions.create(
                                file=fh, model=model, language=language,
                                response_format="verbose_json",
                                timestamp_granularities=["word", "segment"])
                    r = with_retry(send_clip)
                record_stt(model, r, span[1] - span[0])
                return r

            def one_piece(p: Path, sp: tuple[float, float]) -> list[Word]:
                def send():
                    with p.open("rb") as fh:
                        return client().audio.transcriptions.create(
                            file=fh, model=model, language=language,
                            response_format="verbose_json",
                            timestamp_granularities=["word", "segment"])
                resp = with_retry(send)
                record_stt(model, resp, sp[1] - sp[0])          # E-14 per-job cost
                words = _words_of(resp, sp[0])
                if not words and str(_field(resp, "text") or "").strip():
                    # 2026-10-28: the service answered a 4-min chunk full of speech with
                    # text but NO words; asked again it gave 675 — ask once more, then
                    # fall back to the segment timings
                    heard[0] = True
                    resp = with_retry(send)
                    record_stt(model, resp, sp[1] - sp[0])
                    words = _words_of(resp, sp[0]) or _words_from_segments(resp, sp[0])
                    if log:
                        log(f"⚠ Mốc từng chữ {sp[0]:.0f}–{sp[1]:.0f}s trả về rỗng — "
                            + ("hỏi lại được" if _words_of(resp, sp[0]) else
                               "dùng mốc theo đoạn" if words else "vẫn rỗng, sẽ hỏi lại lần sau"))
                words, fixed = repair_collapsed(words, resp, sp[0], reask=reask)
                if fixed and log:
                    log(f"⚠ {fixed} chữ bị dồn về một mốc ({sp[0]:.0f}–{sp[1]:.0f}s) — dùng mốc "
                        f"theo đoạn cho chỗ đó")
                return words

            pieces = split_on_drop(media, (lo, _hi), path, one_piece, log=log)
            return [w for _sp, words in pieces for w in words]

        key = f"words|{model}|{span_tag((lo, _hi))}|{audio_id}|{language}"
        heard = [False]       # the chunk has speech (text) — an empty word list is a failure
        # JSON gives lists back: rebuild the tuples so cached and fresh results are equal
        return [(t, s, e) for t, s, e in chunk_cache(
            cache, "words", key, fetch, keep=lambda v: bool(v) or not heard[0])]

    return [w for chunk in run_chunks(one, items) for w in chunk]


def time_groups(media: Path, groups: list[Group], language: str,
                words: list[Word] | None = None,
                voice: list[tuple[float, float]] | None = None) -> list[Cue]:
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
        out.extend(align_text_to_words(sents, local, (lo, hi), voice=voice))
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


class Counter:
    """Thread-safe 1, 2, 3 … for "chunk i/N done" progress lines from the pool."""

    def __init__(self) -> None:
        import threading

        self._n = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._n += 1
            return self._n
