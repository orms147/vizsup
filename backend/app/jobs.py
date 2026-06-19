"""In-process async job worker.

A job runs the pipeline stages to the human-edit gate, parks, then (after the
user approves the edited subtitles) renders. Sync stage functions run in a
thread executor; progress is published to WebSocket subscribers; a cancel flag
is checked between stages. State per job lives in its work_dir, so subtitle/
render/download endpoints work even after a server restart (re-loaded from disk).
"""
from __future__ import annotations

import asyncio
import functools
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.config import settings
from app.models import Job
from app.pipeline import asr, assemble, download, translate, tts


class Stage(str, Enum):
    QUEUED = "queued"
    DOWNLOAD = "download"
    SUBTITLE = "subtitle"        # OCR or ASR (or provided SRT)
    TRANSLATE = "translate"
    AWAIT_EDIT = "await_edit"    # the human gate — pipeline parks here
    TTS = "tts"
    ASSEMBLE = "assemble"
    DONE = "done"
    ERROR = "error"


@dataclass
class JobState:
    job: Job
    stage: Stage = Stage.QUEUED
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    cancelled: bool = False
    options: dict = field(default_factory=dict)
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "id": self.job.id,
            "stage": self.stage.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
        }


class CancelledError(RuntimeError):
    pass


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}

    # lifecycle -----------------------------------------------------------------
    def create(self, job_id: str, url: str | None = None, options: dict | None = None) -> JobState:
        work_dir = settings.vizsup_storage_dir / job_id
        state = JobState(job=Job(id=job_id, work_dir=work_dir, url=url), options=options or {})
        self._jobs[job_id] = state
        return state

    def get(self, job_id: str) -> JobState | None:
        return self._jobs.get(job_id)

    def get_or_load(self, job_id: str) -> JobState | None:
        """Return the live state, or reconstruct a minimal one from disk."""
        if state := self._jobs.get(job_id):
            return state
        work_dir = settings.vizsup_storage_dir / job_id
        if not work_dir.exists():
            return None
        state = JobState(job=Job(id=job_id, work_dir=work_dir))
        meta = work_dir / "metadata.json"
        if meta.exists():
            state.job.metadata = json.loads(meta.read_text(encoding="utf-8"))
        if state.job.output_video.exists():
            state.stage = Stage.DONE
        elif state.job.vi_srt.exists():
            state.stage = Stage.AWAIT_EDIT
        self._jobs[job_id] = state
        return state

    def cancel(self, job_id: str) -> None:
        if state := self._jobs.get(job_id):
            state.cancelled = True

    # pub/sub -------------------------------------------------------------------
    def subscribe(self, job_id: str) -> asyncio.Queue | None:
        if state := self._jobs.get(job_id):
            q: asyncio.Queue = asyncio.Queue()
            state._subscribers.append(q)
            return q
        return None

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        if state := self._jobs.get(job_id):
            if q in state._subscribers:
                state._subscribers.remove(q)

    def _emit(self, state: JobState, *, stage: Stage | None = None, progress: float | None = None,
              message: str | None = None, error: str | None = None) -> None:
        if stage is not None:
            state.stage = stage
        if progress is not None:
            state.progress = progress
        if message is not None:
            state.message = message
        if error is not None:
            state.error = error
        for q in list(state._subscribers):
            q.put_nowait(state.snapshot())

    def _save_meta(self, state: JobState) -> None:
        (state.job.work_dir / "metadata.json").write_text(
            json.dumps(state.job.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _check_cancel(self, state: JobState) -> None:
        if state.cancelled:
            raise CancelledError("cancelled by user")

    # runners -------------------------------------------------------------------
    async def run_until_gate(self, job_id: str) -> None:
        state = self._jobs[job_id]
        loop = asyncio.get_running_loop()
        opts = state.options
        job = state.job
        try:
            # 1) acquire the source video
            self._check_cancel(state)
            if opts.get("file"):
                import shutil

                self._emit(state, stage=Stage.DOWNLOAD, progress=0.1, message="copying local file")
                await loop.run_in_executor(None, shutil.copy, opts["file"], str(job.source_video))
                job.metadata.setdefault("platform", "local")
            else:
                self._emit(state, stage=Stage.DOWNLOAD, progress=0.1, message="downloading")
                await loop.run_in_executor(None, download.download, job)
            self._save_meta(state)

            # 2) Chinese subtitles: provided SRT, else ASR (OCR path is P5)
            self._check_cancel(state)
            self._emit(state, stage=Stage.SUBTITLE, progress=0.4, message="preparing Chinese subtitles")
            if opts.get("srt"):
                from app.pipeline.srt import parse_srt, write_srt

                await loop.run_in_executor(None, lambda: write_srt(parse_srt(Path(opts["srt"])), job.cn_srt))
            else:
                fn = functools.partial(asr.transcribe, job, provider=opts.get("asr", settings.vizsup_default_asr))
                await loop.run_in_executor(None, fn)

            # 3) translate CN -> VI
            self._check_cancel(state)
            self._emit(state, stage=Stage.TRANSLATE, progress=0.7, message="translating CN->VI")
            fn = functools.partial(
                translate.translate,
                job,
                draft=opts.get("translator", "passthrough"),
                refine=opts.get("refine"),
            )
            await loop.run_in_executor(None, fn)

            # 4) park at the edit gate
            self._emit(state, stage=Stage.AWAIT_EDIT, progress=1.0,
                       message="review/edit subtitles, then render")
        except CancelledError:
            self._emit(state, stage=Stage.ERROR, message="cancelled", error="cancelled")
        except Exception as exc:  # noqa: BLE001 - surface any stage failure to the client
            self._emit(state, stage=Stage.ERROR, message="failed", error=f"{type(exc).__name__}: {exc}")

    async def render(self, job_id: str) -> None:
        state = self._jobs[job_id]
        loop = asyncio.get_running_loop()
        opts = state.options
        job = state.job
        try:
            self._check_cancel(state)
            if not job.vi_srt.exists():
                raise FileNotFoundError("no vi.srt to render; run to the edit gate first")
            self._emit(state, stage=Stage.TTS, progress=0.2, message="synthesizing Vietnamese voice")
            fn = functools.partial(tts.synthesize, job, provider=opts.get("tts", settings.vizsup_default_tts),
                                   voice=opts.get("voice"))
            segments = await loop.run_in_executor(None, fn)

            self._check_cancel(state)
            self._emit(state, stage=Stage.ASSEMBLE, progress=0.6, message="assembling final video")
            fn = functools.partial(
                assemble.assemble,
                job,
                segments,
                replace_audio=not opts.get("keep_audio", False),
                cover_hardsubs=opts.get("cover_hardsubs", False),
                font=opts.get("font", "Be Vietnam Pro"),
            )
            await loop.run_in_executor(None, fn)
            self._emit(state, stage=Stage.DONE, progress=1.0, message="done")
        except CancelledError:
            self._emit(state, stage=Stage.ERROR, message="cancelled", error="cancelled")
        except Exception as exc:  # noqa: BLE001
            self._emit(state, stage=Stage.ERROR, message="failed", error=f"{type(exc).__name__}: {exc}")


manager = JobManager()
