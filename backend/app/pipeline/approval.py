"""The human edit gate, made durable: which REVISION of the subtitles was approved.

CLAUDE.md: never spend TTS credits or encode time on un-approved subtitles. The
desktop app used to treat "the user clicked Duyệt" as approval for whatever the
files held later; v2 (qt-handoff) asks the backend to compare the approved revision
with the current one, so an edit made AFTER approving locks rendering again until
the user re-approves — even across screens or restarts.

A revision is a hash of everything that changes what gets voiced or burned in:
vi.srt, the per-cue overrides (gain / mute), per-cue styles, the global style and the
user's own commentary notes.
Editor-only review bookkeeping (lock, "Đã xem") is excluded — marking a line
reviewed must not revoke an approval.
The user's background music (music.json, pipeline/music.py) is excluded too: like the
audio mode and the volumes it is a render-time mix setting — nothing in it is voiced
(no TTS spend) or burned as text — so adding a song must not send the user back to
re-approve the subtitles. Every render reads the current music.json instead.
"""
from __future__ import annotations

import hashlib
import json
import time

from app.models import Job
from app.fsutil import atomic_write_text

_FILE = "approved.json"
_EDITOR_ONLY = ("lock", "reviewed")


def _overrides_bytes(job: Job) -> bytes:
    p = job.work_dir / "overrides.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return b""
    if not isinstance(rows, list):
        return b""
    clean = [{k: v for k, v in r.items() if k not in _EDITOR_ONLY} if isinstance(r, dict) else r
             for r in rows]
    if not any(isinstance(r, dict) and (r.get("gain_db") or r.get("mute") or r.get("voice")) for r in clean):
        return b""          # all-default rows == no overrides file
    return json.dumps(clean, sort_keys=True, ensure_ascii=False).encode("utf-8")


def revision(job: Job) -> str:
    h = hashlib.sha256()
    # commentary.json (F-4): the user's own notes are voiced and burned too
    for name in ("vi.srt", "style_overrides.json", "style.json", "commentary.json"):
        p = job.work_dir / name
        h.update(name.encode())
        h.update(p.read_bytes() if p.exists() else b"")
    h.update(b"overrides.json")
    h.update(_overrides_bytes(job))
    return h.hexdigest()[:16]


def approve(job: Job, *, cues: int | None = None) -> str:
    rev = revision(job)
    atomic_write_text((job.work_dir / _FILE), json.dumps(
        {"revision": rev, "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cues": cues},
        ensure_ascii=False, indent=1), encoding="utf-8")
    try:                                   # Bộ phim: what the user kept becomes the series'
        from app.pipeline import series

        series.learn(job)
    except Exception:  # noqa: BLE001 - learning never blocks approving
        pass
    return rev


def approved_revision(job: Job) -> str | None:
    try:
        return json.loads((job.work_dir / _FILE).read_text(encoding="utf-8")).get("revision")
    except (OSError, ValueError, AttributeError):
        return None


def is_approved(job: Job) -> bool:
    rev = approved_revision(job)
    return bool(rev) and rev == revision(job)
