"""Stage: ASR (no-hardsub path). Thin orchestrator over the chosen ASR provider.

Extracts audio from the video, runs the provider, and writes the Chinese cues to
``job.cn_srt``. Working providers: faster-whisper (CUDA→CPU fallback) and FunASR
Paraformer (CN-native). The fused word-timing provider is planned as N-7.
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


def transcribe(job: Job, *, provider: str | None = None, language: str = "zh",
               log=None) -> Job:
    if provider is None:
        from app.config import settings

        provider = settings.vizsup_default_asr
    asr = get_asr(provider)
    if not asr.available():
        raise RuntimeError(f"ASR provider '{provider}' is not available (missing dependency "
                           f"or API key).")
    if not job.source_audio.exists():
        if log:
            log("Tách âm thanh từ video (ffmpeg)…")
        _extract_audio(job)
    from app.heavy import heavy_task

    with heavy_task(f"ASR ({provider})"):
        try:
            cues = asr.transcribe(job.source_audio, language=language, log=log)
        finally:
            asr.unload()   # free VRAM before the next heavy stage (8 GB rule)
    write_srt(cues, job.cn_srt)
    return job
