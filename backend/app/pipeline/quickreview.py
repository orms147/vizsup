"""«Duyệt nhanh» content, shared by the desktop dialog (desktop/main.py ``_fast_approve``) and
the Telegram remote (app/pipeline/remote.py): the lines still needing a look — flagged and
not marked "Đã xem" — judged exactly as the editor judges them. Reads the project folder
only (no Qt); what happens after approval is ``autopilot.after_approve_text``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.models import Job


@dataclass
class ReviewCue:
    """The editor cue's fields the review flags look at (desktop.cuemodel.EditorCue has more)."""
    start: float
    end: float
    vi: str
    zh: str = ""
    reviewed: str = ""


def fingerprint(start: float, end: float, vi: str) -> str:
    """What "Đã xem" stores: ANY later edit of the cue's text or timing makes it stale."""
    raw = f"{start:.3f}|{end:.3f}|{vi}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def is_reviewed(c) -> bool:
    return bool(getattr(c, "reviewed", "")) and c.reviewed == fingerprint(c.start, c.end, c.vi)


def load_cues(job: Job) -> list[ReviewCue]:
    """vi.srt paired with the Chinese reference (``zh_ref.json`` when it matches, else cn.srt
    line by line) and the "Đã xem" stamps — desktop.cuemodel.load_pair's pairing."""
    from app.pipeline import sidecars
    from app.pipeline.srt import parse_srt

    vi = parse_srt(job.vi_srt) if job.vi_srt.exists() else []
    zh = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    zh_texts = [zh[i].text if i < len(zh) else "" for i in range(len(vi))]
    try:
        data = json.loads((job.work_dir / "zh_ref.json").read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) == len(vi):
            zh_texts = [str(x) for x in data]
    except (OSError, ValueError):
        pass
    ov = sidecars.load_overrides(job)
    out = []
    for i, c in enumerate(vi):
        o = ov[i] if i < len(ov) and isinstance(ov[i], dict) else {}
        out.append(ReviewCue(start=c.start, end=c.end, vi=c.text, zh=zh_texts[i],
                             reviewed=str(o.get("reviewed", "") or "")))
    return out


def voice_speed(job: Job) -> float:
    """The dub speed picked on the render page (voice.json), 1.0 when none."""
    try:
        r = json.loads((job.work_dir / "voice.json").read_text(encoding="utf-8")).get("rate")
        return float(r) if r else 1.0
    except (OSError, ValueError, TypeError, AttributeError):
        return 1.0


def cue_issues(job: Job, cues: list) -> list[list[str]]:
    """Each cue's review flags (keys), exactly as the editor judges them: same speed, same
    Vietnamese lint, same AI review findings, same late-dub rows."""
    from app.pipeline import qa, review, vi_lint

    ocr, term = review.load_flag_spans(job)
    rate = voice_speed(job)
    return review.cue_issues(cues, ocr_spans=ocr, term_spans=term,
                             guess_spans=review.load_guess_spans(job),
                             qa_findings=qa.load(job), rate=rate, lint=vi_lint.context(job),
                             late=review.load_late(job, rate=f"{int(round((rate - 1) * 100)):+d}%"))


def flagged_lines(job: Job) -> list[dict]:
    """Lines still needing a look: {n, start, zh, vi, flags} with the flags' Vietnamese
    labels — what «Duyệt nhanh» shows. [] when nothing is flagged."""
    from app.pipeline import review

    if not job.vi_srt.exists():
        return []
    cues = load_cues(job)
    out = []
    for n, (c, keys) in enumerate(zip(cues, cue_issues(job, cues), strict=False), 1):
        if keys and not is_reviewed(c):
            out.append({"n": n, "start": c.start, "zh": c.zh or "", "vi": c.vi or "",
                        "flags": [review.LABELS.get(k, k) for k in keys]})
    return out
