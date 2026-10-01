"""Which subtitle lines need a human look at the edit gate ("Cần xem lại", v2 editor).

Pure, UI-agnostic. A cue is flagged for any of:
- ``short``   — the slot is under MIN_SECONDS (cloud ASR on music gave whole
                sentences a 0.3 s slot)
- ``overlap`` — it starts before the previous cue ends
- ``long``    — the Vietnamese cannot be spoken in its slot even at the tempo cap
                (same estimate the translate stage uses: fit_estimate)
- ``ocr``     — the Chinese source came from an OCR read marked suspect
- ``term``    — a glossary term's Vietnamese rendering is missing from the line

OCR and glossary reports number cues by their position in cn.srt, which no longer
matches vi.srt once translation deduped repeats and the user split/merged lines —
so those flags are mapped onto the editor's cues by TIME OVERLAP, not by index.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.models import Job
from app.pipeline import fit_estimate

MIN_SECONDS = 0.8
LABELS = {
    "short": "Timing quá ngắn",
    "overlap": "Chồng lấn",
    "long": "Quá dài",
    "ocr": "OCR đáng ngờ",
    "term": "Thiếu thuật ngữ",
}
ORDER = ("short", "overlap", "long", "ocr", "term")


@dataclass(frozen=True)
class Span:
    start: float
    end: float


def _overlaps(a_start: float, a_end: float, spans: list[Span]) -> bool:
    return any(min(a_end, s.end) - max(a_start, s.start) > 0.05 for s in spans)


def cue_issues(cues, *, ocr_spans: list[Span] = (), term_spans: list[Span] = ()) -> list[list[str]]:
    """``cues``: objects with ``start``, ``end``, and Vietnamese text in ``vi`` (editor
    cues) or ``text``. Returns, per cue, its issue keys in ORDER."""
    out: list[list[str]] = []
    for i, c in enumerate(cues):
        text = getattr(c, "vi", None)
        if text is None:
            text = getattr(c, "text", "")
        issues: list[str] = []
        if c.end - c.start < MIN_SECONDS:
            issues.append("short")
        if i > 0 and c.start < cues[i - 1].end - 0.01:
            issues.append("overlap")
        if text.strip() and not fit_estimate.fits(text, fit_estimate.slot_seconds(cues, i)):
            issues.append("long")
        if ocr_spans and _overlaps(c.start, c.end, ocr_spans):
            issues.append("ocr")
        if term_spans and _overlaps(c.start, c.end, term_spans):
            issues.append("term")
        out.append(issues)
    return out


def _cn_spans(job: Job) -> dict[int, Span]:
    from app.pipeline.srt import parse_srt

    if not job.cn_srt.exists():
        return {}
    return {c.index: Span(c.start, c.end) for c in parse_srt(job.cn_srt)}


def _report(job: Job, name: str) -> list:
    try:
        data = json.loads((job.work_dir / name).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def load_flag_spans(job: Job) -> tuple[list[Span], list[Span]]:
    """(ocr_spans, term_spans): time ranges of suspect OCR reads and glossary misses."""
    cn = _cn_spans(job)
    ocr = [cn[r["index"]] for r in _report(job, "ocr_report.json")
           if isinstance(r, dict) and r.get("suspect") and r.get("index") in cn]
    term = [cn[r["index"]] for r in _report(job, "glossary_report.json")
            if isinstance(r, dict) and r.get("index") in cn]
    return ocr, term
