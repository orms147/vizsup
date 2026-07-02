"""Quality metrics for pipeline output — pure functions, stdlib only.

The eval scripts (OCR/ASR/translate quality) and the assemble fit report are all
built on these three primitives, so "did this change help?" is always a number,
not a vibe:

- ``cer(hyp, ref)``            — character error rate (Levenshtein / len(ref)).
- ``srt_text_cer(hyp, ref)``   — CER between two SRT files' concatenated texts.
- ``timing_stats(hyp, ref)``   — |Δstart|/|Δend| stats between two cue lists,
                                 matched by maximum time overlap.
"""
from __future__ import annotations

import statistics
from pathlib import Path


def _levenshtein(a: str, b: str) -> int:
    """Edit distance, two-row DP — fine for subtitle-sized strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,          # delete
                cur[j - 1] + 1,       # insert
                prev[j - 1] + (ca != cb),  # substitute
            ))
        prev = cur
    return prev[-1]


def _norm(s: str) -> str:
    """Whitespace never counts toward CER (Chinese has none; VN spacing is layout)."""
    return "".join((s or "").split())


def cer(hyp: str, ref: str) -> float:
    """Character error rate of ``hyp`` against ``ref``, whitespace-insensitive.
    Empty ref → 0.0 when hyp is also empty, else 1.0 (fully wrong)."""
    h, r = _norm(hyp), _norm(ref)
    if not r:
        return 0.0 if not h else 1.0
    return _levenshtein(h, r) / len(r)


def srt_text_cer(hyp_path: Path, ref_path: Path) -> float:
    """CER between the concatenated cue texts of two SRT files (timing ignored)."""
    from app.pipeline.srt import parse_srt

    hyp = "".join(c.text for c in parse_srt(Path(hyp_path)))
    ref = "".join(c.text for c in parse_srt(Path(ref_path)))
    return cer(hyp, ref)


def _dist_stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "mean": None, "median": None, "p95": None}
    xs = sorted(xs)
    p95_ix = max(0, min(len(xs) - 1, round(0.95 * len(xs)) - 1))
    return {
        "n": len(xs),
        "mean": round(sum(xs) / len(xs), 4),
        "median": round(statistics.median(xs), 4),
        "p95": round(xs[p95_ix], 4),
    }


def timing_stats(hyp_cues, ref_cues) -> dict:
    """Timing accuracy of ``hyp_cues`` against a reference: each ref cue is matched
    to the hyp cue with the largest time overlap (unmatched refs are counted but
    contribute no deltas). Returns |Δstart|/|Δend| distributions in seconds."""
    hyp_cues, ref_cues = list(hyp_cues), list(ref_cues)

    def overlap(a, b) -> float:
        return max(0.0, min(a.end, b.end) - max(a.start, b.start))

    d_start: list[float] = []
    d_end: list[float] = []
    matched = 0
    for r in ref_cues:
        best, best_ov = None, 0.0
        for h in hyp_cues:
            ov = overlap(h, r)
            if ov > best_ov:
                best, best_ov = h, ov
        if best is None:
            continue
        matched += 1
        d_start.append(abs(best.start - r.start))
        d_end.append(abs(best.end - r.end))
    return {
        "matched": matched,
        "ref_total": len(ref_cues),
        "d_start": _dist_stats(d_start),
        "d_end": _dist_stats(d_end),
    }
