"""Lightweight persistence for the desktop app:
- UI choices from step 1 (so they're remembered next launch) → storage/ui_state.json
- Per-job metadata (job.json in each work dir) so past projects can be listed and
  resumed straight to the edit gate without re-downloading / re-running ASR.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from desktop import paths  # noqa: F401
from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402


def _storage() -> Path:
    return Path(settings.vizsup_storage_dir)


# --- UI choices ------------------------------------------------------------
_UI_KEYS = ("subtitle_source", "asr", "translator", "refine", "tts",
            "ocr_region", "ocr_proofread", "extract_terms", "auto_context", "options_open",
            "auto_snap", "ai_review", "ai_frames", "split_long", "ocr_engine", "ocr_fast")


def load_ui_state() -> dict:
    p = _storage() / "ui_state.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_ui_state(opts: dict) -> None:
    p = _storage() / "ui_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: opts.get(k) for k in _UI_KEYS if opts.get(k) is not None}
    old = load_ui_state()
    data.update({k: old[k] for k in _OTHER_UI_KEYS if k in old})   # other screens' choices
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# Preferences of other screens (render page): merged in, never cleared by the start screen.
_OTHER_UI_KEYS = ("render_frame", "export_dir", "credit_overlay", "ai_label", "render_res",
                  "render_quality")


def update_ui_state(**kv) -> None:
    """Merge a few remembered choices (``_OTHER_UI_KEYS``) into ui_state.json."""
    p = _storage() / "ui_state.json"
    data = load_ui_state()
    data.update({k: v for k, v in kv.items() if k in _OTHER_UI_KEYS})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# --- jobs ------------------------------------------------------------------
def save_job(job: Job, opts: dict, stage: str = "await_edit") -> None:
    data = {
        "id": job.id,
        "url": job.url,
        "opts": opts,
        "stage": stage,
        "title": job.metadata.get("title") or job.id,
        "platform": job.metadata.get("platform"),
    }
    (job.work_dir / "job.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_jobs(limit: int = 8) -> list[dict]:
    """Recent projects, newest first: ready ones (vi.srt / output.mp4) and, E-11,
    unfinished ones (a source video or cn.srt but no vi.srt yet — a run was cancelled
    or failed) flagged ``unfinished`` so they can be resumed without re-downloading."""
    base = _storage()
    out: list[dict] = []
    if not base.exists():
        return out
    for d in base.iterdir():
        if not is_project_dir(d):    # a split video's parent folder, _models, test leftovers
            continue
        has_vi = (d / "vi.srt").exists()
        has_output = (d / "output.mp4").exists()
        partial = [p for p in (d / "cn.srt", d / "source.mp4") if p.exists()]
        if not (has_vi or has_output or (partial and (d / "job.json").exists())):
            continue
        out.append(_job_row(d))
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


def _job_row(d: Path) -> dict:
    """What job.json / metadata.json say about one project folder (cheap: no media)."""
    has_vi = (d / "vi.srt").exists()
    has_output = (d / "output.mp4").exists()
    stamps = [p for p in (d / "vi.srt", d / "output.mp4", d / "cn.srt", d / "source.mp4",
                          d / "job.json") if p.exists()]
    try:
        mtime = (stamps[0] if stamps else d).stat().st_mtime
    except OSError:
        mtime = 0.0
    info = {"id": d.name, "title": d.name, "opts": {}, "has_vi": has_vi, "platform": None,
            "has_output": has_output, "unfinished": not (has_vi or has_output),
            "url": None, "mtime": mtime, "group": None, "duration": 0.0}
    jf = d / "job.json"
    if jf.exists():
        try:
            saved = json.loads(jf.read_text(encoding="utf-8"))
            info["title"] = saved.get("title") or d.name
            info["platform"] = saved.get("platform")
            info["opts"] = saved.get("opts", {}) or {}
            info["url"] = saved.get("url")
        except Exception:  # noqa: BLE001
            pass
    try:
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        if isinstance(meta, dict):
            info["group"] = meta.get("group")
            if info["title"] == d.name and meta.get("title"):
                info["title"] = meta["title"]      # job.json is written before the download
            try:
                info["duration"] = float(meta.get("duration") or 0)
            except (TypeError, ValueError):
                pass
            info["platform"] = info["platform"] or meta.get("platform")
    except (OSError, ValueError):
        pass
    src = (info["opts"] or {}).get("file")
    if info["title"] == d.name and src:
        info["title"] = Path(src).stem             # a local file: its name, not the job id
    return info


def list_projects(limit: int = 6) -> list[dict]:
    """"Tiếp tục dự án" rows: plain projects, and ONE row per split video
    (``kind="group"``, its parts in order) — six parts must not fill the list."""
    rows: list[dict] = []
    groups: dict[str, dict] = {}
    for j in list_jobs(limit=10_000):
        g = j.get("group")
        if not g:
            rows.append({**j, "kind": "job"})
            continue
        gid = str(g.get("id"))
        row = groups.get(gid)
        if row is None:
            info = _group_file(gid) or {}
            row = {"kind": "group", "id": gid, "title": info.get("title") or j["title"],
                   "total": int(g.get("total") or len(info.get("parts") or [])), "parts": [],
                   "mtime": 0.0, "has_output": (_storage() / gid / "output.mp4").exists(),
                   "url": info.get("url")}
            groups[gid] = row
            rows.append(row)
        row["parts"].append({**j, "index": g.get("index")})
        row["mtime"] = max(row["mtime"], j["mtime"])
    base = _storage()
    for gfile in base.glob("*/group.json") if base.exists() else []:
        gid = gfile.parent.name
        if gid not in groups:                     # every part gone: keep the original findable
            info = _group_file(gid) or {}
            row = {"kind": "group", "id": gid, "title": info.get("title") or gid,
                   "total": len(info.get("parts") or []), "parts": [], "has_output": False,
                   "mtime": gfile.stat().st_mtime, "url": info.get("url")}
            groups[gid] = row
            rows.append(row)
    for row in groups.values():
        row["parts"].sort(key=lambda x: x.get("index") or 0)
    rows.sort(key=lambda x: x["mtime"], reverse=True)
    return rows[:limit]


def _group_file(gid: str) -> dict | None:
    try:
        data = json.loads((_storage() / gid / "group.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def known_urls() -> set[str]:
    """Links already turned into a project (E-10: don't offer them from the clipboard),
    including a split video's original link."""
    out: set[str] = set()
    for j in list_jobs(limit=500):
        for u in (j.get("url"), (j.get("opts") or {}).get("url")):
            if u:
                out.add(str(u).strip())
    base = _storage()
    if base.exists():
        for g in base.glob("*/group.json"):
            info = _group_file(g.parent.name) or {}
            if info.get("url"):
                out.add(str(info["url"]).strip())
    return out


def thumbnail(job_dir: Path, *, width: int = 120) -> Path | None:
    """A small JPEG frame of the project's source (cached as thumb.jpg). Blocking —
    call off the GUI thread."""
    import subprocess

    out = job_dir / "thumb.jpg"
    if out.exists():
        return out
    # the source may have been cleaned away ("Chỉ xóa file nặng"): any video will do
    src = next((job_dir / n for n in ("source.mp4", "output.mp4", "preview.mp4")
                if (job_dir / n).exists()), None)
    if src is None:
        return None
    try:
        from app.ffmpegutil import ffmpeg_bin

        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-ss", "1", "-i", str(src),
                        "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)],
                       capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        return None
    return out if out.exists() else None


def _qa_load(job) -> list:
    from app.pipeline import qa

    return qa.load(job)


def project_summary(job_id: str) -> dict:
    """Status details for the "Tiếp tục dự án" list: cue count, cues still needing
    review, approval, duration, thumbnail. Blocking (reads files, may run ffmpeg)."""
    d = _storage() / job_id
    info: dict = {"id": job_id, "cues": 0, "pending": 0, "approved": False,
                  "duration": 0.0, "thumb": None}
    loaded = load_job(job_id)
    if not loaded:
        return info
    job, _opts = loaded
    try:
        info["duration"] = float(job.metadata.get("duration") or 0)
    except (TypeError, ValueError):
        pass
    if job.vi_srt.exists():
        try:
            from app.pipeline import approval, review
            from desktop import cuemodel

            cues = cuemodel.load_pair(job)
            ocr, term = review.load_flag_spans(job)
            rate = _voice_speed(job)            # same speed as the editor judges at
            issues = review.cue_issues(cues, ocr_spans=ocr, term_spans=term,
                                       guess_spans=review.load_guess_spans(job),
                                       qa_findings=_qa_load(job), rate=rate,
                                       late=review.load_late(
                                           job, rate=f"{int(round((rate - 1) * 100)):+d}%"))
            info["cues"] = len(cues)
            info["pending"] = sum(1 for c, i in zip(cues, issues, strict=False)
                                  if i and not cuemodel.is_reviewed(c))
            info["approved"] = approval.is_approved(job)
        except Exception:  # noqa: BLE001 - a summary must never break the start screen
            pass
    thumb = thumbnail(d)
    info["thumb"] = str(thumb) if thumb else None
    from app import usage

    info["usage"] = usage.label(d)          # E-14: "AI $0.012 · 2 phút 05"
    return info


def _voice_speed(job) -> float:
    """The dub speed picked on the render page (voice.json), 1.0 when none."""
    try:
        r = json.loads((job.work_dir / "voice.json").read_text(encoding="utf-8")).get("rate")
        return float(r) if r else 1.0
    except (OSError, ValueError, TypeError, AttributeError):
        return 1.0


def debug(msg: str) -> None:
    """Append a line to storage/vizsup-debug.log, flushed immediately, so we can
    pinpoint where a native crash happened (no Python traceback on segfault)."""
    try:
        import time

        p = _storage() / "vizsup-debug.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
            f.flush()
    except Exception:  # noqa: BLE001
        pass


# --- library: every project on disk (desktop/views/library_view.py) ------------------
_JOB_ID = re.compile(r"[0-9a-f]{12}")
_PROJECT_FILES = ("job.json", "metadata.json", "source.mp4", "cn.srt", "vi.srt", "output.mp4")
# "Chỉ xóa file nặng": media the app rebuilds by itself (render caches, extracted audio,
# the quick proof). Subtitles, settings, the user's own recordings and output.mp4 stay.
HEAVY_FILES = ("source.wav", "preview.mp4", "bg.wav", "output.joining.mp4", "_dub_preview.mp3")
HEAVY_DIRS = ("stems", "tts", "dub", "frames", "detect")


def is_project_dir(d: Path) -> bool:
    """A project folder: has job.json, or is named like a job id and holds project files.
    Never ``_models`` / ``_browser`` / bench folders, a split video's parent folder
    (group.json — listed through its parts), test leftovers like ``smoke`` or empty dirs."""
    if not d.is_dir() or d.name.startswith(("_", ".")) or (d / "group.json").exists():
        return False
    if (d / "job.json").exists():
        return True
    return bool(_JOB_ID.fullmatch(d.name)) and any((d / f).exists() for f in _PROJECT_FILES)


def _status(has_output: bool, has_vi: bool) -> str:
    return "rendered" if has_output else "review" if has_vi else "unfinished"


def library_items() -> list[dict]:
    """Every project, newest first, a split video as ONE item (``kind="group"``). Each
    item: id, kind, title, mtime, duration, status (rendered | review | unfinished),
    has_source / has_output / has_preview, ``dirs`` (the folders it owns) and, for a
    group, ``parts``. Cheap (file checks + small JSON reads); sizes: library_details."""
    base = _storage()
    rows: list[dict] = []
    if not base.exists():
        return rows
    groups: dict[str, dict] = {}
    for d in base.iterdir():
        if not is_project_dir(d):
            continue
        j = _job_row(d)
        j.update(has_source=(d / "source.mp4").exists(), has_preview=(d / "preview.mp4").exists(),
                 kind="job", dirs=[str(d)], status=_status(j["has_output"], j["has_vi"]))
        g = j.get("group") if isinstance(j.get("group"), dict) else None
        gid = str(g.get("id") or "") if g else ""
        if not gid or Path(gid).name != gid or gid in (".", ".."):   # not a sibling folder
            rows.append(j)
            continue
        row = groups.get(gid) or _group_row(gid)
        groups[gid] = row
        row["parts"].append({**j, "index": g.get("index")})
    for gfile in base.glob("*/group.json"):           # every part gone: the original stays
        gid = gfile.parent.name
        if gid not in groups and not gid.startswith(("_", ".")):
            groups[gid] = _group_row(gid)
    for row in groups.values():
        _finish_group(row)
        rows.append(row)
    rows.sort(key=lambda x: x["mtime"], reverse=True)
    return rows


def _group_row(gid: str) -> dict:
    info = _group_file(gid) or {}
    try:
        duration = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"kind": "group", "id": gid, "title": info.get("title") or "", "parts": [],
            "url": info.get("url"), "duration": duration, "opts": info.get("opts") or {},
            "total": len(info.get("parts") or [])}


