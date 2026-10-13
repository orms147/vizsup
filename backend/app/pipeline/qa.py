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

BATCH = 40
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
    "Do not flag style preferences or fine lines. For each flagged line give a one-"
    "sentence note in Vietnamese and a corrected Vietnamese line (fix) that keeps the "
    "same length or shorter. Reply ONLY with JSON: "
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
            body = head + "\n".join(f"{k + 1}. ZH: {zh}\n   VI: {vi}"
                                    for k, (_s, _e, zh, vi) in enumerate(chunk))
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
        (job.work_dir / _REPORT).write_text(json.dumps(findings, ensure_ascii=False, indent=1),
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
