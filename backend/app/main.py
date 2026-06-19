"""FastAPI app: provider listing + the full job lifecycle (download → subtitle →
translate → EDIT GATE → render), with live progress over WebSocket.

Run: uvicorn app.main:app --reload   (from backend/)

Note: automatic Chinese subtitles need ASR (P2) or OCR (P5), which are stubs.
Until then, pass a Chinese ``srt`` path when creating a job, or upload one via the
subtitles endpoint. The translate/TTS/assemble path is fully wired.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.jobs import Stage, manager
from app.pipeline.srt import cues_to_dicts, dicts_to_cues, parse_srt, write_srt
from app.providers.registry import list_providers

app = FastAPI(title="vizsup", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# keep references to background tasks so they aren't garbage-collected
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/providers")
def providers() -> dict:
    return list_providers()


# --- jobs ---------------------------------------------------------------------
class CreateJob(BaseModel):
    url: str | None = None
    file: str | None = None        # local video path (alternative to url)
    srt: str | None = None         # provided Chinese .srt (skips ASR/OCR)
    translator: str = "passthrough"
    refine: str | None = None
    asr: str = "whisperx_funasr"


@app.post("/api/jobs")
async def create_job(body: CreateJob) -> dict:
    if not body.url and not body.file:
        raise HTTPException(400, "provide a url or a file")
    job_id = uuid.uuid4().hex[:12]
    manager.create(job_id, url=body.url, options=body.model_dump(exclude_none=True))
    _spawn(manager.run_until_gate(job_id))
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    state = manager.get_or_load(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    return state.snapshot()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if not manager.get(job_id):
        raise HTTPException(404, "job not found")
    manager.cancel(job_id)
    return {"ok": True}


# --- subtitles (the edit gate) ------------------------------------------------
@app.get("/api/jobs/{job_id}/subtitles")
def get_subtitles(job_id: str) -> dict:
    state = manager.get_or_load(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    cn = cues_to_dicts(parse_srt(state.job.cn_srt)) if state.job.cn_srt.exists() else []
    vi = cues_to_dicts(parse_srt(state.job.vi_srt)) if state.job.vi_srt.exists() else []
    return {"cn": cn, "vi": vi}


class SaveSubtitles(BaseModel):
    vi: list[dict]


@app.put("/api/jobs/{job_id}/subtitles")
def save_subtitles(job_id: str, body: SaveSubtitles) -> dict:
    state = manager.get_or_load(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    write_srt(dicts_to_cues(body.vi), state.job.vi_srt)
    return {"ok": True, "count": len(body.vi)}


class RenderOptions(BaseModel):
    tts: str = "edge"
    voice: str | None = None
    keep_audio: bool = False
    cover_hardsubs: bool = False
    font: str = "Be Vietnam Pro"


@app.post("/api/jobs/{job_id}/render")
async def render_job(job_id: str, body: RenderOptions) -> dict:
    state = manager.get_or_load(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    if not state.job.vi_srt.exists():
        raise HTTPException(409, "no vi.srt yet; reach the edit gate first")
    state.options.update(body.model_dump())
    state.cancelled = False
    _spawn(manager.render(job_id))
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download/{name}")
def download_artifact(job_id: str, name: str):
    state = manager.get_or_load(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    allowed = {"output.mp4": state.job.output_video, "vi.srt": state.job.vi_srt, "subs.ass": state.job.subs_ass}
    path = allowed.get(name)
    if not path or not path.exists():
        raise HTTPException(404, "artifact not found")
    return FileResponse(path)


# --- live progress ------------------------------------------------------------
@app.websocket("/ws/jobs/{job_id}")
async def job_ws(ws: WebSocket, job_id: str) -> None:
    await ws.accept()
    state = manager.get_or_load(job_id)
    if not state:
        await ws.send_json({"error": "job not found"})
        await ws.close()
        return
    q = manager.subscribe(job_id)
    try:
        await ws.send_json(state.snapshot())  # initial snapshot
        if q is None:
            return
        while True:
            event = await q.get()
            await ws.send_json(event)
            if event.get("stage") in (Stage.DONE.value, Stage.ERROR.value, Stage.AWAIT_EDIT.value):
                # keep the socket open a beat so the client receives terminal/gate states
                continue
    except WebSocketDisconnect:
        pass
    finally:
        if q is not None:
            manager.unsubscribe(job_id, q)
