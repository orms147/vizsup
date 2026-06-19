"""Stage: OCR — read hardcoded (burned-in) Chinese subtitles off the video into
job.cn_srt. For videos that already carry on-screen subtitles, this is usually
more accurate than ASR (and works when there's little/no speech).

Approach (Windows-friendly, no PaddlePaddle): sample frames with ffmpeg cropped
to the bottom third (where subs live, also drops watermarks), OCR each crop with
RapidOCR (ONNX PP-OCR models, CPU), then merge consecutive identical lines into
timed cues. Errors get fixed at the human edit gate.
"""
from __future__ import annotations

import re
import subprocess

from app.ffmpegutil import ffmpeg_bin
from app.models import Cue, Job
from app.pipeline.srt import write_srt
from app.providers.base import _installed

# bottom third, full width: crop=w:h:x:y
_CROP = {
    "bottom-third": "crop=iw:ih/3:0:ih*2/3",
    "bottom-quarter": "crop=iw:ih/4:0:ih*3/4",
    "bottom-half": "crop=iw:ih/2:0:ih/2",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def extract_hardsubs(job: Job, *, fps: float = 2.0, crop: str = "bottom-third",
                     min_dur: float = 0.4) -> Job:
    """OCR burned-in subtitles → job.cn_srt."""
    if not _installed("rapidocr_onnxruntime"):
        raise RuntimeError('OCR chưa được cài. Chạy: pip install -e ".[ocr]"')
    from rapidocr_onnxruntime import RapidOCR

    frames_dir = job.work_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    for old in frames_dir.glob("*.png"):
        old.unlink()

    vf = f"fps={fps}," + _CROP.get(crop, _CROP["bottom-third"])
    subprocess.run(
        [ffmpeg_bin(), "-y", "-i", str(job.source_video), "-vf", vf, str(frames_dir / "%05d.png")],
        check=True, capture_output=True,
    )

    ocr = RapidOCR()
    frames = sorted(frames_dir.glob("*.png"))
    cues: list[Cue] = []
    cur_text: str | None = None
    cur_start = 0.0

    for k, fp in enumerate(frames):
        t = k / fps
        res, _ = ocr(str(fp))
        text = " ".join(line[1] for line in res).strip() if res else ""
        if _norm(text) == _norm(cur_text or ""):
            continue  # unchanged (same subtitle still on screen, or both empty)
        if cur_text:  # subtitle changed → close the current cue at this frame
            cues.append(Cue(index=len(cues) + 1, start=cur_start, end=t, text=cur_text))
        cur_text = text or None
        cur_start = t

    if cur_text:
        cues.append(Cue(index=len(cues) + 1, start=cur_start, end=len(frames) / fps, text=cur_text))

    cues = [c for c in cues if c.duration >= min_dur]
    for i, c in enumerate(cues, 1):
        c.index = i
    write_srt(cues, job.cn_srt)
    return job
