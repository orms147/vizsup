"""Stage: TTS. Synthesize one audio segment per (edited) Vietnamese cue.

Reads ``job.vi_srt`` (post-edit), writes ``job.tts_dir/seg_0001.mp3`` etc.,
and returns the list of (Cue, segment_path) pairs for the assembly stage.
Provider-pluggable; default edge-tts (vi-VN).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.models import Cue, Job
from app.pipeline.srt import parse_srt
from app.providers.registry import get_tts


def load_overrides(job: Job) -> list[dict]:
    """Per-cue dub overrides (gain_db, mute) aligned with vi.srt order."""
    p = job.work_dir / "overrides.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def synthesize(
    job: Job,
    *,
    provider: str = "edge",
    voice: str | None = None,
    rate: str | None = None,
) -> list[tuple[Cue, Path]]:
    tts = get_tts(provider)
    if not tts.available():
        raise RuntimeError(f"TTS provider '{provider}' is not available (missing key or dependency).")

    cues = parse_srt(job.vi_srt)
    ov = load_overrides(job)
    segments: list[tuple[Cue, Path]] = []
    for i, c in enumerate(cues):
        if (ov[i].get("mute") if i < len(ov) else False) or not c.text.strip():
            continue  # muted line → no dub (subtitle still shows)
        seg = job.tts_dir / f"seg_{c.index:04d}.mp3"
        tts.synthesize(c.text, seg, voice=voice, rate=rate)
        segments.append((c, seg))
    return segments
