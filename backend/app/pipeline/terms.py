"""ENG-1: auto term-extraction pre-pass + glossary compliance check.

BEFORE translating (opt-in), ask the draft translator to pull recurring proper nouns
/ brands / personas out of the CN transcript and suggest VI renderings — a
LOW-authority glossary (precedence: suggested < global < job, so your own entries
always win) that keeps names consistent across the video. AFTER translating, a pure
check flags lines where a glossary term's VI target is missing, so the user fixes
them at the gate. Suggestions and warnings are advisory — never silently authoritative.
"""
from __future__ import annotations

import json
import re

from app.models import Job
from app.providers.registry import get_translator

_TERM_BATCH = 200   # CN lines per extraction request (terms need broad context)
# strip a leading bullet and a list-numbering prefix ("1. " / "2) ") ONLY — a bare
# leading digit is part of the term (brands like 360, 4399, 3M), so we require the
# numbering to be digits + . or ) + whitespace, never a plain digit run.
_TERM_PREFIX = re.compile(r"^\s*[-*•]?\s*(?:\d+[.)]\s+)?")

EXTRACT_INSTRUCTION = (
    "You build a translation glossary from Chinese video subtitles. From the lines "
    "below, list ONLY the recurring PROPER NOUNS — person/persona names, brands, "
    "product names, place names — that must stay consistent across the whole video, "
    "each with a natural Vietnamese rendering.\n"
    "Rules:\n"
    "- Only terms that RECUR or clearly matter; skip common words and one-offs.\n"
    "- Output ONLY lines of the form `zh => vi`, one per term. No numbering, no "
    "commentary. If there are no such terms, output nothing."
)


def parse_terms(reply: str) -> dict[str, str]:
    """Tolerant parse of ``zh => vi`` lines → ``{zh: vi}``. Ignores anything that
    isn't a term line; a malformed/empty reply yields ``{}`` (never raises)."""
    out: dict[str, str] = {}
    for line in (reply or "").splitlines():
        if "=>" not in line:
            continue
        zh, _, vi = line.partition("=>")
        zh = _TERM_PREFIX.sub("", zh.strip()).strip()
        vi = vi.strip().strip('"').strip()
        if zh and vi and len(zh) <= 40:
            out.setdefault(zh, vi)   # first rendering wins (stable across chunks)
    return out


def extract_terms(cues, provider_name: str, *, context=None) -> dict[str, str]:
    """One chunked LLM pass over the CN transcript → ``{zh: vi}`` suggested glossary.
    Reuses the draft translator's single-shot ``rewrite()``. Returns ``{}`` when the
    provider can't help (no rewrite / unavailable) or nothing recurs — never raises,
    so an optional pre-pass can't break the run."""
    prov = get_translator(provider_name)
    if not hasattr(prov, "rewrite") or not prov.available():
        return {}
    texts = [c.text for c in cues if (c.text or "").strip()]
    merged: dict[str, str] = {}
    for i in range(0, len(texts), _TERM_BATCH):
        body = "\n".join(texts[i:i + _TERM_BATCH])
        try:
            reply = prov.rewrite(body, EXTRACT_INSTRUCTION, max_tokens=1000, role="terms")
        except Exception:  # noqa: BLE001 - optional pre-pass; never break the run
            continue
        for zh, vi in parse_terms(reply).items():
            merged.setdefault(zh, vi)
    return merged


def suggest_glossary(job: Job, provider_name: str, *, context=None, log=None) -> int:
    """Run the extraction pre-pass and write ``<job>/glossary.suggested.json`` (sorted,
    or removed when empty). Returns the term count. load_glossary merges this at the
    lowest precedence, so the user's global/job entries always override a suggestion."""
    from app.pipeline.srt import parse_srt

    if not job.cn_srt.exists():
        return 0
    terms = extract_terms(parse_srt(job.cn_srt), provider_name, context=context)
    path = job.work_dir / "glossary.suggested.json"
    if terms:
        path.write_text(json.dumps(dict(sorted(terms.items())), ensure_ascii=False, indent=1),
                        encoding="utf-8")
        if log:
            log(f"✓ Gợi ý {len(terms)} thuật ngữ (tự nạp khi dịch; bản của bạn luôn thắng)")
    else:
        path.unlink(missing_ok=True)
    return len(terms)


def check_glossary(cn_cues, vi_cues, glossary: dict[str, str]) -> list[dict]:
    """For each glossary source term present in a CN line, verify its VI target
    appears in the paired VI line — case-insensitive but diacritics-exact (``lower()``
    keeps Vietnamese marks). Returns the violations ``[{index, term, expected}]``.
    Pure; cn_cues and vi_cues must be 1:1 aligned (post-dedupe)."""
    out: list[dict] = []
    for cn, vi in zip(cn_cues, vi_cues):
        cn_text = cn.text or ""
        vi_low = (vi.text or "").lower()
        for zh, target in glossary.items():
            if zh and target and zh in cn_text and target.lower() not in vi_low:
                out.append({"index": cn.index, "term": zh, "expected": target})
    return out


def write_glossary_report(job: Job, cn_cues, vi_cues, glossary: dict[str, str] | None) -> int:
    """Run check_glossary and persist ``<job>/glossary_report.json`` (or remove it
    when clean). Returns the violation count for the caller to log."""
    violations = check_glossary(cn_cues, vi_cues, glossary) if glossary else []
    path = job.work_dir / "glossary_report.json"
    if violations:
        path.write_text(json.dumps(violations, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return len(violations)
