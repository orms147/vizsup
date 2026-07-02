"""Pre-gate speech-fit estimation (pure helpers, N-6 light).

CLAUDE.md: prefer SHORTER Vietnamese translations over heavy speed-up. These
estimates let the translate stage auto-shorten lines that could never fit their
slot, so the user reviews already-fitting lines at the edit gate.
"""
from __future__ import annotations

SYL_PER_SEC = 4.8    # comfortable Vietnamese speaking rate (KrillinAI consensus)
MAX_TEMPO = 1.30     # mirrors assemble.SPEED_MAX (kept literal to avoid an import cycle)


def speech_seconds(vi_text: str) -> float:
    """Rough spoken duration: Vietnamese words ≈ syllables."""
    return len((vi_text or "").split()) / SYL_PER_SEC


def slot_seconds(cues, i: int) -> float:
    """Time budget for cue i: the span to the NEXT cue's start (mirrors the
    assemble planner's gap reclaim); the last cue gets its own duration."""
    c = cues[i]
    if i + 1 < len(cues):
        return max(0.0, cues[i + 1].start - c.start)
    return max(0.0, c.end - c.start)


def fits(vi_text: str, slot: float) -> bool:
    """Can this line be spoken inside its slot even at the tempo cap?"""
    return speech_seconds(vi_text) <= slot * MAX_TEMPO