def _finish_group(row: dict) -> None:
    parent = _storage() / row["id"]
    parts = sorted(row["parts"], key=lambda x: x.get("index") or 0)
    row["parts"] = parts
    row["total"] = max(row["total"], len(parts))
    row["title"] = row["title"] or (parts[0]["title"] if parts else row["id"])
    joined = (parent / "output.mp4").exists()
    row["has_output"] = joined or (bool(parts) and all(p["has_output"] for p in parts))
    row["has_vi"] = any(p["has_vi"] for p in parts)
    row["has_source"] = (parent / "source.mp4").exists() or any(p["has_source"] for p in parts)
    row["has_preview"] = any(p["has_preview"] for p in parts)
    row["status"] = _status(row["has_output"], row["has_vi"])
    row["unfinished"] = row["status"] == "unfinished"
    row["dirs"] = [p["dirs"][0] for p in parts] + ([str(parent)] if parent.is_dir() else [])
    stamps = [p["mtime"] for p in parts]
    if not stamps:
        try:
            stamps = [(parent / "group.json").stat().st_mtime]
        except OSError:
            stamps = [0.0]
    row["mtime"] = max(stamps)
    row["platform"] = next((p.get("platform") for p in parts if p.get("platform")), None)


def library_item(item_id: str) -> dict | None:
    """The library item for a job id, a split video's group id, or one of its parts."""
    for it in library_items():
        if it["id"] == item_id or any(p["id"] == item_id for p in it.get("parts") or []):
            return it
    return None


