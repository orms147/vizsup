"""Editor-side cue model: pairs Vietnamese (editable) with Chinese (reference).
Bridges the backend Cue/SRT utilities and the editor widgets.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from desktop import paths  # noqa: F401
from app.models import Cue  # noqa: E402
from app.pipeline.srt import parse_srt, write_srt  # noqa: E402

MAX_WPS = 5.0  # words/sec above which a line is "too long to dub"


@dataclass
class EditorCue:
    start: float
    end: float
    vi: str
    zh: str = ""
    gain_db: float = 0.0          # per-line dub volume trim
    mute: bool = False            # drop this line's dub (keep the subtitle)
    style: dict | None = None     # per-line subtitle style override (sparse)
    locked: bool = False          # timing locked against accidental nudges / drags (v2)
    reviewed: str = ""            # fingerprint() at the moment the user marked it "Đã xem"

    @property
    def dur(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def est_speak(self) -> float:
        """Rough estimate of spoken length for the VI text (sec)."""
        words = len(self.vi.split())
        return words / MAX_WPS if words else 0.0

    @property
    def too_long(self) -> bool:
        return self.dur > 0 and self.est_speak > self.dur


def fmt_time(s: float) -> str:
    s = max(0.0, s)
    m = int(s // 60)
    sec = s - m * 60
    ss = int(sec)
    d = int((sec - ss) * 10 + 1e-4)
    return f"{m:02d}:{ss:02d}.{d}"


def parse_time(text: str) -> float | None:
    text = text.strip()
    m = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", text)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(text)
    except ValueError:
        return None


def load_overrides(job) -> list[dict]:
    """Per-cue dub overrides — delegates to the shared backend loader."""
    from app.pipeline import sidecars

    return sidecars.load_overrides(job)


def load_style_overrides(job) -> dict:
    """Per-cue style overrides (string keys) — delegates to the shared loader."""
    from app.pipeline import sidecars

    return sidecars.load_style_overrides(job)


def _zh_ref_path(job):
    return job.work_dir / "zh_ref.json"


def _default_zh(job, n: int) -> list[str]:
    """The naive reference pairing: row i ↔ cn.srt line i."""
    zh = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    return [zh[i].text if i < len(zh) else "" for i in range(n)]


def load_pair(job) -> list[EditorCue]:
    """Load vi.srt (editable) aligned with the Chinese reference + dub & style
    overrides. The zh column prefers ``zh_ref.json`` (written by save_vi when
    structural edits shifted rows off the naive cn.srt index pairing); a
    missing/length-mismatched sidecar degrades to the index pairing — never
    worse than the old behavior."""
    vi = parse_srt(job.vi_srt) if job.vi_srt.exists() else []
    zh_texts = _default_zh(job, len(vi))
    try:
        data = json.loads(_zh_ref_path(job).read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) == len(vi):
            zh_texts = [str(x) for x in data]
    except (OSError, ValueError):
        pass
    from app.pipeline import sidecars

    ov = sidecars.load_overrides(job)
    sov = sidecars.load_style_overrides(job)
    out: list[EditorCue] = []
    for i, c in enumerate(vi):
        o = ov[i] if i < len(ov) else {}
        out.append(EditorCue(start=c.start, end=c.end, vi=c.text, zh=zh_texts[i],
                             gain_db=float(o.get("gain_db", 0.0)), mute=bool(o.get("mute", False)),
                             style=sov.get(str(i + 1)), locked=bool(o.get("lock", False)),
                             reviewed=str(o.get("reviewed", "") or "")))
    return out


def save_vi(job, cues: list[EditorCue]) -> None:
    from app.pipeline import sidecars

    rows = [Cue(index=i + 1, start=c.start, end=c.end, text=c.vi) for i, c in enumerate(cues)]
    write_srt(rows, job.vi_srt)
    sidecars.save_overrides(job, [_override_row(c) for c in cues])
    sidecars.save_style_overrides(job, {str(i + 1): c.style for i, c in enumerate(cues) if c.style})
    # zh reference sidecar: rows are positional, so split/merge/delete shift later
    # rows off the naive cn.srt pairing — persist the editor's zh column whenever
    # it differs (and drop the file when the pairing is redundant again)
    zh_list = [c.zh for c in cues]
    p = _zh_ref_path(job)
    if zh_list != _default_zh(job, len(cues)):
        p.write_text(json.dumps(zh_list, ensure_ascii=False), encoding="utf-8")
    elif p.exists():
        p.unlink()


def _override_row(c: EditorCue) -> dict:
    row: dict = {"gain_db": round(c.gain_db, 2), "mute": c.mute}
    if c.locked:
        row["lock"] = True
    if c.reviewed:
        row["reviewed"] = c.reviewed
    return row


# --- review state ("Đã xem") --------------------------------------------------
# Stored as a fingerprint of what the user looked at, not a bool: ANY later edit to
# the cue's text or timing makes it stale, so "đã xem" can never vouch for content
# the user has not seen (qt-handoff: "sửa cue sẽ làm trạng thái kiểm tra hết hiệu lực").

def fingerprint(c: EditorCue) -> str:
    import hashlib

    raw = f"{c.start:.3f}|{c.end:.3f}|{c.vi}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def is_reviewed(c: EditorCue) -> bool:
    return bool(c.reviewed) and c.reviewed == fingerprint(c)


def mark_reviewed(c: EditorCue) -> None:
    c.reviewed = fingerprint(c)


def copy_cue(c: EditorCue) -> EditorCue:
    """Deep enough for undo snapshots: the style dict is copied, not shared."""
    from dataclasses import replace

    return replace(c, style=dict(c.style) if c.style else None)


# --- timing edits (v2 inspector / keyboard) -------------------------------------
MIN_CUE = 0.2   # seconds — a cue can never be shorter than this after an edit


def clamp_edge(c: EditorCue, which: str, t: float, *, duration: float | None = None) -> bool:
    """Set ``which`` ("start" | "end") to ``t``, kept inside [0, duration] and with
    start < end by at least MIN_CUE. Locked cues are not changed. True if changed."""
    if c.locked:
        return False
    hi = duration if duration and duration > 0 else None
    t = max(0.0, t)
    if hi is not None:
        t = min(t, hi)
    if which == "start":
        t = min(t, c.end - MIN_CUE)
        t = max(0.0, t)
        changed = abs(t - c.start) > 1e-9
        c.start = round(t, 3)
    else:
        t = max(t, c.start + MIN_CUE)
        if hi is not None:
            t = min(t, hi)
        changed = abs(t - c.end) > 1e-9
        c.end = round(t, 3)
    return changed


def nudge(c: EditorCue, which: str, delta_ms: int, *, duration: float | None = None) -> bool:
    return clamp_edge(c, which, (c.start if which == "start" else c.end) + delta_ms / 1000.0,
                      duration=duration)


def split_at(c: EditorCue, t: float) -> tuple[EditorCue, EditorCue] | None:
    """Split at playhead ``t`` (must be strictly inside the cue, MIN_CUE from each
    edge). Words are divided in proportion to time; zh stays on the first half;
    dub + style settings are inherited. None when ``t`` is not a valid cut."""
    if not (c.start + MIN_CUE <= t <= c.end - MIN_CUE):
        return None
    words = c.vi.split()
    k = round(len(words) * (t - c.start) / max(c.dur, 1e-9))
    k = min(max(k, 1 if len(words) > 1 else 0), max(len(words) - 1, 0))
    a = EditorCue(start=c.start, end=round(t, 3), vi=" ".join(words[:k]), zh=c.zh,
                  gain_db=c.gain_db, mute=c.mute, style=c.style)
    b = EditorCue(start=round(t, 3), end=c.end, vi=" ".join(words[k:]), zh="",
                  gain_db=c.gain_db, mute=c.mute, style=c.style)
    return a, b


# --- pure structural edits (policy lives HERE, headless-testable; the editor
# widget methods are thin wrappers) ------------------------------------------

def split_cue(c: EditorCue) -> tuple[EditorCue, EditorCue]:
    """Split at the time midpoint, words halved; both halves inherit the per-line
    dub + style settings (P0-2 / C-3 policy); the zh reference stays on the
    first half."""
    mid = (c.start + c.end) / 2
    words = c.vi.split()
    half = len(words) // 2
    a = EditorCue(start=c.start, end=mid, vi=" ".join(words[:half]), zh=c.zh,
                  gain_db=c.gain_db, mute=c.mute, style=c.style)
    b = EditorCue(start=mid, end=c.end, vi=" ".join(words[half:]), zh="",
                  gain_db=c.gain_db, mute=c.mute, style=c.style)
    return a, b


def merge_cues(a: EditorCue, b: EditorCue) -> EditorCue:
    """Merge b into a: texts join (zh gains a space only between two non-empty
    parts), mute ORs (never silently un-mute), the larger |gain| wins, and the
    FIRST cue's style is kept (C-3 policy)."""
    zh = f"{a.zh} {b.zh}".strip() if (a.zh and b.zh) else (a.zh + b.zh).strip()
    return EditorCue(
        start=a.start, end=b.end,
        vi=(a.vi + " " + b.vi).strip(), zh=zh,
        gain_db=a.gain_db if abs(a.gain_db) >= abs(b.gain_db) else b.gain_db,
        mute=a.mute or b.mute,
        style=a.style,
    )


