"""Stage: TTS. Synthesize one audio segment per (edited) Vietnamese cue.

Reads ``job.vi_srt`` (post-edit), writes ``job.tts_dir/seg_0001.mp3`` etc.,
and returns the list of (Cue, segment_path) pairs for the assembly stage.
Provider-pluggable; default edge-tts (vi-VN).

Resilience (N-10):
- Resume — ``tts/manifest.json`` maps cue index → a hash of every knob that
  shapes the audio; a segment whose file exists and whose hash matches is kept,
  so a re-render only re-synthesizes lines whose text/voice actually changed
  (and a mid-run crash keeps all finished segments).
- Retry — each cue gets 3 attempts with backoff (edge-tts throws sporadic 403s;
  one blip at cue 180/200 used to discard 179 finished segments). Implemented
  here in the pipeline layer so every provider benefits.
- Spoken text — the voice gets ``speak.spoken(line)`` (numbers, units, abbreviations and the
  user's ``pronounce.json`` read the Vietnamese way); the subtitle keeps the written form.
  The SPOKEN text is what the cache key hashes, so editing a rule re-voices only the lines
  it changes.
- Trimmed silence — every provider's segment carries ~0.2 s of leading and ~0.65 s of trailing
  silence (edge-tts measured 2026-10-30; 3-5 word lines are padded to 1.87 s). The assembler
  plans on file durations, so that silence was sped up, pushed the next line and was flagged
  "QUÁ DÀI" (a 5-episode series: 167 flagged lines, 57 s trimmed, none really too long). Each
  segment is cut to its speech (+0.05 s before, +0.12 s after) once, in place; ``tts/trimmed.json``
  remembers which files (size + mtime) are done, so segments cached by an older run are
  trimmed too, without a new synthesis. Never fatal: a file that cannot be analysed or would
  shrink under 0.15 s stays as it is.
- Cancel — an optional callback is checked between cues; cancelling returns the
  partial list (the worker's between-stage check then aborts the render).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

from app.models import Cue, Job
from app.pipeline import speak
from app.pipeline.srt import parse_srt
from app.providers.registry import get_tts
from app.fsutil import atomic_write_text

_RETRY_DELAYS = (1.0, 4.0)   # 3 attempts: try, wait 1s, try, wait 4s, final try

# --- trimming the silence around the speech --------------------------------------------------
TRIM_FILE = "trimmed.json"
TRIM_LEAD, TRIM_TAIL = 0.05, 0.12   # s of silence kept before / after the speech
TRIM_MIN = 0.15                     # a segment is never cut shorter than this
_ENV_HOP = 0.01                     # envelope frame, s
_ABS_FLOOR = 0.003                  # ≈ −50 dBFS: below this is silence whatever the level
_REL_FLOOR = 0.012                  # ≈ −38 dB under the segment's own peak
_PCM_RATE = 16000


def speech_window(env, dur: float, *, hop: float = _ENV_HOP) -> tuple[float, float] | None:
    """(start, end) in seconds to keep from a segment: the first / last frame of the peak
    envelope ``env`` above max(absolute floor, 1.2 % of the segment's own peak), widened by
    TRIM_LEAD / TRIM_TAIL. None when nothing is above it (silence) or the result would be
    shorter than TRIM_MIN. Pure."""
    import numpy as np

    env = np.asarray(env, dtype=float)
    if env.size == 0:
        return None
    peak = float(env.max())
    thr = max(_ABS_FLOOR, _REL_FLOOR * peak)
    loud = np.nonzero(env > thr)[0]
    if loud.size == 0:
        return None
    start = max(0.0, loud[0] * hop - TRIM_LEAD)
    end = min(dur, (loud[-1] + 1) * hop + TRIM_TAIL)
    return (start, end) if end - start >= TRIM_MIN else None


def trim_silence(path: Path, *, min_gain: float = 0.08) -> bool:
    """Cut the silence off both ends of one audio file, in place (temp + replace).
    True when the file now holds only its speech (cut, or nothing worth cutting); False when it
    could not be analysed / the cut would be unsafe (the file is left untouched). Never raises."""
    import subprocess

    import numpy as np

    from app.ffmpegutil import ffmpeg_bin
    from app.fsutil import replace

    path = Path(path)
    tmp = path.with_name(f".{path.stem}.trim{path.suffix}")
    try:
        raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                              "-ar", str(_PCM_RATE), "-f", "s16le", "-"],
                             capture_output=True, check=True, timeout=60).stdout
        x = np.abs(np.frombuffer(raw[:len(raw) // 2 * 2], dtype=np.int16)).astype(np.float32) / 32768.0
        step = int(_PCM_RATE * _ENV_HOP)
        n = len(x) // step
        if n < 2:
            return False
        env = x[:n * step].reshape(n, step).max(axis=1)
        dur = len(x) / _PCM_RATE
        win = speech_window(env, dur)
        if win is None:
            return False
        start, end = win
        if (start + (dur - end)) < min_gain:                   # nothing worth cutting
            return True
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{start:.3f}", "-t",
                        f"{end - start:.3f}", "-i", str(path), "-vn", "-c:a", "libmp3lame",
                        "-q:a", "2", str(tmp)], capture_output=True, check=True, timeout=60)
        if not tmp.exists() or tmp.stat().st_size < 300:
            return False
        replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 - trimming is an aid; the untrimmed file is still valid
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _stamp(p: Path) -> list | None:
    try:
        st = p.stat()
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def trim_files(paths, registry: Path, *, workers: int = 4,
               cancel: Callable[[], bool] | None = None) -> int:
    """Trim every file in ``paths`` not yet recorded in ``registry`` (name → [size, mtime_ns]
    AFTER trimming); records the ones done. Returns how many were cut/checked this call.
    Off with VIZSUP_TRIM_TTS=0."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    if os.environ.get("VIZSUP_TRIM_TTS", "1") == "0":
        return 0
    try:
        done = json.loads(registry.read_text(encoding="utf-8"))
        done = done if isinstance(done, dict) else {}
    except (OSError, ValueError):
        done = {}
    todo = [p for p in map(Path, paths) if p.exists() and done.get(p.name) != _stamp(p)]
    if not todo:
        return 0

    def one(p: Path):
        if cancel and cancel():
            return p, False
        return p, trim_silence(p)

    n = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for p, ok in ex.map(one, todo):
            if ok:
                done[p.name] = _stamp(p)
                n += 1
    try:
        atomic_write_text(registry, json.dumps(done), encoding="utf-8")
    except OSError:
        pass
    return n


def load_overrides(job: Job) -> list[dict]:
    """Per-cue dub overrides (gain_db, mute) — delegates to pipeline.sidecars
    (the single loader implementation; kept here for existing callers)."""
    from app.pipeline import sidecars

    return sidecars.load_overrides(job)


def _seg_hash(provider: str, voice: str | None, rate: str | None, text: str) -> str:
    """Cache key for one synthesized segment; ``text`` is the SPOKEN text (``speak``).
    EVERY knob that changes the audio MUST appear here — a missing input would serve stale audio on resume
    (locked by tests/test_tts_resume.py)."""
    return hashlib.sha1(
        f"{provider}|{voice or ''}|{rate or ''}|{text}".encode()).hexdigest()


def _load_manifest(job: Job) -> dict:
    try:
        data = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_manifest(job: Job, manifest: dict) -> None:
    atomic_write_text((job.tts_dir / "manifest.json"),
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def _unvoiceable(exc: Exception) -> bool:
    """edge-tts raises NoAudioReceived when a line contains nothing the picked
    voice can speak — e.g. pure-Chinese text on a vi-VN voice, which is exactly
    what the no-keys passthrough smoke path produces. The caller skips that line's
    dub (subtitle still shows). NOT for normal text: there it means the service is
    throttling — the providers retry those and raise a different error
    (base.has_speakable decides). Matched by class name (no edge_tts import)."""
    return type(exc).__name__ == "NoAudioReceived"


def _explain(exc: Exception) -> Exception:
    """edge-tts answered with HTTP 403: Microsoft changed the handshake and this client is
    too old (2026-10-16: 7.2.8 refused, 7.3.1 fine) — say what to do instead of the raw
    aiohttp error."""
    if "403" in str(exc) and "Handshake" in type(exc).__name__:
        return RuntimeError("Dịch vụ giọng đọc miễn phí của Microsoft từ chối kết nối (403) — "
                            "thường do edge-tts đã cũ. Vào Cài đặt → Thư mục và công cụ → "
                            "Cập nhật edge-tts, khởi động lại vizsup rồi dựng lại.")
    return exc


def _synth_retry(tts, text: str, seg: Path, *, voice, rate, sleep) -> None:
    from app.providers.base import has_speakable

    for delay in (*_RETRY_DELAYS, None):
        try:
            tts.synthesize(text, seg, voice=voice, rate=rate)
            return
        except Exception as exc:  # noqa: BLE001 - transient 403/timeouts get the retries
            if _unvoiceable(exc) and not has_speakable(text):
                raise                         # nothing a voice could say → caller skips
            if delay is None:
                if _unvoiceable(exc):         # "no audio" for normal text = throttling
                    raise RuntimeError("máy chủ giọng đọc đang giới hạn — không trả âm "
                                       "thanh") from exc
                raise
            sleep(delay)


def _line_voices(tts, ov: list[dict], n: int, voice: str | None, by_gender: bool) -> list[str | None]:
    """The voice of each line: ``voice`` for all, or the tag's voice when ``by_gender``."""
    if not by_gender:
        return [voice] * n
    from app.pipeline import voices

    try:
        names = list(tts.voices() or [])
    except Exception:  # noqa: BLE001 - a provider without a voice list keeps the one voice
        names = []
    rows = [ov[i] if i < len(ov) else {} for i in range(n)]
    return voices.line_voices(rows, names, voice)


def synthesize(
    job: Job,
    *,
    provider: str = "edge",
    voice: str | None = None,
    rate: str | None = None,
    log: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    voice_by_gender: bool = False,
    trim: bool = True,
    _sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[Cue, Path]]:
    """One segment per dubbed line (see ``_synthesize``), then the silence around each one's
    speech is cut (``trim``; the module docstring says why) — cached segments of older runs
    included. ``progress(done, total)``: lines ready so far (cache hits included)."""
    segments = _synthesize(job, provider=provider, voice=voice, rate=rate, log=log, cancel=cancel,
                           progress=progress, voice_by_gender=voice_by_gender, _sleep=_sleep)
    if trim and segments:                 # (not cancel-aware on purpose: it takes seconds)
        n = trim_files([seg for _c, seg in segments], job.tts_dir / TRIM_FILE)
        if log and n:
            log(f"✂ Cắt khoảng lặng đầu / cuối của {n} đoạn giọng đọc")
    return segments


def _synthesize(
    job: Job,
    *,
    provider: str = "edge",
    voice: str | None = None,
    rate: str | None = None,
    log: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    voice_by_gender: bool = False,
    _sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[Cue, Path]]:
    """``progress(done, total)``: lines ready so far (cache hits included) — a 717-line
    dub takes ~13 min, a bar frozen at one value looked like a hang.

    ``voice_by_gender``: a line tagged "f" / "m" in overrides.json (pipeline/voices.py)
    is read by the provider's female / male voice; untagged lines (and providers without
    such voices) keep ``voice``. The line's voice is part of its cache key."""
    from app.providers.registry import split_voice

    provider, voice = split_voice(provider, voice)     # "vieneu:Trúc Ly" runs VieNeu
    tts = get_tts(provider)
    if not tts.available():
        raise RuntimeError(f"TTS provider '{provider}' is not available (missing key or dependency).")
    if log and provider == "vieneu":
        from app.providers.tts import vieneu

        if not vieneu.model_cached():
            log("⬇ Tải giọng VieNeu lần đầu (~580 MB, vài phút) — các lần sau dùng ngay")

    cues = parse_srt(job.vi_srt)
    ov = load_overrides(job)
    manifest = _load_manifest(job)
    rules = speak.load_rules(job)
    said = {c.index: speak.spoken(c.text, rules) for c in cues}     # what the voice reads
    lv = _line_voices(tts, ov, len(cues), voice, voice_by_gender)
    if log and (n := sum(said[c.index] != c.text.strip() for c in cues if c.text.strip())):
        log(f"✎ Chuẩn hoá cách đọc (số, đơn vị, từ viết tắt, từ điển phát âm): {n} câu")

    # API-10: batch-capable providers (edge-tts) synthesize the whole job on ONE
    # event loop with bounded concurrency; others keep the per-cue path below.
    if getattr(tts, "supports_batch", False):
        return _synthesize_batch(job, tts, cues, ov, manifest, said, provider=provider,
                                 line_voices=lv, rate=rate, log=log, cancel=cancel,
                                 progress=progress)

    segments: list[tuple[Cue, Path]] = []
    kept = made = 0
    for i, c in enumerate(cues):
        if cancel and cancel():
            break  # partial list; the caller's between-stage check aborts the render
        key = str(c.index)
        if (ov[i].get("mute") if i < len(ov) else False) or not said[c.index].strip():
            manifest.pop(key, None)   # muted/empty line → no dub, and no stale cache
            continue
        seg = job.tts_dir / f"seg_{c.index:04d}.mp3"
        want = _seg_hash(provider, lv[i], rate, said[c.index])
        if seg.exists() and seg.stat().st_size > 0 and manifest.get(key) == want:
            kept += 1
        else:
            try:
                _synth_retry(tts, said[c.index], seg, voice=lv[i], rate=rate, sleep=_sleep)
            except Exception as exc:  # noqa: BLE001
                if _unvoiceable(exc):
                    manifest.pop(key, None)
                    if log:
                        log(f"⚠ Câu {c.index}: giọng đọc không phát âm được văn bản này "
                            f"— bỏ lồng tiếng câu này (phụ đề vẫn hiện).")
                    continue
                raise _explain(exc) from exc
            manifest[key] = want
            _save_manifest(job, manifest)   # write-after-each: a crash keeps progress
            made += 1
        segments.append((c, seg))
        if progress:
            progress(i + 1, len(cues))
    _save_manifest(job, manifest)           # persists mute-drops even when made == 0
    if log and kept:
        log(f"↷ Giữ lại {kept} câu đã tạo trước đó, tạo mới {made} câu")
    return segments


def _synthesize_batch(
    job: Job, tts, cues: list[Cue], ov: list[dict], manifest: dict, said: dict[int, str], *,
    provider: str, line_voices: list[str | None], rate: str | None,
    log: Callable[[str], None] | None, cancel: Callable[[], bool] | None,
    progress: Callable[[int, int], None] | None = None,
) -> list[tuple[Cue, Path]]:
    """API-10 batch path. Phase A: a sequential scan makes the SAME per-cue decision
    as the fallback (muted/empty → drop cache; cache hit → keep; else → to-do), with
    no network. Phase B: one bounded-concurrency batch on the provider's single event
    loop. Manifest writes + logging happen in on_done, which the provider invokes on
    the loop thread — serialized, so an out-of-order completion can't corrupt the
    index-keyed manifest and a mid-run crash keeps every finished segment."""
    # Phase A — scan.
    todo: list[tuple[str, str, Path]] = []
    voice_of: dict[str, str | None] = {}
    want_of: dict[str, str] = {}
    seg_of: dict[str, Path] = {}
    kept = 0
    for i, c in enumerate(cues):
        key = str(c.index)
        if (ov[i].get("mute") if i < len(ov) else False) or not said[c.index].strip():
            manifest.pop(key, None)          # muted/empty line → no dub, no stale cache
            continue
        seg = job.tts_dir / f"seg_{c.index:04d}.mp3"
        want = _seg_hash(provider, line_voices[i], rate, said[c.index])
        voice_of[key] = line_voices[i]
        seg_of[key] = seg
        want_of[key] = want
        if seg.exists() and seg.stat().st_size > 0 and manifest.get(key) == want:
            kept += 1
        else:
            todo.append((key, said[c.index], seg))

    if progress:
        progress(kept, kept + len(todo))     # cache hits count as done right away
    # Phase B — one batch. on_done runs on the loop thread (serialized).
    made = 0
    fatal: Exception | None = None

    def _done(key: str, exc: Exception | None) -> None:
        nonlocal made, fatal
        if exc is None:
            manifest[key] = want_of[key]
            _save_manifest(job, manifest)    # write-after-each: a crash keeps progress
            made += 1
            if progress:
                progress(kept + made, kept + len(todo))
        elif _unvoiceable(exc):
            manifest.pop(key, None)
            if log:
                log(f"⚠ Câu {key}: giọng đọc không phát âm được văn bản này "
                    f"— bỏ lồng tiếng câu này (phụ đề vẫn hiện).")
        elif isinstance(exc, InterruptedError):
            pass                             # cancelled — not fatal, no manifest write
        elif fatal is None:
            fatal = exc                      # first real failure → raise after the drain

    groups: dict[str | None, list[tuple[str, str, Path]]] = {}
    for item in todo:                        # one batch per voice (the provider takes one)
        groups.setdefault(voice_of[item[0]], []).append(item)
    for v, items in groups.items():
        if fatal is not None or (cancel and cancel()):
            break
        tts.synthesize_many(items, voice=v, rate=rate, retry_delays=_RETRY_DELAYS,
                            on_done=_done, cancel=cancel)
    _save_manifest(job, manifest)            # persists mute-drops even when nothing made
    if fatal is not None:
        if type(fatal).__name__ == "TTSThrottled":
            total = kept + len(todo)
            raise RuntimeError(
                f"Máy chủ giọng đọc đang giới hạn — đã tạo {kept + made}/{total} câu (được giữ "
                f"lại). Đợi vài phút rồi bấm Dựng lại để tạo tiếp.") from fatal
        raise _explain(fatal) from fatal
    # Dubbed segments in cue order (assemble re-sorts by start, but keep it
    # deterministic). A cue is dubbed iff its manifest entry matches — kept or made;
    # cancelled/unvoiceable/muted cues have no matching entry and are excluded.
    segments: list[tuple[Cue, Path]] = []
    for c in cues:
        want = want_of.get(str(c.index))
        if want is not None and manifest.get(str(c.index)) == want:
            segments.append((c, seg_of[str(c.index)]))
    if log and kept:
        log(f"↷ Giữ lại {kept} câu đã tạo trước đó, tạo mới {made} câu")
    return segments