def videos_of(item: dict) -> list[tuple[str, Path]]:
    """Playable files, best first: (label, path). A project: bản dựng → bản xem nhanh →
    video gốc. A split video: the joined MP4, each part's best file, the original."""
    out: list[tuple[str, Path]] = []
    if item.get("kind") == "group":
        parent = _storage() / item["id"]
        if (parent / "output.mp4").exists():
            out.append(("Bản ghép đầy đủ", parent / "output.mp4"))
        for p in item.get("parts") or []:
            best = videos_of({**p, "kind": "job"})
            if best:
                label, path = best[0]
                out.append((f"Phần {p.get('index') or '?'} · {label.lower()}", path))
        if (parent / "source.mp4").exists():
            out.append(("Video gốc", parent / "source.mp4"))
        return out
    d = Path(item["dirs"][0])
    for label, name in (("Bản dựng", "output.mp4"), ("Bản xem nhanh", "preview.mp4"),
                        ("Video gốc", "source.mp4")):
        if (d / name).exists():
            out.append((label, d / name))
    return out


def dir_size(path: Path) -> int:
    """Bytes on disk under ``path`` (a file or a folder tree; links not followed)."""
    try:
        if not Path(path).is_dir():
            return Path(path).stat().st_size if os.path.lexists(path) else 0
    except OSError:
        return 0
    total = 0
    stack = [str(path)]
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


