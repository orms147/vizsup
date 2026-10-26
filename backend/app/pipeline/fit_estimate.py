"""Pre-gate speech-fit estimation (pure helpers, N-6 light).

CLAUDE.md: prefer SHORTER Vietnamese translations over heavy speed-up. These
estimates let the translate stage auto-shorten lines that could never fit their
slot, so the user reviews already-fitting lines at the edit gate.
"""
from __future__ import annotations

# Words (≈ syllables) per second the TTS voice actually speaks at rate 1.0. Measured
# 2026-10-27 on 119 edge-tts NamMinh segments of a real dub: median 2.8, p75 3.0 (incl. the
# mp3's own lead-in/out). The old 4.8 (a "comfortable speaking rate" from KrillinAI) was
# ~70% optimistic: the editor flagged 17 lines "Quá dài", the render then found 522/717 late.
SYL_PER_SEC = 3.0
# Before the gate the dub speed isn't chosen yet: translation budgets / auto-shorten judge
# at the speed the render page suggests to fast talkers, so subtitle-only projects and
# normal speakers aren't cut down to fit a 1.0× voice nobody will use.
PREGATE_RATE = 1.3
MAX_TEMPO = 1.30     # mirrors assemble.SPEED_MAX (kept literal to avoid an import cycle)


def speech_seconds(vi_text: str, rate: float = 1.0) -> float:
    """Rough spoken duration at the voice speed ``rate`` ("Tốc độ nói", 1.0 = normal):
    Vietnamese words ≈ syllables."""
    return len((vi_text or "").split()) / (SYL_PER_SEC * max(0.1, rate))


def slot_seconds(cues, i: int) -> float:
    """Time budget for cue i: the span to the NEXT cue's start (mirrors the
    assemble planner's gap reclaim); the last cue gets its own duration."""
    c = cues[i]
    if i + 1 < len(cues):
        return max(0.0, cues[i + 1].start - c.start)
    return max(0.0, c.end - c.start)


def fits(vi_text: str, slot: float, rate: float = 1.0) -> bool:
    """Can this line be spoken inside its slot even at the tempo cap?"""
    return speech_seconds(vi_text, rate) <= slot * MAX_TEMPO


def late_share(cues, rate: float = 1.0) -> float:
    """Share of non-empty lines that won't fit their slot at voice speed ``rate``. Pure."""
    texts = [(i, getattr(c, "vi", None) or getattr(c, "text", "")) for i, c in enumerate(cues)]
    texts = [(i, t) for i, t in texts if t.strip()]
    if not texts:
        return 0.0
    return sum(not fits(t, slot_seconds(cues, i), rate) for i, t in texts) / len(texts)


def suggest_rate(cues, *, ok_share: float = 0.15, steps=(1.0, 1.1, 1.2, 1.3, 1.4)) -> tuple[float, float]:
    """(slowest voice speed at which ≤ ``ok_share`` of lines run late, that share) — the
    fastest step when none gets there. For a fast talker the render page proposes it
    BEFORE ~15 minutes of TTS, instead of a dub that drifts behind. Pure."""
    for r in steps:
        share = late_share(cues, r)
        if share <= ok_share:
            return r, share
    return steps[-1], late_share(cues, steps[-1])
