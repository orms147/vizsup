"""Optional project rules for softening Vietnamese wording in the output."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import replace

from app.fsutil import atomic_write_text

FILE = "word_filter.json"
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


def normal(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def transform(job):
    """Build a reusable one-pass replacer. Prefer longer phrases and keep casing."""
    enabled, rules = load(job)
    mapping = {" ".join(normal(a).lower().split()): normal(b) for a, b in rules}
    if not enabled or not mapping:
        return str
    terms = sorted(mapping, key=len, reverse=True)
    alternatives = [r"[^\S\r\n]+".join(map(re.escape, term.split())) for term in terms]
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)

    def substitute(match: re.Match) -> str:
        source = match.group()
        target = mapping.get(" ".join(source.lower().split()), source)
        if source.isupper():
            return target.upper()
        if source[0].isupper():
            return target[:1].upper() + target[1:]
        return target

    def apply(text: str) -> str:
        return pattern.sub(substitute, normal(text))

    return apply


def apply_cues(cues: list, apply) -> list:
    return [replace(c, text=apply(c.text)) for c in cues] if apply is not str else cues
