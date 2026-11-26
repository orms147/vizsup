"""Optional project rules for softening Vietnamese wording in the output.

The rules live in the project (word_filter.json, part of the approved revision). The owner may
also keep them for NEW projects: in the series profile (`Profile.word_filter`) when the project
is in one, else app-wide (storage/_word_filter.json). `inherit` copies that into a project that
has no rules of its own at the start of a run — never into an approved project."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import replace

from app.fsutil import atomic_write_text

FILE = "word_filter.json"
GLOBAL_FILE = "_word_filter.json"
DEFAULT_RULES = (
    ("đồ súc sinh", "đồ khả ái"),
    ("giết", "dít"),
    ("chết", "chớt"),
    ("đồ khốn kiếp", "đồ đáng ghét"),   # before "đồ khốn" wins the left-most match
    ("đồ khốn", "đồ đáng ghét"),
    ("cút đi", "đi chỗ khác"),
    ("khốn kiếp", "quá đáng"),
)


def load(job) -> tuple[bool, list[tuple[str, str]]]:
    try:
        data = json.loads((job.work_dir / FILE).read_text(encoding="utf-8"))
        return bool(data["enabled"]), [(a, b) for a, b in data["rules"]
                                       if isinstance(a, str) and a.strip() and isinstance(b, str)]
    except (OSError, ValueError, TypeError, KeyError):
        return False, list(DEFAULT_RULES)


def save(job, enabled: bool, rules: list[tuple[str, str]]) -> None:
    atomic_write_text(job.work_dir / FILE, json.dumps(
        {"enabled": enabled, "rules": rules}, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean(data) -> tuple[bool, list[tuple[str, str]]] | None:
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        return None
    rules = [(a, b) for r in data["rules"] if isinstance(r, (list, tuple)) and len(r) == 2
             for a, b in [r] if isinstance(a, str) and a.strip() and isinstance(b, str)]
    return bool(data.get("enabled")), rules


def _global_path():
    from pathlib import Path

    from app.config import settings

    return Path(settings.vizsup_storage_dir) / GLOBAL_FILE


def _global_kept() -> tuple[bool, list[tuple[str, str]]] | None:
    try:
        return _clean(json.loads(_global_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def keep_target(job) -> tuple[str, tuple[bool, list[tuple[str, str]]] | None]:
    """Where «Dùng danh sách này cho các tập mới» saves for this project — its series' name, or
    "" for app-wide — and what is kept THERE (None = nothing)."""
    from app.pipeline import series

    p = series.profile_of(job)
    if p is not None:
        return p.name, _clean(p.word_filter)
    return "", _global_kept()


def kept_for_new(job) -> tuple[str, tuple[bool, list[tuple[str, str]]] | None]:
    """The list a new project of this one's kind takes: its series' own list, else the
    app-wide one (a series made automatically from a title has none). (series name or "",
    list or None)."""
    where, kept = keep_target(job)
    if kept and kept[1]:
        return where, kept
    return "", _global_kept()


def keep_for_new(job, enabled: bool, rules: list[tuple[str, str]] | None) -> None:
    """Keep (or with rules None, forget) the rules for new projects — see :func:`kept_for_new`."""
    from app.pipeline import series

    data = {} if rules is None else {"enabled": bool(enabled), "rules": [list(r) for r in rules]}
    p = series.profile_of(job)
    if p is not None:
        p.word_filter = data
        series.save(p)
    elif data:
        atomic_write_text(_global_path(), json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _global_path().unlink(missing_ok=True)


def inherit(job, *, log=None) -> bool:
    """Start of a run: copy the rules kept for new projects into a project with none of its
    own. Never touches an approved project (its revision must not change)."""
    from app.pipeline import approval

    if (job.work_dir / FILE).exists() or approval.is_approved(job):
        return False
    where, kept = kept_for_new(job)
    if not kept or not kept[1]:
        return False
    save(job, *kept)
    if log:
        log(f"↷ Lọc từ trong bản xuất: dùng danh sách {len(kept[1])} từ"
            + (f" của bộ phim 《{where}》" if where else " đã lưu cho video mới")
            + ("" if kept[0] else " (đang tắt)"))
    return True


def normal(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def transform(job):
    """Build a reusable one-pass replacer. Prefer longer phrases and keep casing."""
    enabled, rules = load(job)
    mapping = {" ".join(normal(a).lower().split()): normal(b) for a, b in rules}
    if not enabled or not mapping:
        return str
    terms = sorted(mapping, key=len, reverse=True)
    # a phrase may be split by the line break of a two-line cue ("Cút\nđi")
    alternatives = [r"\s+".join(map(re.escape, term.split())) for term in terms]
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)

    def substitute(match: re.Match) -> str:
        source = match.group()
        target = mapping.get(" ".join(source.lower().split()), source)
        if source.isupper():
            target = target.upper()
        elif source[0].isupper():
            target = target[:1].upper() + target[1:]
        if "\n" in source and " " in target:     # keep the cue's two lines
            spaces = [i for i, ch in enumerate(target) if ch == " "]
            mid = min(spaces, key=lambda i: abs(i - len(target) / 2))
            target = target[:mid] + "\n" + target[mid + 1:]
        return target

    def apply(text: str) -> str:
        return pattern.sub(substitute, normal(text))

    return apply


def apply_cues(cues: list, apply) -> list:
    return [replace(c, text=apply(c.text)) for c in cues] if apply is not str else cues
