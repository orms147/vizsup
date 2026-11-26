"""Dọn dung lượng — what the whole library can give back, in three kinds, safest first.

A finished episode keeps ~3 videos on disk: the download (source.mp4), the render (output.mp4)
and its vertical copy (output.vertical.mp4) — 2.5 GB for one 30-min episode on 2026-11-11 — plus
render caches (tts/, dub/, frames, stems). Only projects that are DONE are looked at: approved,
rendered from the approved revision (``batch.render_state`` is None) and untouched for
``keep_days``; anything still in progress keeps every file.

* ``cache``  — what the app rebuilds by itself (HEAVY_FILES / HEAVY_DIRS: voice segments, dub
  windows, extracted audio, the quick proof, a render cut off mid-encode).
* ``source`` — the downloaded video, only when it can come back (a link, or a local file still on
  disk; a split part's source is a cut of the original — never).
* ``output`` — the finished videos, only when EVERY post planned for the project is already on its
  platform (``autopilot`` posts: done / scheduled / private / draft; none pending, running or
  failed) and at least one exists. Removing them writes ``cleanup.json`` naming the rendered
  revision, so the project still counts as rendered (``rendered``): it is never offered to
  «Dựng hàng loạt» again or re-rendered by the autopilot. Editing and approving again makes it a
  normal "new" render (the revision no longer matches).

Subtitles, settings, the cover (thumb.jpg), the owner's own recordings and every sidecar always
stay. Pure planning here; the desktop moves the paths to the Recycle Bin (``desktop.trash``)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.fsutil import atomic_write_text

MARK = "cleanup.json"
KEEP_DAYS = 3                       # an episode rendered in the last days may still be re-rendered

# media the app rebuilds by itself (the library's "Chỉ xóa file nặng" removes these too)
HEAVY_FILES = ("source.wav", "preview.mp4", "bg.wav", "output.joining.mp4", "_dub_preview.mp3",
               "output.rendering.mp4", "preview.rendering.mp4",   # a render cut off mid-encode
               "vocals.wav", "source.downloading.mp4", "still.png")
HEAVY_DIRS = ("stems", "tts", "dub", "frames", "detect", "_norm",   # _norm: a stopped join
              "upscaled")      # the AI-upscaled copy of the source (pipeline/upscale.py), ~GBs
OUTPUT_FILES = ("output.mp4", "output.vertical.mp4", "output.srt")
OUTPUT_KEEP = ("output.srt",)       # the rendered subtitles are tiny and worth keeping
ON_PLATFORM = ("done", "scheduled", "private", "draft")
KINDS = ("cache", "source", "output")


def _read(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def archived(work_dir: Path) -> bool:
    """The finished video was removed by «Dọn dung lượng» and the approved revision is still the
    one it was rendered from."""
    d = Path(work_dir)
    mark = _read(d / MARK)
    if "output.mp4" not in (mark.get("removed") or []) or (d / "output.mp4").exists():
        return False
    approved = _read(d / "approved.json").get("revision")
    return bool(approved) and mark.get("revision") == approved


def rendered(work_dir: Path) -> bool:
    """output.mp4 on disk, or removed by «Dọn dung lượng» after it was posted (``archived``)."""
    return (Path(work_dir) / "output.mp4").exists() or archived(work_dir)


def posted(job_id: str, posts: list[dict]) -> bool:
    """Every post planned for ``job_id`` is on its platform, and there is at least one. Pure."""
    mine = [p for p in posts if p.get("job") == job_id]
    return bool(mine) and all(p.get("status") in ON_PLATFORM for p in mine)


def refetchable(work_dir: Path) -> bool:
    """source.mp4 can come back: downloaded from a link, or copied from a local file still on
    disk. A split part's source is a cut of the original — never."""
    d = Path(work_dir)
    if _read(d / "metadata.json").get("group"):
        return False
    job = _read(d / "job.json")
    opts = job.get("opts") if isinstance(job.get("opts"), dict) else {}
    if job.get("url") or opts.get("url"):
        return True
    f = opts.get("file")
    return bool(f) and Path(f).exists()


