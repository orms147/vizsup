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
from app.pipeline.srt import parse_srt
from app.providers.registry import get_tts

_RETRY_DELAYS = (1.0, 4.0)   # 3 attempts: try, wait 1s, try, wait 4s, final try


def load_overrides(job: Job) -> list[dict]:
    """Per-cue dub overrides (gain_db, mute) — delegates to pipeline.sidecars
    (the single loader implementation; kept here for existing callers)."""
    from app.pipeline import sidecars

    return sidecars.load_overrides(job)


def _seg_hash(provider: str, voice: str | None, rate: str | None, text: str) -> str:
    """Cache key for one synthesized segment. EVERY knob that changes the audio
    MUST appear here — a missing input would serve stale audio on resume
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
    (job.tts_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def _unvoiceable(exc: Exception) -> bool:
    """edge-tts raises NoAudioReceived when a line contains nothing the picked
    voice can speak — e.g. pure-Chinese text on a vi-VN voice, which is exactly
    what the no-keys passthrough smoke path produces. Deterministic, so retrying
    is waste and aborting the render at its LAST stage is hostile; the caller
    skips that line's dub instead (subtitle still shows). Matched by class name
    to keep this module free of an edge_tts import."""
    return type(exc).__name__ == "NoAudioReceived"


def _synth_retry(tts, text: str, seg: Path, *, voice, rate, sleep) -> None:
    for delay in (*_RETRY_DELAYS, None):
        try:
            tts.synthesize(text, seg, voice=voice, rate=rate)
            return
        except Exception as exc:  # noqa: BLE001 - transient 403/timeouts get the retries
            if delay is None or _unvoiceable(exc):
                raise
            sleep(delay)


def synthesize(
    job: Job,
    *,
    provider: str = "edge",
    voice: str | None = None,
    rate: str | None = None,
    log: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    _sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[Cue, Path]]:
    tts = get_tts(provider)
    if not tts.available():
        raise RuntimeError(f"TTS provider '{provider}' is not available (missing key or dependency).")

    cues = parse_srt(job.vi_srt)
    ov = load_overrides(job)
    manifest = _load_manifest(job)

    # API-10: batch-capable providers (edge-tts) synthesize the whole job on ONE
    # event loop with bounded concurrency; others keep the per-cue path below.
    if getattr(tts, "supports_batch", False):
        return _synthesize_batch(job, tts, cues, ov, manifest, provider=provider,
                                 voice=voice, rate=rate, log=log, cancel=cancel)

    segments: list[tuple[Cue, Path]] = []
    kept = made = 0
    for i, c in enumerate(cues):
        if cancel and cancel():
            break  # partial list; the caller's between-stage check aborts the render
        key = str(c.index)
        if (ov[i].get("mute") if i < len(ov) else False) or not c.text.strip():
            manifest.pop(key, None)   # muted/empty line → no dub, and no stale cache
            continue
        seg = job.tts_dir / f"seg_{c.index:04d}.mp3"
        want = _seg_hash(provider, voice, rate, c.text)
        if seg.exists() and seg.stat().st_size > 0 and manifest.get(key) == want:
            kept += 1
        else:
            try:
                _synth_retry(tts, c.text, seg, voice=voice, rate=rate, sleep=_sleep)
            except Exception as exc:  # noqa: BLE001
                if _unvoiceable(exc):
                    manifest.pop(key, None)
                    if log:
                        log(f"⚠ Câu {c.index}: giọng đọc không phát âm được văn bản này "
                            f"— bỏ lồng tiếng câu này (phụ đề vẫn hiện).")
                    continue
                raise
            manifest[key] = want
            _save_manifest(job, manifest)   # write-after-each: a crash keeps progress
            made += 1
        segments.append((c, seg))
    _save_manifest(job, manifest)           # persists mute-drops even when made == 0
    if log and kept:
        log(f"↷ Giữ lại {kept} câu đã tạo trước đó, tạo mới {made} câu")
    return segments


def _synthesize_batch(
    job: Job, tts, cues: list[Cue], ov: list[dict], manifest: dict, *,
    provider: str, voice: str | None, rate: str | None,
    log: Callable[[str], None] | None, cancel: Callable[[], bool] | None,
) -> list[tuple[Cue, Path]]:
    """API-10 batch path. Phase A: a sequential scan makes the SAME per-cue decision
    as the fallback (muted/empty → drop cache; cache hit → keep; else → to-do), with
    no network. Phase B: one bounded-concurrency batch on the provider's single event
    loop. Manifest writes + logging happen in on_done, which the provider invokes on
    the loop thread — serialized, so an out-of-order completion can't corrupt the
    index-keyed manifest and a mid-run crash keeps every finished segment."""
    # Phase A — scan.
    todo: list[tuple[str, str, Path]] = []
    want_of: dict[str, str] = {}
    seg_of: dict[str, Path] = {}
    kept = 0
    for i, c in enumerate(cues):
        key = str(c.index)
        if (ov[i].get("mute") if i < len(ov) else False) or not c.text.strip():
            manifest.pop(key, None)          # muted/empty line → no dub, no stale cache
            continue
        seg = job.tts_dir / f"seg_{c.index:04d}.mp3"
        want = _seg_hash(provider, voice, rate, c.text)
        seg_of[key] = seg
        want_of[key] = want
        if seg.exists() and seg.stat().st_size > 0 and manifest.get(key) == want:
            kept += 1
        else:
            todo.append((key, c.text, seg))

    # Phase B — one batch. on_done runs on the loop thread (serialized).
    made = 0
    fatal: Exception | None = None

    def _done(key: str, exc: Exception | None) -> None:
        nonlocal made, fatal
        if exc is None:
            manifest[key] = want_of[key]
            _save_manifest(job, manifest)    # write-after-each: a crash keeps progress
            made += 1
        elif _unvoiceable(exc):
            manifest.pop(key, None)
            if log:
                log(f"⚠ Câu {key}: giọng đọc không phát âm được văn bản này "
                    f"— bỏ lồng tiếng câu này (phụ đề vẫn hiện).")
        elif isinstance(exc, InterruptedError):
            pass                             # cancelled — not fatal, no manifest write
        elif fatal is None:
            fatal = exc                      # first real failure → raise after the drain

    if todo:
        tts.synthesize_many(todo, voice=voice, rate=rate, retry_delays=_RETRY_DELAYS,
                            on_done=_done, cancel=cancel)
    _save_manifest(job, manifest)            # persists mute-drops even when nothing made
    if fatal is not None:
        raise fatal
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
