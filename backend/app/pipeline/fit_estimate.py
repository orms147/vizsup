"""Pre-gate speech-fit estimation (pure helpers, N-6 light).

CLAUDE.md: prefer SHORTER Vietnamese translations over heavy speed-up. These
estimates let the translate stage auto-shorten lines that could never fit their
slot, so the user reviews already-fitting lines at the edit gate.
"""
from __future__ import annotations

# Words (≈ syllables) per second the voice speaks at rate 1.0, SPEECH ONLY — measured
# 2026-10-30 on 269 real edge-tts NamMinh segments at "+30%" after their silence was cut
# (pipeline/tts.py trim): dur = 0.26 s + 0.166 s/word → 6.0 words/s at 1.3× = 4.6 at 1.0×
# (lines vary ±0.13 s around that). The old 3.0 (2026-10-27) included the ~0.9 s of silence
# every TTS segment used to carry, which made the estimate call fitting lines "too long".
SYL_PER_SEC = 4.5
# Fixed time per segment (NOT scaled by the voice speed): the 0.05 s + 0.12 s kept around the
# speech by the trim, plus the voice's own onset / release.
SEG_PAD = 0.25
# Before the gate the dub speed isn't chosen yet: translation budgets / auto-shorten judge
# at the speed the render page suggests to fast talkers, so subtitle-only projects and
# normal speakers aren't cut down to fit a 1.0× voice nobody will use. A project that
# already has a speed (voice.json, render_opts.json, its series' render settings) uses that.
PREGATE_RATE = 1.3
MAX_TEMPO = 1.30     # mirrors assemble.SPEED_MAX (kept literal to avoid an import cycle)


def speech_seconds(vi_text: str, rate: float = 1.0) -> float:
    """Rough spoken duration of one trimmed TTS segment at the voice speed ``rate``
    ("Tốc độ nói", 1.0 = normal): a fixed SEG_PAD plus Vietnamese words ≈ syllables at
    SYL_PER_SEC × rate. 0 for an empty line."""
    words = len((vi_text or "").split())
    return SEG_PAD + words / (SYL_PER_SEC * max(0.1, rate)) if words else 0.0


def word_budget(slot: float, rate: float = 1.0) -> int:
    """How many words fit a ``slot`` (seconds) at voice speed ``rate`` and the tempo cap."""
    return max(1, int((slot * MAX_TEMPO - SEG_PAD) * SYL_PER_SEC * max(0.1, rate)))


def parse_rate(value) -> float | None:
    """"+30%" / "-10%" / 1.3 / "1.3" → 1.3 / 0.9 / 1.3 / 1.3; None when it is none of those."""
    try:
        if isinstance(value, str) and value.strip().endswith("%"):
            return 1.0 + float(value.strip().rstrip("%")) / 100.0
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def job_rate(job) -> float:
    """The voice speed this project will be dubbed at, as far as it is known: its own
    voice.json, else render_opts.json, else its series' render settings, else PREGATE_RATE.
    Clamped to 0.6–1.6. Never raises."""
    import json
    from pathlib import Path

    d = Path(job.work_dir)
    found: float | None = None
    for name in ("voice.json", "render_opts.json"):
        try:
            found = parse_rate(json.loads((d / name).read_text(encoding="utf-8")).get("rate"))
        except (OSError, ValueError, AttributeError):
            found = None
        if found:
            break
    if not found:
        try:
            from app.pipeline import series

            prof = series.profile_of(job)
            found = parse_rate((prof.render or {}).get("rate")) if prof else None
        except Exception:  # noqa: BLE001
            found = None
    return min(1.6, max(0.6, found or PREGATE_RATE))


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


def late_indexes(cues, rate: float = 1.0) -> list[int]:
    """0-based positions of the non-empty lines that won't fit their slot at voice speed
    ``rate`` — the lines behind ``late_share``. Pure."""
    out = []
    for i, c in enumerate(cues):
        t = getattr(c, "vi", None) or getattr(c, "text", "")
        if t.strip() and not fits(t, slot_seconds(cues, i), rate):
            out.append(i)
    return out


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
