"""E-5 "AI soát": a strong model reads each Chinese → Vietnamese pair after the
translation and points at the lines a human should look at — meaning errors, wrong
xưng hô, names, omissions, unnatural Vietnamese, too long to dub — each with a short
note and a proposed fix.

It never edits vi.srt. Findings go to ``qa_report.json`` keyed by TIME and by the
Vietnamese text they were about (``vi``), so the editor shows a finding only while
the line is still unchanged: editing the line (or "Dùng gợi ý") clears it.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from app.models import Job
from app.providers.registry import get_translator
from app.fsutil import atomic_write_text

BATCH = 40
# Lines are cut from running speech, so a sentence often spans two or three of them.
# Judged alone, a fragment looked wrong (job 703e…, 2026-10-26: "…là viền đen rộng hơn"
# flagged "meaning" because 黑边 sits in the NEXT Chinese line; the next line flagged
# "câu bị tách rời"). Each batch is therefore sent as a run of consecutive lines with
# CONTEXT lines from the neighbouring batches on both sides, marked as not to be judged.
CONTEXT = 2                       # context lines before and after each batch
CONTEXT_CHARS = 60                # …shortened: they are there for continuity, not review
KINDS = {"meaning": "Sai nghĩa", "pronoun": "Xưng hô", "name": "Tên riêng",
         "omission": "Thiếu ý", "unnatural": "Chưa tự nhiên", "too_long": "Quá dài"}
_REPORT = "qa_report.json"

INSTRUCTION = (
    "You are a strict professional Chinese→Vietnamese subtitle editor. You receive "
    "numbered lines: the Chinese source (ZH) and its Vietnamese subtitle (VI). Flag ONLY "
    "lines with a real problem a viewer would notice: meaning error (meaning), wrong or "
    "inconsistent xưng hô / pronouns for these speakers (pronoun), mistranslated or "
    "inconsistent proper name (name), dropped content (omission), stiff/unnatural "
    "Vietnamese (unnatural), clearly too long to say in a short subtitle (too_long). "
    "The lines are CONSECUTIVE subtitle cues cut from running speech: one sentence often "
    "starts in one line and ends in the next. Read every line together with the line "
    "before and the line after it, and judge the meaning across the boundary: a word "
    "that sits in a neighbouring ZH line is translated in that neighbour's VI, so never "
    "flag a line for content that is in an adjacent line (that is not an omission nor a "
    "meaning error), and never flag a line just because it is a fragment of a sentence "
    "that continues before or after it. Lines marked CONTEXT ONLY are the neighbours "
    "outside this batch, shown only for that purpose: do not judge them, never list them. "
    "Do not flag style preferences or fine lines. For each flagged line give a one-"
    "sentence note in Vietnamese and a corrected Vietnamese line (fix) that keeps the "
    "same length or shorter and covers only that line's part of the sentence. Reply ONLY "
    "with JSON: "
    '{"issues":[{"i":3,"kind":"meaning","note":"…","fix":"…"}]} '
    '(empty list when all lines are fine).'
)

_JSON = re.compile(r"\{.*\}", re.S)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _pairs(job: Job) -> list[tuple[float, float, str, str]]:
    """(start, end, zh, vi) for each Vietnamese line, paired like the editor does:
    the editor's own ``zh_ref.json`` (kept through splits/merges), else line order when
    the counts match, else the Chinese lines overlapping in time. (Time-only pairing
    gave stacked ASR cues several sources each → false "omission" findings.)"""
    from app.pipeline.srt import parse_srt

    cn = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    vi = parse_srt(job.vi_srt)
    zh: list[str] | None = None
    try:
        ref = json.loads((job.work_dir / "zh_ref.json").read_text(encoding="utf-8"))
        if isinstance(ref, list) and len(ref) == len(vi):
            zh = [str(x) for x in ref]
    except (OSError, ValueError):
        pass
    if zh is None and len(cn) == len(vi):
        zh = [c.text for c in cn]
    if zh is None:
        zh = [" ".join(c.text for c in cn if c.start < v.end - 0.05 and c.end > v.start + 0.05)
              for v in vi]
    return [(v.start, v.end, z, v.text) for v, z in zip(vi, zh)]


def _short(s: str, n: int = CONTEXT_CHARS) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def batch_body(pairs: list[tuple[float, float, str, str]], b0: int, head: str = "") -> str:
    """The prompt body for ``pairs[b0:b0 + BATCH]``: the lines to judge, numbered from 1,
    between up to CONTEXT unnumbered, shortened lines on each side marked CONTEXT ONLY
    (the end of the previous batch, the start of the next), so a sentence cut at a batch
    edge reads whole."""
    chunk = pairs[b0:b0 + BATCH]
    before = pairs[max(0, b0 - CONTEXT):b0]
    after = pairs[b0 + BATCH:b0 + BATCH + CONTEXT]

    def ctx(rows, where: str) -> str:
        return "\n".join([f"CONTEXT ONLY — {where} (do not judge):",
                          *(f"   ZH: {_short(zh)} | VI: {_short(vi)}" for _s, _e, zh, vi in rows)])

    review = "Lines to review:\n" + "\n".join(
        f"{k + 1}. ZH: {zh}\n   VI: {vi}" for k, (_s, _e, zh, vi) in enumerate(chunk))
    parts = ([ctx(before, "the lines just before")] if before else []) + [review] + (
        [ctx(after, "the lines just after")] if after else [])
    return head + "\n\n".join(parts)


def parse_issues(reply: str, n: int) -> list[dict]:
    m = _JSON.search(reply or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for it in data.get("issues") or []:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("i"))
        except (TypeError, ValueError):
            continue
        kind = str(it.get("kind") or "").strip()
        if 1 <= i <= n and kind in KINDS:
            out.append({"i": i, "kind": kind, "note": str(it.get("note") or "").strip(),
                        "fix": str(it.get("fix") or "").strip()})
    return out


def review(job: Job, provider_name: str, *, context: dict | None = None, log=None) -> int:
    """Run the QA pass over vi.srt and write qa_report.json. Returns the number of
    flagged lines. Never raises (an aid — the run goes on without it)."""
    say = log or (lambda _m: None)
    try:
        prov = get_translator(provider_name)
        if not hasattr(prov, "rewrite") or not prov.available() or not job.vi_srt.exists():
            return 0
        pairs = _pairs(job)
        brief = (context or {}).get("_brief") or ""
        head = f"Video context:\n{brief}\n\n" if brief else ""

        def one(b0: int) -> list[dict]:
            chunk = pairs[b0:b0 + BATCH]
            body = batch_body(pairs, b0, head)
            try:
                reply = prov.rewrite(body, INSTRUCTION, max_tokens=2500, role="qa")
            except Exception:  # noqa: BLE001
                return []
            found = []
            for it in parse_issues(reply, len(chunk)):
                s, e, _zh, vi = chunk[it["i"] - 1]
                if _norm(it["fix"]) == _norm(vi):
                    continue                     # "fix" identical to the line: no finding
                found.append({"start": s, "end": e, "vi": vi, "kind": it["kind"],
                              "note": it["note"], "fix": it["fix"]})
            return found

        with ThreadPoolExecutor(3) as ex:
            findings = [f for part in ex.map(one, range(0, len(pairs), BATCH)) for f in part]
        atomic_write_text((job.work_dir / _REPORT), json.dumps(findings, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        say(f"✓ AI soát bản dịch: {len(findings)} câu nên xem lại" if findings
            else "✓ AI soát bản dịch: không thấy câu nào sai rõ")
        return len(findings)
    except Exception as exc:  # noqa: BLE001
        say(f"⚠ Bỏ qua bước AI soát ({type(exc).__name__}: {exc})")
        return 0


def load(job: Job) -> list[dict]:
    try:
        data = json.loads((job.work_dir / _REPORT).read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def findings_for(cue, findings: list[dict]) -> list[dict]:
    """Findings that still apply to this editor cue: they overlap it in time AND the
    line still reads exactly as when it was reviewed."""
    text = _norm(getattr(cue, "vi", None) or getattr(cue, "text", ""))
    return [f for f in findings
            if min(cue.end, f["end"]) - max(cue.start, f["start"]) > 0.05
            and _norm(f.get("vi", "")) == text]