def _refetchable(job_row: dict) -> bool:
    """source.mp4 can come back on its own: downloaded from a link, or copied from a local
    file still on disk. A split part's source is a cut of the original — never."""
    if job_row.get("group"):
        return False
    if job_row.get("url") or (job_row.get("opts") or {}).get("url"):
        return True
    f = (job_row.get("opts") or {}).get("file")
    return bool(f) and Path(f).exists()


def heavy_paths(item: dict) -> list[Path]:
    """What "Chỉ xóa file nặng" removes from an item (existing paths only)."""
    rows = item.get("parts") if item.get("kind") == "group" else [item]
    out: list[Path] = []
    for r in rows or []:
        d = Path(r["dirs"][0])
        names = list(HEAVY_FILES) + list(HEAVY_DIRS)
        if _refetchable(r):
            names.insert(0, "source.mp4")
        out += [d / n for n in names if os.path.lexists(d / n)]
    return out


def library_details(items: list[dict]) -> dict[str, dict]:
    """Per item: ``size`` (bytes on disk), ``heavy`` (what "Chỉ xóa file nặng" frees),
    ``approved``. Blocking (walks folders) — call off the GUI thread."""
    from app.pipeline import approval

    out: dict[str, dict] = {}
    for it in items:
        size = sum(dir_size(Path(d)) for d in it.get("dirs") or [])
        heavy = sum(dir_size(p) for p in heavy_paths(it))
        approved = False
        if it.get("kind") == "job" and it.get("has_vi"):
            try:
                d = Path(it["dirs"][0])
                approved = approval.is_approved(Job(id=d.name, work_dir=d))
            except Exception:  # noqa: BLE001 - a status must never break the list
                approved = False
        out[it["id"]] = {"size": size, "heavy": heavy, "approved": approved,
                         "duration": None if it.get("duration") else _probe_length(it)}
    return out


