"""Soát xưng hô — one pass over the WHOLE translated video that fixes who says "tôi" to whom.

The translator works in batches of ~50 lines and is never told who speaks a line, so the
xưng hô drifts — most often a girl says "tôi" to the man she calls "anh" (four Claude Sonnet
5.5 reviews of 《末日校车》, 2026-11-01: xưng hô stayed ~5.8/10 whatever the prompt rules said).

The refine model reads the video in windows of WINDOW lines (the lines before as context)
with the series sheet / the video brief and only CLASSIFIES:
  CAST — who talks to whom and the pair each uses,
  WHO  — per line: speaker → listener and the self-pronoun(s) the cast allows them.
The fix itself is deterministic (``fix_line``): an unambiguous first-person word (tôi / tao /
tớ) that the cast does not allow for that speaker → listener becomes the allowed one. Nothing
else is touched: em / anh / cô … can be "I" or "you", and a model rewriting them turned
"anh xem" into "em xem" (a first try, 2026-11-01: 11 of 22 rewrites were wrong). The mixed
pairs this leaves (tao … cô) are flagged by vi_lint for the gate. Runs right after translate.
Never raises.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.fsutil import atomic_write_text
from app.models import Job

REPORT = "pronoun_report.json"
WINDOW = 150          # lines per call: whole scenes, still one cheap request
CONTEXT = 20          # earlier lines shown read-only before a window
_MAX_TOKENS = 6000

INSTRUCTION = (
    "You read the Vietnamese subtitles of a Chinese video as ONE conversation and say who\n"
    "speaks each line. You get the cast sheet / brief when there is one, then numbered lines:\n"
    "ZH (the source) and VI (the subtitle). Reply in exactly two sections:\n"
    "CAST:\n"
    "every pair of people who talk to each other here and the pair each uses (speaker's\n"
    "self-pronoun – what they call the listener), e.g. `Cố → các nữ sinh: tôi – các cô (giận:\n"
    "tao – chúng mày)`, `nữ sinh → Cố: em – anh`. Take them from the sheet / brief when given\n"
    "(they are fixed); otherwise decide from age, gender, relation and tone, once.\n"
    "WHO:\n"
    "one line per numbered line: `n: speaker → listener | self: X` where X is EVERY\n"
    "self-pronoun the cast allows this speaker with this listener, separated by `/` (e.g.\n"
    "`self: tôi/tao` for a man who is calm or angry, `self: em` for a girl talking to him).\n"
    "When the cast gives this speaker two pairs by mood (calm / angry, quarrelling /\n"
    "begging), list both unless the mood of the line is unmistakable.\n"
    "Decide the speaker from the ZH and the lines around it, not from the VI pronouns (they\n"
    "may be wrong). Write `n: ?` when the speaker cannot be told and `n: —` for narration, a\n"
    "system voice or a thought. No other text.\n"
)

FIRST = ("tôi", "tao", "tớ")         # always "I" — safe to swap; em / anh / cô … are not
_ALPHA = r"(?<![^\W\d_]){}(?![^\W\d_])"
_WHO = re.compile(r"^\s*(\d+)\s*[:.)]\s*(.+?)\s*(?:\|\s*self\s*[:=]\s*(.*?))?\s*$", re.IGNORECASE)
_HEAD = re.compile(r"^\W*(CAST|WHO)\W*$", re.IGNORECASE)


def allowed_selves(spec: str) -> list[str]:
    """``"tôi/tao"`` → ["tôi", "tao"]: the one-word Vietnamese pronouns in a ``self:`` value,
    in order. Pure."""
    from app.pipeline.vi_lint import PRONOUNS

    out: list[str] = []
    for part in re.split(r"[/,;|]| hoặc ", (spec or "").casefold()):
        w = part.strip(" .`'\"()")
        if w in PRONOUNS and w not in out:
            out.append(w)
    return out


def fix_line(vi: str, allowed: list[str]) -> str | None:
    """``vi`` with every first-person word (tôi / tao / tớ) that ``allowed`` does not list
    replaced by ``allowed[0]`` (capital kept); None when nothing changes, or when the swap
    would mix two pairs in the line ("Giờ tôi bảo … nhà cô" → "tao … cô"). Pure."""
    from app.pipeline.vi_lint import mixed_pair

    if not allowed or not vi:
        return None
    out = vi
    for word in FIRST:
        if word in allowed:
            continue
        pat = re.compile(_ALPHA.format(word), re.IGNORECASE)
        out = pat.sub(lambda m: allowed[0].capitalize() if m[0][0].isupper() else allowed[0], out)
    if out == vi or (mixed_pair(out) and not mixed_pair(vi)):
        return None
    return out


def parse(reply: str, n: int) -> list[tuple[str, list[str]]]:
    """(speaker → listener, allowed self-pronouns) for each of ``n`` numbered lines from a
    CAST / WHO reply; ("", []) where the reply says nothing. Pure."""
    who: list[tuple[str, list[str]]] = [("", [])] * n
    section = ""
    for line in (reply or "").splitlines():
        m = _HEAD.match(line)
        if m:
            section = m[1].upper()
            continue
        if section == "WHO" and (m := _WHO.match(line)) and 1 <= int(m[1]) <= n:
            label = m[2].strip()
            known = "→" in label and "?" not in label        # speaker AND listener told
            who[int(m[1]) - 1] = (label, allowed_selves(m[3] or "") if known else [])
    return who


def window_body(rows: list[tuple[float, float, str, str]], w0: int, head: str = "") -> str:
    """The request for ``rows[w0:w0 + WINDOW]`` (start, end, zh, vi), numbered from 1, after
    up to CONTEXT earlier lines. Pure."""
    before = rows[max(0, w0 - CONTEXT):w0]
    parts = [head] if head else []
    if before:
        parts.append("EARLIER LINES (context only, not numbered):\n" + "\n".join(
            f"   ZH: {zh} | VI: {vi}" for _s, _e, zh, vi in before))
    parts.append("LINES:\n" + "\n".join(
        f"{k}. ZH: {zh}\n   VI: {vi}" for k, (_s, _e, zh, vi) in enumerate(rows[w0:w0 + WINDOW], 1)))
    return "\n\n".join(parts)


def check(job: Job, provider_name: str, *, log: Callable[[str], None] | None = None) -> dict | None:
    """Run the pass over vi.srt. Returns {"fixed": n, "lines": total}, or None when it did not
    run. Never raises."""
    say = log or (lambda _m: None)
    try:
        from app.pipeline import brief, memory, qa, series
        from app.pipeline.srt import parse_srt, write_srt
        from app.providers.registry import get_translator

        prov = get_translator(provider_name)
        if not hasattr(prov, "rewrite") or not prov.available() or not job.vi_srt.exists():
            return None
        rows = qa._pairs(job)
        if not rows:
            return None
        sheet, note = series.sheet_for(job), brief.load_brief(job)
        head = "\n\n".join(x for x in (
            f"SERIES SHEET (fixed for every episode):\n{sheet}" if sheet else "",
            f"VIDEO BRIEF:\n{note}" if note else "") if x)

        def one(w0: int) -> list[tuple[str, list[str]]]:
            n = len(rows[w0:w0 + WINDOW])
            try:
                reply = prov.rewrite(window_body(rows, w0, head), INSTRUCTION,
                                     max_tokens=_MAX_TOKENS, role="refine")
            except Exception:  # noqa: BLE001 - one window failing leaves its lines as they are
                return [("", [])] * n
            return parse(reply, n)

        with ThreadPoolExecutor(3) as ex:
            who = [w for part in ex.map(one, range(0, len(rows), WINDOW)) for w in part]
        cues = parse_srt(job.vi_srt)
        if len(cues) != len(rows):
            return None
        fixed: list[dict] = []
        for c, (label, allowed) in zip(cues, who):
            new = fix_line(c.text, allowed)
            if new:
                fixed.append({"start": c.start, "vi": c.text, "fix": new, "who": label})
                c.text = new
        if fixed:
            write_srt(cues, job.vi_srt)
            memory.save_machine_translation(job)   # the pass is the machine's, not the user's edit
        atomic_write_text(job.work_dir / REPORT, json.dumps(
            {"fixed": fixed, "who": [{"who": w, "self": a} for w, a in who]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"✓ Soát xưng hô cả video: sửa {len(fixed)} câu" if fixed
            else "✓ Soát xưng hô cả video: không thấy câu nào xưng sai")
        return {"fixed": len(fixed), "lines": len(cues)}
    except Exception as exc:  # noqa: BLE001 - an aid, never fatal
        say(f"⚠ Bỏ qua bước soát xưng hô ({type(exc).__name__}: {exc})")
        return None