# --- pure find / replace over the VI column (UX-4) ---------------------------
# Kept here, Qt-free, so the search semantics are headless-testable; the editor's
# find bar is a thin wrapper. Only the VI text is searched — the zh column is a
# read-only reference and must never be rewritten.

def find_matches(cues: list[EditorCue], needle: str, *,
                 case_sensitive: bool = False) -> list[int]:
    """Indices of cues whose VI text contains ``needle``. Empty needle → no match
    (so an empty box never "matches everything" and selects row 0)."""
    if not needle:
        return []
    hay = (lambda s: s) if case_sensitive else str.lower
    n = hay(needle)
    return [i for i, c in enumerate(cues) if n in hay(c.vi)]


def find_next(cues: list[EditorCue], needle: str, start: int, *,
              case_sensitive: bool = False) -> int:
    """Next matching index at or after ``start``, wrapping around; -1 if none.

    ``start`` may be out of range (no selection yet → -1); it is clamped, so the
    first Enter in a fresh find bar lands on the first match in the file.
    """
    hits = find_matches(cues, needle, case_sensitive=case_sensitive)
    if not hits:
        return -1
    start = max(0, start)
    for i in hits:
        if i >= start:
            return i
    return hits[0]                      # wrap


def replace_in_cue(cues: list[EditorCue], idx: int, needle: str, repl: str, *,
                   case_sensitive: bool = False) -> bool:
    """Replace the FIRST occurrence in one cue. True when something changed."""
    if not needle or not (0 <= idx < len(cues)):
        return False
    text = cues[idx].vi
    if case_sensitive:
        pos = text.find(needle)
    else:
        pos = text.lower().find(needle.lower())
    if pos < 0:
        return False
    cues[idx].vi = text[:pos] + repl + text[pos + len(needle):]
    return True


def replace_all(cues: list[EditorCue], needle: str, repl: str, *,
                case_sensitive: bool = False) -> tuple[int, int]:
    """Replace every occurrence across every cue. Returns (occurrences, lines).

    Case-insensitive mode replaces the matched span, not the needle's casing, so
    "Hà nội" → "Hà Nội" works regardless of how each line happened to spell it.
    """
    if not needle:
        return (0, 0)
    occurrences = lines = 0
    flags = 0 if case_sensitive else re.IGNORECASE
    pat = re.compile(re.escape(needle), flags)
    for c in cues:
        new, n = pat.subn(repl, c.vi)
        if n:
            c.vi = new
            occurrences += n
            lines += 1
    return (occurrences, lines)
