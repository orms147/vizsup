"""Bộ phim: names + xưng hô learnt from the owner's APPROVED episodes, so every later
episode uses the same Vietnamese forms without the owner writing a note.

Each episode's brief (``pipeline/brief.py``) picks pronouns afresh; across 《末日校车》 01–09
the driver called the students "các em", "các cô", "cô nương" and "các bạn". Before a new
episode is translated, the newest approved earlier episode of the series that was not read
yet is read ONCE (its brief + the approved Chinese → Vietnamese lines, one "brief"-role
call) into a cast sheet, merged into the series profile:

- a character not in the profile is added (``auto: true`` — the Bộ phim dialog shows it and
  the owner may edit or delete it; an edited row is the owner's);
- a known character only gets an empty xưng hô note filled — earlier entries win;
- the general rules (narrator, system voice, how a group is addressed) fill an empty brief.

The sheet then reaches the episode's brief (as fixed input) and every translate batch
(``series.context_for``). What was read is recorded in ``_series/<slug>/cast_learned.json``.
Never raises: no approved episode, no key, a bad reply → the run goes on as before.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from app.fsutil import atomic_write_text

LEARNED = "cast_learned.json"
SAMPLE_CHARS = 5000
MAX_CHARS = 12               # characters taken from one episode
_MAX_TOKENS = 1500
_ATTEMPTS = 2

INSTRUCTION = (
    "You keep the CHARACTER SHEET of a Chinese series that is translated into Vietnamese\n"
    "episode by episode. From ONE APPROVED episode — its Chinese lines with the owner's final\n"
    "Vietnamese — write down who is who and how they address each other, so that every later\n"
    "episode uses the same Vietnamese names and pronouns (xưng hô).\n"
    "Return ONLY a JSON object, no prose, no code fence:\n"
    '{"characters": [{"zh": "顾哥", "vi": "anh Cố", "gender": "nam", '
    '"xung_ho": "xưng \\"tôi\\"; gọi các nữ sinh \\"các cô\\""}], '
    '"chung": "người kể xưng \\"tôi\\", gọi khán giả \\"các bạn\\""}\n'
    "Rules:\n"
    "- The APPROVED Vietnamese lines are the truth: take every name and pronoun from them.\n"
    "  The brief only helps to tell who speaks to whom; where it disagrees, the lines win.\n"
    "- zh = the Chinese name as the lines write it; vi = the Vietnamese form the approved\n"
    "  lines use for that person. gender = \"nam\", \"nữ\" or \"\" when unclear.\n"
    "- xung_ho = in Vietnamese, how this person refers to self and addresses the others\n"
    "  (by name or group), e.g. 'xưng \"em\", gọi 顾哥 là \"anh Cố\"'. Concrete words only —\n"
    "  never alternatives (\"hoặc\", \"tùy\").\n"
    "- Only people who speak or are spoken to and matter to the story; at most 12; skip\n"
    "  one-off extras and anyone you cannot name.\n"
    "- A KNOWN SHEET may be given: it is fixed. Do not repeat its people unless their\n"
    "  xưng hô is missing there, and never contradict it.\n"
    "- chung = at most 40 words of general rules (narrator, a system voice, how a group is\n"
    "  addressed); \"\" if none.\n"
    '- If nothing can be told, return {"characters": [], "chung": ""}.'
)


# --- pure helpers -------------------------------------------------------------------------

def pairs(cn_cues, vi_cues) -> list[str]:
    """``中文 → tiếng Việt`` for each Vietnamese cue, the Chinese being the cues that
    overlap it in time (approved edits merge and split lines, so indexes drift). Pure."""
    out: list[str] = []
    j = 0
    for v in vi_cues:
        text = " ".join(v.text.split())
        if not text:
            continue
        while j < len(cn_cues) and cn_cues[j].end <= v.start:
            j += 1
        zh = []
        k = j
        while k < len(cn_cues) and cn_cues[k].start < v.end:
            if min(cn_cues[k].end, v.end) - max(cn_cues[k].start, v.start) > 0:
                zh.append(" ".join(cn_cues[k].text.split()))
            k += 1
        out.append(f"{' '.join(zh) or '—'} → {text}")
    return out


def parse_cast(reply: str | None) -> dict:
    """The model's JSON → ``{"characters": [{zh, vi, gender, xung_ho}], "chung": str}``.
    Tolerates a code fence or prose around the object; anything malformed → empty."""
    empty = {"characters": [], "chung": ""}
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return empty
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return empty
    if not isinstance(d, dict):
        return empty
    chars = []
    for c in d.get("characters") or []:
        if not isinstance(c, dict):
            continue
        zh = str(c.get("zh") or "").strip()
        vi = str(c.get("vi") or "").strip()
        if not zh or not vi or len(zh) > 20:
            continue
        gender = str(c.get("gender") or "").strip().casefold()
        chars.append({"zh": zh, "vi": vi,
                      "gender": gender if gender in ("nam", "nữ") else "",
                      "xung_ho": " ".join(str(c.get("xung_ho") or "").split())})
    return {"characters": chars[:MAX_CHARS], "chung": " ".join(str(d.get("chung") or "").split())}


def merge(profile, cast: dict) -> list[str]:
    """Merge a parsed cast into the profile (in place). New people are added with
    ``auto: true``; a known person only gets an empty gender / note filled; the general
    rules fill an empty brief. Returns the Vietnamese names that were added. Pure."""
    known = {str(c.get("zh", "")).strip(): c for c in profile.characters}
    added: list[str] = []
    for c in cast.get("characters") or []:
        old = known.get(c["zh"])
        if old is None:
            row = {"zh": c["zh"], "vi": c["vi"], "gender": c["gender"], "note": c["xung_ho"],
                   "auto": True}
            profile.characters.append(row)
            known[c["zh"]] = row
            added.append(c["vi"])
            continue
        if not str(old.get("note") or "").strip() and c["xung_ho"]:
            old["note"] = c["xung_ho"]
        if not str(old.get("gender") or "").strip() and c["gender"]:
            old["gender"] = c["gender"]
    if not profile.brief.strip() and cast.get("chung"):
        profile.brief = cast["chung"]
    return added


# --- what is pending ------------------------------------------------------------------------

def _learned_path(slug: str) -> Path:
    from app.pipeline import series

    return series.root() / slug / LEARNED


def learned(slug: str) -> dict:
    try:
        d = json.loads(_learned_path(slug).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def pending(job) -> Path | None:
    """The newest approved episode of this job's series (an earlier one, or one without
    a number) that has not been read yet; None when there is none."""
    from app.config import settings
    from app.models import Job
    from app.pipeline import approval, series

    lk = series.link_of(job.work_dir)
    if not lk:
        return None
    done = set(learned(lk["slug"]).get("from") or [])
    ep = lk.get("episode")
    best: tuple[float, Path] | None = None
    try:
        dirs = list(Path(settings.vizsup_storage_dir).iterdir())
    except OSError:
        return None
    for d in dirs:
        if (not d.is_dir() or d.name.startswith(("_", ".")) or d.name in done
                or d.resolve() == job.work_dir.resolve() or not (d / "vi.srt").exists()):
            continue
        other = series.link_of(d)
        if not other or other.get("slug") != lk["slug"]:
            continue
        last = other.get("last") if other.get("last") is not None else other.get("episode")
        if ep is not None and last is not None and last >= ep:
            continue                                   # a LATER episode: not read for this one
        try:
            if not approval.is_approved(Job(id=d.name, work_dir=d)):
                continue
        except Exception:  # noqa: BLE001
            continue
        rank = float(last) if last is not None else -1.0
        if best is None or rank > best[0]:
            best = (rank, d)
    return best[1] if best else None


# --- the call -----------------------------------------------------------------------------------

def build_request(profile, work_dir: Path) -> str:
    from app.models import Job
    from app.pipeline import brief, series
    from app.pipeline.srt import parse_srt

    parts = [f"SERIES: 《{profile.name}》"]
    if known := series.sheet(profile):
        parts.append("KNOWN SHEET (fixed):\n" + known)
    b = brief.load_brief(Job(id=work_dir.name, work_dir=work_dir))
    if b:
        parts.append("THIS EPISODE'S BRIEF (written before the owner's review):\n" + b)
    cn = parse_srt(work_dir / "cn.srt") if (work_dir / "cn.srt").exists() else []
    vi = parse_srt(work_dir / "vi.srt")
    sample, used = brief.sample_transcript(pairs(cn, vi), SAMPLE_CHARS)
    parts.append(f"APPROVED LINES (Chinese → the owner's Vietnamese, {used} of {len(vi)}):\n"
                 + sample)
    return "\n\n".join(parts)


def learn(job, provider_name: str, *, log: Callable[[str], None] | None = None) -> list[str]:
    """Read the newest unread approved episode of this job's series into its profile.
    Returns the names added ([] when nothing was read or nothing was new). Never raises."""
    from app.pipeline import series
    from app.providers.registry import get_translator

    say = log or (lambda _m: None)
    try:
        p = series.profile_of(job)
        src = pending(job) if p else None
        if p is None or src is None:
            return []
        prov = get_translator(provider_name)
        if not hasattr(prov, "rewrite") or not prov.available():
            return []
        request = build_request(p, src)
        reply = None
        for attempt in range(_ATTEMPTS):           # an empty / transient reply: one retry
            try:
                reply = prov.rewrite(request, INSTRUCTION, max_tokens=_MAX_TOKENS, role="brief")
                if (reply or "").strip():
                    break
            except Exception:  # noqa: BLE001
                if attempt == _ATTEMPTS - 1:
                    raise
        if "{" not in (reply or ""):                 # no answer: not recorded, read next run
            say("⚠ Không học được xưng hô của bộ phim (AI không trả lời)")
            return []
        cast = parse_cast(reply)
        p = series.profile_of(job) or p              # the dialog may have saved meanwhile
        added = merge(p, cast)
        series.save(p)
        rec = learned(p.slug)
        rec["from"] = [*(rec.get("from") or []), src.name]
        rec["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_write_text(_learned_path(p.slug), json.dumps(rec, ensure_ascii=False, indent=1))
        ep = (series.link_of(src) or {}).get("episode")
        where = f"tập {ep}" if ep is not None else "tập đã duyệt"
        if added:
            say(f"↷ Bộ phim 《{p.name}》: học xưng hô từ {where} — thêm {len(added)} nhân vật "
                f"({', '.join(added[:6])}{'…' if len(added) > 6 else ''})")
        else:
            say(f"↷ Bộ phim 《{p.name}》: đã đọc xưng hô của {where} — không có nhân vật mới")
        return added
    except Exception as exc:  # noqa: BLE001 - an aid; the brief and translate work without it
        say(f"⚠ Không học được xưng hô của bộ phim ({type(exc).__name__}: {exc})")
        return []
