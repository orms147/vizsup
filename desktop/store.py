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
_UI_KEYS = ("subtitle_source", "asr", "translator", "refine", "tts")


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
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    """Recent resumable projects (have vi.srt or output.mp4), newest first."""
    base = _storage()
    out: list[dict] = []
    if not base.exists():
        return out
    for d in base.iterdir():
        if not d.is_dir():
            continue
        has_vi = (d / "vi.srt").exists()
        has_output = (d / "output.mp4").exists()
        if not (has_vi or has_output):
            continue
        info = {"id": d.name, "title": d.name, "opts": {}, "has_vi": has_vi,
                "has_output": has_output, "mtime": (d / "vi.srt").stat().st_mtime if has_vi
                else (d / "output.mp4").stat().st_mtime}
        jf = d / "job.json"
        if jf.exists():
            try:
                saved = json.loads(jf.read_text(encoding="utf-8"))
                info["title"] = saved.get("title") or d.name
                info["opts"] = saved.get("opts", {})
            except Exception:  # noqa: BLE001
                pass
        out.append(info)
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


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
