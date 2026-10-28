"""Crash- and power-loss-safe file writes.

A plain ``Path.write_text`` truncates the file first and then writes it: an app killed
or a laptop losing power in between leaves an empty or half file — for vi.srt that is
the user's whole review gone. ``atomic_write_text`` writes a temp file next to the
target, flushes it to the disk (fsync) and only then swaps it in with ``os.replace``
(atomic on NTFS and POSIX), so the target is always either the old or the new content.

Windows: a reader (antivirus, the indexer, our own player) can hold the target open for
a moment and make the replace fail with PermissionError — retried briefly before giving
up (the temp file is removed either way)."""
from __future__ import annotations

import os
import time
from pathlib import Path

_RETRIES = (0.05, 0.1, 0.25, 0.5, 1.0)


def replace(src: Path, dst: Path) -> None:
    """os.replace with a few retries for files briefly locked on Windows."""
    for wait in (*_RETRIES, None):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def atomic_write_bytes(path: Path | str, data: bytes) -> Path:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return path


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, text.encode(encoding))
