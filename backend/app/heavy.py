"""Serialization of heavy model work (the 8 GB VRAM rule) — within this process AND across
processes (the open app, `vizsup autopilot --once` from the Windows task, a CLI render).

Wrap any stage that loads a big model or keeps the graphics card busy (ASR, the OCR frame loop,
Demucs, the AI upscale pass, VieNeu on the card) in ``heavy_task()`` — two such stages must
never run at the same time on this card. Re-entrant per thread (a stage that calls another
wrapped stage can't deadlock); the first entry of a thread also takes an OS file lock on
``<storage>/_gpu.lock`` (released by the OS if the process dies), and ``_gpu.json`` says who
holds it so a waiting stage can tell the owner what it waits for. ``cancel()`` is polled while
waiting (InterruptedError).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

_LOCK = threading.RLock()
_held = threading.local()           # per thread: the depth of nested heavy_task()s
_FILE: dict[str, object] = {}       # the open lock file while a thread holds it
POLL_S = 0.5


def _lock_path() -> Path:
    from app.config import settings

    return Path(settings.vizsup_storage_dir) / "_gpu.lock"


def _try_os_lock(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def holder() -> str:
    """What holds the card right now ('' when unknown)."""
    try:
        d = json.loads((_lock_path().with_name("_gpu.json")).read_text(encoding="utf-8"))
        return str(d.get("label") or "")
    except (OSError, ValueError):
        return ""


def _wait(step: Callable[[], bool], what: str, log, cancel) -> None:
    told = False
    while not step():
        if cancel is not None and cancel():
            raise InterruptedError("Đã hủy.")
        if not told and log:
            who = holder()
            log(f"… đợi card đồ họa rảnh ({who or 'tác vụ nặng khác'} đang chạy) rồi mới chạy: "
                f"{what}")
            told = True
        time.sleep(POLL_S)


@contextmanager
def heavy_task(label: str, log: Callable[[str], None] | None = None,
               cancel: Callable[[], bool] | None = None):
    _wait(lambda: _LOCK.acquire(timeout=POLL_S), label, log, cancel)
    depth = getattr(_held, "depth", 0)
    try:
        if depth == 0:
            path = _lock_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, "a+b")  # noqa: SIM115 - kept open while held
            try:
                _wait(lambda: _try_os_lock(fh), label, log, cancel)
            except BaseException:
                fh.close()
                raise
            _FILE["fh"] = fh
            try:
                path.with_name("_gpu.json").write_text(
                    json.dumps({"label": label, "pid": os.getpid(), "since": time.time()},
                               ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        _held.depth = depth + 1
        try:
            yield
        finally:
            _held.depth = depth
            if depth == 0:
                fh = _FILE.pop("fh", None)
                if fh is not None:
                    _os_unlock(fh)
                    fh.close()  # type: ignore[attr-defined]
    finally:
        _LOCK.release()
