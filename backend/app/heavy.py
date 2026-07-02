"""Process-wide serialization of heavy model work (the 8 GB VRAM rule).

Wrap any stage that loads a big model (ASR, OCR frame loop, Demucs) in
``heavy_task()`` — two such stages must never run concurrently on this card.
RLock, so a stage that internally calls another wrapped stage can't deadlock.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager

_LOCK = threading.RLock()


@contextmanager
def heavy_task(label: str, log: Callable[[str], None] | None = None):
    if not _LOCK.acquire(blocking=False):
        if log:
            log(f"… đợi tác vụ nặng khác kết thúc rồi mới chạy: {label}")
        _LOCK.acquire()
    try:
        yield
    finally:
        _LOCK.release()
