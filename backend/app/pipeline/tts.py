"""Stage: TTS. Synthesize one audio segment per (edited) Vietnamese cue.

Reads ``job.vi_srt`` (post-edit), writes ``job.tts_dir/seg_0001.mp3`` etc.,
and returns the list of (Cue, segment_path) pairs for the assembly stage.
Provider-pluggable; default edge-tts (vi-VN).
"""
from __future__ import annotations

from pathlib import Path

from app.models import Cue, Job
from app.pipeline.srt import parse_srt
from app.providers.registry import get_tts


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
    segments: list[tuple[Cue, Path]] = []
    for c in cues:
        if not c.text.strip():
            continue
        seg = job.tts_dir / f"seg_{c.index:04d}.mp3"
        tts.synthesize(c.text, seg, voice=voice, rate=rate)
        segments.append((c, seg))
    return segments
