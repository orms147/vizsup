"""What the app was doing — kept on disk so a crash, a forced close or a power cut
doesn't lose the plan.

``storage/_session.json`` holds the runs in flight: the video being processed to the
edit gate, the gate queue, a render, the batch-render queue. The main window rewrites it
(atomically) whenever one of them changes and deletes it when everything is done. When
the next start finds it, the previous run ended with work unfinished → the user is
offered to continue; every stage resumes from what is on disk (nothing is redone that
already finished)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.fsutil import atomic_write_text

FILE = "_session.json"


def _path() -> Path:
    from desktop import store

    return store._storage() / FILE


def has_work(state: dict | None) -> bool:
    s = state or {}
    return bool(s.get("gate") or s.get("queue") or s.get("render") or s.get("batch"))


def save(state: dict) -> None:
    """Write the current runs, or remove the file when there are none. Never raises."""
    try:
        p = _path()
        if not has_work(state):
            p.unlink(missing_ok=True)
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps({**state, "pid": os.getpid(), "saved": int(time.time())},
                                        ensure_ascii=False, indent=1, default=str))
    except OSError:
        pass


def load() -> dict | None:
    """The previous run's unfinished work, or None (no file, unreadable, or written by
    this very process)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("pid") == os.getpid() or not has_work(data):
        return None
    return data


def clear() -> None:
    try:
        _path().unlink(missing_ok=True)
    except OSError:
        pass


def describe(state: dict, title_of) -> list[str]:
    """Lines for the "continue?" dialog. ``title_of(job_id)`` → a display title."""
    out = []
    if state.get("gate"):
        out.append(f"Đang dịch: {title_of(state['gate'].get('job_id'))}")
    if state.get("queue"):
        out.append(f"{len(state['queue'])} video trong hàng đợi dịch")
    if state.get("render"):
        out.append(f"Đang dựng: {title_of(state['render'].get('id'))}")
    if state.get("batch"):
        out.append(f"{len(state['batch'])} video chờ dựng hàng loạt")
    return out
