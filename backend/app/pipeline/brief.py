"""Per-video translation brief: a short context sheet the translator reads with
every batch of ONE video (genre, who is speaking to whom, xưng hô, tone, names).

Runs after the Chinese subtitles exist and before translate. Two inputs, both
optional, at least one required:
  - auto    — the draft translator skims the title/description + a transcript sample
  - note    — the user's own words ("vlog nấu ăn, cô ấy gọi chồng là Lão Lý") which
              the LLM expands into a full brief; the note is AUTHORITATIVE — the
              transcript only fills gaps, it never overrides what the user said.
A series episode also gets its series sheet (names + xưng hô fixed across episodes, learnt
from approved episodes by ``pipeline/cast.py``) as fixed input.

The brief is persisted to ``<job>/context_brief.json`` and re-used while its inputs
(note + transcript) are unchanged, so a resumed run never pays for it twice. It
reaches the model through ``translate.translate`` (``context['_brief']``) and is
rendered into the SYSTEM prompt by ``providers.translation._common.system_prompt_for``.

Optional pre-pass: never raises. If the LLM is unavailable or fails, a user note is
used verbatim (still better than nothing); without a note, translation proceeds
without a brief.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from app.models import Job
from app.fsutil import atomic_write_text

BRIEF_FILE = "context_brief.json"
SAMPLE_CHARS = 6000          # transcript budget sent to the brief model
_SAMPLE_BLOCKS = 3           # head / middle / tail — a video's topic drifts
_MAX_TOKENS = 1200
_ATTEMPTS = 2       # the brief has no retry ladder of its own (unlike translate batches)

BRIEF_INSTRUCTION = (
    "You prepare a TRANSLATION BRIEF that a Chinese-to-Vietnamese subtitle translator will\n"
    "read before translating one video. Write it in Vietnamese, as plain text, at most about\n"
    "180 words, using exactly these five headings, each followed by its content:\n"
    "Thể loại & nội dung:\n"
    "Người nói & xưng hô:\n"
    "Giọng điệu:\n"
    "Tên riêng & thuật ngữ:\n"
    "Lưu ý khi dịch:\n"
    "Rules:\n"
    "- Base everything on the material given. If a USER NOTE is present it is authoritative:\n"
    "  keep every fact in it and never contradict it; use the transcript only to fill gaps.\n"
    "- OBSERVED notes come from listening to / looking at the video itself: trust them for\n"
    "  who speaks or sings, gender and age, and choose the Vietnamese pronouns from that\n"
    "  (e.g. a young woman singing to an absent lover → xưng \"em\", gọi \"anh\"). When the\n"
    "  audio and the frames disagree about who speaks or sings, trust the AUDIO (a person\n"
    "  on screen may be silent). The USER NOTE still wins over both.\n"
    "- If the USER NOTE says how a person should be called, use EXACTLY that form in every\n"
    "  section, including the names list (e.g. note says 小王 is called \"em Vương\" → write\n"
    "  `小王 → em Vương`, never a different rendering).\n"
    "- 他 / 她 / 它 are the same spoken word (tā): speech recognition usually writes 他, so a\n"
    "  他 in the transcript is NOT evidence that the person is male.\n"
    "- For SONG lyrics choose the pronouns from the lyrics' own persona (who 我 and 你 are in\n"
    "  the song) — a cover singer's gender may differ from it.\n"
    "- A SERIES SHEET, when present, is fixed for every episode: copy its names and xưng hô\n"
    "  exactly (only the USER NOTE may override it) and add only people new in this episode.\n"
    "- If the OBSERVED notes say the gender is uncertain (CHƯA CHẮC / không rõ), use neutral\n"
    "  forms (mình / bạn, or the name) unless the lines themselves make it clear.\n"
    "- Under xưng hô, state the concrete Vietnamese pronouns to use and keep them fixed, e.g.\n"
    "  'người dẫn xưng \"mình\", gọi khán giả \"các bạn\"; gọi chồng là \"anh\"'.\n"
    "- Under names, list ONLY proper nouns that matter — people, places, brands, channels —\n"
    "  as `中文 → tiếng Việt` (Hán-Việt reading for Chinese names; Latin-script brands\n"
    "  unchanged). Do NOT list ordinary words (dishes, ingredients, specs, technical terms):\n"
    "  the translator handles those, and a wrong entry here would be copied into every line.\n"
    "  If unsure of a rendering, leave the name out.\n"
    "- Be consistent with yourself: one person or place has ONE Vietnamese form everywhere.\n"
    "  Decide — never offer alternatives (\"hoặc …\", \"cần thống nhất\"): an open choice\n"
    "  makes the translator pick differently from batch to batch.\n"
    "- Under notes, name slang, puns or recurring jokes that need care. Do not restate\n"
    "  numbers or facts from the transcript — give guidance, not a summary.\n"
    "- The Uploader is the channel that posted the video, not a person in it: never list the\n"
    "  uploader's name (or a watermark like @name) as a character, a name or a term.\n"
    "- If something cannot be told from the material, say so in a few words; never invent.\n"
    "- Output only the brief — no preamble, no closing remarks."
)


# --- pure helpers -------------------------------------------------------------

def sample_transcript(lines: list[str], budget: int = SAMPLE_CHARS) -> tuple[str, int]:
    """Whole transcript when it fits, else three evenly spaced contiguous blocks
    (start / middle / end) joined with a ``…`` marker. Returns (text, lines_used)."""
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    whole = "\n".join(lines)
    if len(whole) <= budget:
        return whole, len(lines)
    per_block = budget // _SAMPLE_BLOCKS
    n = len(lines)
    blocks: list[list[str]] = []
    used = 0
    for b in range(_SAMPLE_BLOCKS):
        start = (n * b) // _SAMPLE_BLOCKS
        block: list[str] = []
        size = 0
        for ln in lines[start:]:
            if size + len(ln) + 1 > per_block:
                break
            block.append(ln)
            size += len(ln) + 1
        blocks.append(block)
        used += len(block)
    return "\n…\n".join("\n".join(b) for b in blocks if b), used


def build_request(job: Job, cn_lines: list[str], note: str | None,
                  observed: str | None = None, fixed: str | None = None) -> str:
    meta = job.metadata or {}
    parts: list[str] = []
    for label, key in (("Title", "title"), ("Platform", "platform"),
                       ("Uploader", "uploader"), ("Description", "description")):
        v = (meta.get(key) or "")
        if isinstance(v, str) and v.strip():
            parts.append(f"{label}: {v.strip()}")
    if note and note.strip():
        parts.append(f"USER NOTE (authoritative — these are facts):\n{note.strip()}")
    if fixed and fixed.strip():
        parts.append(f"SERIES SHEET (fixed for every episode of this series):\n{fixed.strip()}")
    if observed and observed.strip():
        parts.append(f"OBSERVED (from the video's own audio / frames):\n{observed.strip()}")
    sample, used = sample_transcript(cn_lines)
    if sample:
        total = len([ln for ln in cn_lines if ln and ln.strip()])
        scope = "full" if used >= total else f"{used} of {total} lines, sampled start/middle/end"
        parts.append(f"TRANSCRIPT (Chinese, {scope}):\n{sample}")
    return "\n\n".join(parts)


def _inputs_key(cn_lines: list[str], note: str | None, observed: str | None = None,
                fixed: str | None = None) -> str:
    h = hashlib.sha1()
    h.update((note or "").strip().encode("utf-8"))
    if observed:                                  # adding an observation rebuilds the brief
        h.update(b"\x01" + observed.strip().encode("utf-8"))
    if fixed and fixed.strip():                   # so does a series sheet that changed
        h.update(b"\x02" + fixed.strip().encode("utf-8"))
    h.update(b"\x00")
    h.update("\n".join(cn_lines).encode("utf-8"))
    return h.hexdigest()


# --- persistence --------------------------------------------------------------

def load_brief(job: Job) -> str | None:
    p = job.work_dir / BRIEF_FILE
    if not p.exists():
        return None
    try:
        brief = json.loads(p.read_text(encoding="utf-8")).get("brief")
    except (json.JSONDecodeError, OSError):
        return None
    return brief.strip() if isinstance(brief, str) and brief.strip() else None


def _save(job: Job, *, brief: str, note: str | None, key: str, source: str) -> None:
    atomic_write_text((job.work_dir / BRIEF_FILE), json.dumps(
        {"brief": brief, "note": (note or "").strip(), "inputs": key, "source": source},
        ensure_ascii=False, indent=1), encoding="utf-8")


# --- the pre-pass -------------------------------------------------------------

def build_brief(job: Job, provider_name: str, *, note: str | None = None,
                force: bool = False, log: Callable[[str], None] | None = None,
                observed: str | None = None, fixed: str | None = None) -> str | None:
    """Create (or re-use) the brief for ``job``. Returns the brief text, or None when
    there is nothing to go on. ``fixed`` = the series sheet (``series.sheet``). Never raises."""
    from app.pipeline.srt import parse_srt
    from app.providers.registry import get_translator

    say = log or (lambda _m: None)
    cn_lines = [c.text for c in parse_srt(job.cn_srt)] if job.cn_srt.exists() else []
    key = _inputs_key(cn_lines, note, observed, fixed)

    p = job.work_dir / BRIEF_FILE
    if not force and p.exists():
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
            if saved.get("inputs") == key and (saved.get("brief") or "").strip():
                say("↷ Đã có brief ngữ cảnh cho video này — dùng lại")
                return saved["brief"].strip()
        except (json.JSONDecodeError, OSError):
            pass

    if not cn_lines and not (note and note.strip()):
        return None

    brief = None
    try:
        prov = get_translator(provider_name)
        if hasattr(prov, "rewrite") and prov.available():
            request = build_request(job, cn_lines, note, observed, fixed)
            for attempt in range(_ATTEMPTS):   # an empty/transient reply is worth one retry
                try:
                    reply = prov.rewrite(request, BRIEF_INSTRUCTION, max_tokens=_MAX_TOKENS, role="brief")
                    brief = (reply or "").strip() or None
                    break
                except Exception:  # noqa: BLE001
                    if attempt == _ATTEMPTS - 1:
                        raise
    except Exception as exc:  # noqa: BLE001 - optional pre-pass; never break the run
        say(f"⚠ Không tạo được brief ngữ cảnh ({type(exc).__name__}: {exc})")

    if brief:
        _save(job, brief=brief, note=note, key=key, source=provider_name)
        return brief
    if note and note.strip():
        # LLM unavailable/failed: the user's own words are still the best context we have
        say("ℹ Dùng nguyên văn ghi chú ngữ cảnh của bạn (không tạo được brief bằng AI)")
        _save(job, brief=note.strip(), note=note, key=key, source="note")
        return note.strip()
    return None
