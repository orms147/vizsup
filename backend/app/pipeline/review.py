"""Which subtitle lines need a human look at the edit gate ("Cần xem lại", v2 editor).

Pure, UI-agnostic. A cue is flagged for any of:
- ``short``   — the slot is under MIN_SECONDS (cloud ASR on music gave whole
                sentences a 0.3 s slot)
- ``overlap`` — it starts before the previous cue ends
- ``long``    — the Vietnamese cannot be spoken in its slot even at the tempo cap
                (same estimate the translate stage uses: fit_estimate)
- ``ocr``     — the Chinese source came from an OCR read marked suspect
- ``term``    — a glossary term's Vietnamese rendering is missing from the line
- ``guess``   — ASR timed it by estimate (no heard words anchored it; timing_report)
- ``novoice`` — filler-only Chinese (呼呜 / 啊啊) with no voice under it: likely an
                ASR hallucination on music
- ``ai``      — the AI QA pass (pipeline/qa.py) flagged the line and it is unchanged since
- ``read``    — too fast to READ (F-6): over READ_FLAG_CPS characters per second, or more
                than MAX_LINES lines at CHARS_PER_LINE. The Netflix Vietnamese guide says
                17 cps / 42 per line / 2 lines for TV; real translated Douyin vlogs run at a
                median 22-24 cps, so 17 would flag 70-80 % of lines and drown the list — the
                flag starts at 25 (≈ 1 line in 5 of a fast vlog, none of a song), and the
                inspector shows every line's speed against 17

OCR and glossary reports number cues by their position in cn.srt, which no longer
matches vi.srt once translation deduped repeats and the user split/merged lines —
so those flags are mapped onto the editor's cues by TIME OVERLAP, not by index.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.models import Job
from app.pipeline import fit_estimate
from app.fsutil import atomic_write_text

MIN_SECONDS = 0.6   # 2026-10-27: a 3-word clause timed to the speech (clauses.py) runs ~0.6 s
LABELS = {
    "short": "Timing quá ngắn",
    "overlap": "Chồng lấn",
    "long": "Quá dài",
    "ocr": "OCR đáng ngờ",
    "term": "Thiếu thuật ngữ",
    "guess": "Timing đoán",
    "novoice": "Không nghe thấy giọng",
    "ai": "AI nghi sai",
    "read": "Đọc không kịp",
    "late": "Lồng tiếng trễ",
}
ORDER = ("short", "overlap", "guess", "novoice", "ai", "long", "late", "read", "ocr", "term")
READ_CPS = 17.0          # Netflix Vietnamese Timed Text Style Guide, adults (changelog 2025-12)
READ_FLAG_CPS = 25.0     # flagged above this (see the module docstring)
CHARS_PER_LINE = 42
MAX_LINES = 2
_FILLER = re.compile(r"^[呼呜啊哦嗯哈呀唔嘿喔哎诶噢嗷哇咿吖啦嘛哼\s，。！？、…~～.!?,-]*$")


def _wrapped(line: str, width: int = CHARS_PER_LINE) -> int:
    """Lines a greedy word wrap at ``width`` characters needs. Pure."""
    n, cur = 0, 0
    for word in line.split():
        w = len(word)
        if cur and cur + 1 + w <= width:
            cur += 1 + w
        else:
            n += 1 + (max(0, w - 1) // width)
            cur = w % width or width
    return n


def reading(text: str, seconds: float) -> tuple[float, int]:
    """(characters per second, lines at CHARS_PER_LINE) of one subtitle. Pure."""
    flat = " ".join((text or "").split())
    lines = sum(_wrapped(part) for part in (text or "").splitlines() if part.strip())
    return round(len(flat) / max(seconds, 0.1), 1), lines


@dataclass(frozen=True)
class Span:
    start: float
    end: float


def _overlaps(a_start: float, a_end: float, spans: list[Span]) -> bool:
    return any(min(a_end, s.end) - max(a_start, s.start) > 0.05 for s in spans)


def stamp_fit_report(job: Job, *, rate: str | None) -> None:
    """Record the voice speed ("+30%") the last render's fit_report was measured at."""
    import json

    p = job.work_dir / "fit_report.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
        rows["rate"] = rate or "+0%"
        atomic_write_text(p, json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass


def load_late(job: Job, *, rate: str | None = None) -> list[dict]:
    """The last render's late dub lines (fit_report.json), [] when none / no render —
    or when it was measured at another voice speed than ``rate`` (a 1.0× preview's 522
    late lines say nothing about the 1.3× dub the user is preparing now)."""
    import json

    try:
        rows = json.loads((job.work_dir / "fit_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if rate is not None and isinstance(rows, dict) and rows.get("rate", "+0%") != rate:
        return []
    late = rows.get("late") if isinstance(rows, dict) else None
    return [r for r in late or [] if isinstance(r, dict) and "index" in r]


def late_lines(cues, late: list[dict]) -> dict[int, float]:
    """0-based cue position → seconds late, for report rows that still describe the same
    line (same number AND same text): after a split / merge / rewrite the old measurement
    no longer applies and is dropped. Pure."""
    out: dict[int, float] = {}
    for r in late or []:
        k = int(r.get("index", 0)) - 1
        if 0 <= k < len(cues):
            c = cues[k]
            text = getattr(c, "vi", None)
            text = getattr(c, "text", "") if text is None else text
            if text[:60] == str(r.get("text", "")):
                out[k] = float(r.get("carry_s", 0.0))
    return out


def cue_issues(cues, *, ocr_spans: list[Span] = (), term_spans: list[Span] = (),
               guess_spans: list[Span] = (), voice: list[tuple[float, float]] | None = None,
               qa_findings: list[dict] = (), late: list[dict] = (),
               rate: float = 1.0) -> list[list[str]]:
    """``cues``: objects with ``start``, ``end``, and Vietnamese text in ``vi`` (editor
    cues) or ``text`` (+ optional Chinese ``zh``). ``voice``: VAD spans, when known.
    ``late``: fit_report.json "late" rows from the last render (F-10). ``rate``: the voice
    speed picked for this project ("Tốc độ nói") — "Quá dài" is judged at it. Returns,
    per cue, its issue keys in ORDER."""
    late_at = late_lines(cues, late)
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
        if guess_spans and _overlaps(c.start, c.end, guess_spans):
            issues.append("guess")
        zh = getattr(c, "zh", "") or ""
        if (voice is not None and zh.strip() and _FILLER.match(zh)
                and not any(s < c.end and e > c.start for s, e in voice)):
            issues.append("novoice")
        if qa_findings:
            from app.pipeline import qa

            if qa.findings_for(c, qa_findings):
                issues.append("ai")
        if text.strip() and not fit_estimate.fits(text, fit_estimate.slot_seconds(cues, i),
                                                  rate):
            issues.append("long")
        if i in late_at:
            issues.append("late")
        if text.strip() and "short" not in issues:
            cps, n_lines = reading(text, c.end - c.start)
            if cps > READ_FLAG_CPS or n_lines > MAX_LINES:
                issues.append("read")
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


def load_guess_spans(job: Job) -> list[Span]:
    """Time ranges of cues the ASR stage timed by estimate (timing_report.json)."""
    cn = _cn_spans(job)
    return [cn[r["index"]] for r in _report(job, "timing_report.json")
            if isinstance(r, dict) and r.get("guess") and r.get("index") in cn]


def load_flag_spans(job: Job) -> tuple[list[Span], list[Span]]:
    """(ocr_spans, term_spans): time ranges of suspect OCR reads and glossary misses."""
    cn = _cn_spans(job)
    ocr = [cn[r["index"]] for r in _report(job, "ocr_report.json")
           if isinstance(r, dict) and r.get("suspect") and r.get("index") in cn]
    term = [cn[r["index"]] for r in _report(job, "glossary_report.json")
            if isinstance(r, dict) and r.get("index") in cn]
    return ocr, term