def size_of(path: Path) -> int:
    """Bytes under ``path`` (a file or a folder tree; links not followed)."""
    p = Path(path)
    try:
        if not p.is_dir():
            return p.stat().st_size if os.path.lexists(p) else 0
    except OSError:
        return 0
    total, stack = 0, [str(p)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _done_since(d: Path) -> float | None:
    """When the project was last rendered (output.mp4, else the mark of an earlier clean)."""
    for p in (d / "output.mp4", d / "render.json", d / MARK):
        try:
            return p.stat().st_mtime
        except OSError:
            continue
    return None


def project_plan(work_dir: Path, posts: list[dict], *, now: float | None = None,
                 keep_days: float = KEEP_DAYS) -> dict | None:
    """{id, dir, cache, source, output} (lists of existing paths) for one finished project, or
    None when it is not finished / too recent. ``output`` only when ``posted``."""
    from app.pipeline import batch

    d = Path(work_dir)
    if not (d / "vi.srt").exists() or not rendered(d):
        return None
    try:
        if batch.render_state(d) is not None:          # approved again since: a render is owed
            return None
    except Exception:  # noqa: BLE001 - an unreadable project keeps its files
        return None
    since = _done_since(d)
    now = time.time() if now is None else now
    if since is None or now - since < keep_days * 86400:
        return None
    exists = [n for n in (*HEAVY_FILES, *HEAVY_DIRS) if os.path.lexists(d / n)]
    out = {"id": d.name, "dir": d, "cache": [d / n for n in exists], "source": [], "output": []}
    if (d / "source.mp4").exists() and refetchable(d):
        out["source"] = [d / "source.mp4"]
    if posted(d.name, posts):
        out["output"] = [d / n for n in OUTPUT_FILES
                         if n not in OUTPUT_KEEP and (d / n).exists()]
    return out


def scan(storage: Path, *, posts: list[dict] | None = None, now: float | None = None,
         keep_days: float = KEEP_DAYS) -> list[dict]:
    """``project_plan`` for every project folder under ``storage`` that has something to give,
    each with ``bytes`` per kind. Blocking (walks folders) — off the GUI thread."""
    if posts is None:
        from app.pipeline import autopilot

        try:
            posts = autopilot.load_posts()
        except Exception:  # noqa: BLE001 - no post list: no finished video is removed
            posts = []
    base = Path(storage)
    rows = []
    if not base.is_dir():
        return rows
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")) or not (d / "job.json").exists():
            continue
        plan = project_plan(d, posts, now=now, keep_days=keep_days)
        if plan is None:
            continue
        plan["bytes"] = {k: sum(size_of(p) for p in plan[k]) for k in KINDS}
        if any(plan["bytes"].values()):
            rows.append(plan)
    return rows


def totals(rows: list[dict]) -> dict[str, int]:
    """Bytes per kind over ``scan`` rows. Pure."""
    return {k: sum(r["bytes"].get(k, 0) for r in rows) for k in KINDS}


def paths_for(rows: list[dict], kinds) -> list[Path]:
    """Every path of the chosen kinds. Pure."""
    return [p for r in rows for k in KINDS if k in kinds for p in r[k]]


def mark_removed(work_dir: Path, removed: list[Path]) -> None:
    """Remember that the finished video of ``work_dir`` was removed (call AFTER the paths are
    gone), with the revision it was rendered from — ``archived`` reads it."""
    d = Path(work_dir)
    names = sorted({Path(p).name for p in removed})
    if "output.mp4" not in names:
        return
    old = _read(d / MARK)
    revision = (_read(d / "render.json").get("revision") or old.get("revision")
                or _read(d / "approved.json").get("revision"))
    names = sorted(set(names) | set(old.get("removed") or []))
    atomic_write_text(d / MARK, json.dumps({"removed": names, "revision": revision,
                                            "at": time.time()}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
