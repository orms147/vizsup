"""Lightweight persistence for the desktop app:
- UI choices from step 1 (so they're remembered next launch) → storage/ui_state.json
- Per-job metadata (job.json in each work dir) so past projects can be listed and
  resumed straight to the edit gate without re-downloading / re-running ASR.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from desktop import paths  # noqa: F401
from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402


def _storage() -> Path:
    return Path(settings.vizsup_storage_dir)


# --- UI choices ------------------------------------------------------------
_UI_KEYS = ("subtitle_source", "asr", "translator", "refine", "tts",
            "ocr_region", "ocr_proofread", "extract_terms", "auto_context", "options_open",
            "auto_snap", "ai_review", "ai_frames", "split_long", "ocr_engine")


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
_OTHER_UI_KEYS = ("render_frame", "export_dir")


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
        if not d.is_dir() or (d / "group.json").exists():   # a split video's parent folder
            continue
        has_vi = (d / "vi.srt").exists()
        has_output = (d / "output.mp4").exists()
        partial = [p for p in (d / "cn.srt", d / "source.mp4") if p.exists()]
        if not (has_vi or has_output or (partial and (d / "job.json").exists())):
            continue
        newest = d / "vi.srt" if has_vi else d / "output.mp4" if has_output else partial[0]
        info = {"id": d.name, "title": d.name, "opts": {}, "has_vi": has_vi, "platform": None,
                "has_output": has_output, "unfinished": not (has_vi or has_output),
                "url": None, "mtime": newest.stat().st_mtime, "group": None}
        jf = d / "job.json"
        if jf.exists():
            try:
                saved = json.loads(jf.read_text(encoding="utf-8"))
                info["title"] = saved.get("title") or d.name
                info["platform"] = saved.get("platform")
                info["opts"] = saved.get("opts", {})
                info["url"] = saved.get("url")
            except Exception:  # noqa: BLE001
                pass
        try:
            meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
            info["group"] = meta.get("group") if isinstance(meta, dict) else None
        except (OSError, ValueError):
            pass
        out.append(info)
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


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
    src = job_dir / "source.mp4"
    if out.exists():
        return out
    if not src.exists():
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
            issues = review.cue_issues(cues, ocr_spans=ocr, term_spans=term,
                                       guess_spans=review.load_guess_spans(job),
                                       qa_findings=_qa_load(job))
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


def delete_job(job_id: str) -> None:
    """Delete a project's working directory (only within storage/, for safety)."""
    base = _storage().resolve()
    d = (base / job_id).resolve()
    if d.parent == base and d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


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
    for want in order:
        hit = next((r for r in ready if want(r)), None)
        if hit:
            info["next"] = hit["id"]
            break
    if info["next"] is None and ready:
        info["next"] = ready[0]["id"]
    first = _storage() / ready[0]["id"] if ready else None
    thumb = thumbnail(first) if first is not None and first.is_dir() else None
    info["thumb"] = str(thumb) if thumb else None
    return info