def _probe_length(item: dict) -> float | None:
    """A local-file project has no duration in its metadata: read it from the video."""
    from app.pipeline.assemble import probe_duration

    for d in item.get("dirs") or []:
        for name in ("source.mp4", "output.mp4"):
            p = Path(d) / name
            if p.exists():
                try:
                    return probe_duration(p) or None
                except Exception:  # noqa: BLE001 - a length must never break the list
                    return None
    return None


def library_thumbs(items: list[dict]) -> dict[str, str | None]:
    """Thumbnail per item (cached thumb.jpg, else one ffmpeg frame). Blocking."""
    out: dict[str, str | None] = {}
    for it in items:
        dirs = [Path(p["dirs"][0]) for p in it.get("parts") or []] or \
            [Path(d) for d in it.get("dirs") or []]
        thumb = None
        for d in dirs[:1]:
            if d.is_dir():
                thumb = thumbnail(d)
        out[it["id"]] = str(thumb) if thumb else None
    return out


def trash_paths(targets) -> None:
    """Move project files/folders to the Recycle Bin (desktop/trash.py). Every path must
    sit INSIDE storage/ (never storage itself, never outside) — checked before anything
    moves, so one bad path means nothing is deleted."""
    from desktop import trash

    base = _storage().resolve()
    checked: list[Path] = []
    for p in targets:
        r = Path(p).resolve()
        if r == base or base not in r.parents:
            raise ValueError(f"Không xóa ngoài thư mục dự án: {p}")
        checked.append(r)
    trash.move_to_trash(checked)


def load_job(job_id: str) -> tuple[Job, dict] | None:
    d = _storage() / job_id
    if not d.exists():
        return None
    job = Job(id=job_id, work_dir=d)
    meta = d / "metadata.json"
    if meta.exists():
        try:
            job.metadata = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    opts = {}
    jf = d / "job.json"
    if jf.exists():
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            job.url = data.get("url")
            opts = data.get("opts", {})
        except Exception:  # noqa: BLE001
            pass
    return job, opts


def group_summary(gid: str) -> dict:
    """Progress of a split video for its "Tiếp tục dự án" row: per part
    (has_vi, approved, rendered), the part to open next, thumbnail, total duration.
    Blocking (reads files, may run ffmpeg)."""
    from app.pipeline import split

    info: dict = {"id": gid, "parts": [], "next": None, "thumb": None, "duration": 0.0,
                  "joined": split.joined_is_current(_storage() / gid, _storage())}
    group = split.load_group(_storage() / gid)
    if not group:
        return info
    info["duration"] = float(group.get("duration") or 0)
    ready = split.readiness(group, _storage())
    info["parts"] = ready
    order = (lambda r: r["has_vi"] and not r["approved"],     # waiting for review
             lambda r: r["approved"] and not r["rendered"],    # approved, not rendered
             lambda r: not r["has_vi"])                         # still processing
    alive = [r for r in ready if (_storage() / r["id"] / "job.json").exists()]  # not deleted
    for want in order:
        hit = next((r for r in alive if want(r)), None)
        if hit:
            info["next"] = hit["id"]
            break
    if info["next"] is None and alive:
        info["next"] = alive[0]["id"]
    first = _storage() / ready[0]["id"] if ready else None
    thumb = thumbnail(first) if first is not None and first.is_dir() else None
    info["thumb"] = str(thumb) if thumb else None
    return info
