"""Job-dir sidecar files, shared by the backend pipeline AND the desktop editor.

One implementation replaces three near-identical loaders that lived in
pipeline/tts.py, pipeline/assemble.py and desktop/cuemodel.py (and had already
drifted on key types). The ON-DISK schema is unchanged, so existing job dirs
keep working:

- ``overrides.json``       — list[{gain_db, mute, lock?, reviewed?}] aligned with vi.srt
  order. ``lock`` / ``reviewed`` are editor-only (timing lock, "Đã xem" fingerprint);
  the TTS / assemble readers ignore keys they do not use.
- ``style_overrides.json`` — {"<1-based position>": sparse style dict}; string
  keys on disk (JSON), int keys via ``load_cue_styles`` for ``write_ass``.

Cue identity is positional (dev-plan rule): list index = vi.srt order = sidecar
position. Never introduce UUID keys here.
"""
from __future__ import annotations

import json

from app.models import Job
from app.fsutil import atomic_write_text

_OVERRIDES = "overrides.json"
_STYLES = "style_overrides.json"


def load_overrides(job: Job) -> list[dict]:
    """Per-cue dub overrides (gain_db, mute), [] when absent/corrupt."""
    p = job.work_dir / _OVERRIDES
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_overrides(job: Job, rows: list[dict]) -> None:
    """Persist dub overrides; an all-defaults list removes the file."""
    p = job.work_dir / _OVERRIDES
    if any(r.get("gain_db") or r.get("mute") or r.get("lock") or r.get("reviewed")
           for r in rows):
        atomic_write_text(p, json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    elif p.exists():
        p.unlink()


def load_style_overrides(job: Job) -> dict[str, dict]:
    """Per-cue style overrides in disk form (string 1-based-position keys)."""
    p = job.work_dir / _STYLES
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_cue_styles(job: Job) -> dict[int, dict] | None:
    """Same data keyed by int for ``write_ass(cue_styles=…)``; None when empty."""
    try:
        return {int(k): v for k, v in load_style_overrides(job).items()} or None
    except (TypeError, ValueError):
        return None


def save_style_overrides(job: Job, styles: dict[str, dict]) -> None:
    """Persist style overrides; an empty dict removes the file."""
    p = job.work_dir / _STYLES
    if styles:
        atomic_write_text(p, json.dumps(styles, ensure_ascii=False), encoding="utf-8")
    elif p.exists():
        p.unlink()
