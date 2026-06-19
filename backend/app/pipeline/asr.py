"""Stage: ASR (no-hardsub path). Thin orchestrator over the chosen ASR provider.

Extracts audio from the video, runs the provider, and writes the Chinese cues to
``job.cn_srt``. The heavy lifting (WhisperX + FunASR) lives in the provider
(STUB until P2); this wiring is ready.
"""
from __future__ import annotations

import subprocess

from app.models import Job
from app.pipeline.srt import write_srt
from app.providers.registry import get_asr


def _extract_audio(job: Job) -> None:
    from app.ffmpegutil import ffmpeg_bin

    subprocess.run(
        [ffmpeg_bin(), "-y", "-i", str(job.source_video), "-vn", "-ac", "1", "-ar", "16000",
         str(job.source_audio)],
        check=True, capture_output=True,
    )


def transcribe(job: Job, *, provider: str = "whisperx_funasr", language: str = "zh") -> Job:
    asr = get_asr(provider)
    if not asr.available():
        raise RuntimeError(f"ASR provider '{provider}' is not available (missing dependency).")
    if not job.source_audio.exists():
        _extract_audio(job)
    cues = asr.transcribe(job.source_audio, language=language)
    write_srt(cues, job.cn_srt)
    return job
