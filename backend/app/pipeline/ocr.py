"""Stage: OCR — read hardcoded (burned-in) Chinese subtitles off the video into
job.cn_srt. For videos that already carry on-screen subtitles, this is usually
more accurate than ASR (and works when there's little/no speech).

Approach (Windows-friendly, no PaddlePaddle): sample frames with ffmpeg cropped
to the bottom third (where subs live, also drops watermarks), OCR each crop with
RapidOCR (ONNX PP-OCR models, CPU), then post-process into clean timed cues.

Post-processing (this is what keeps cn.srt readable):
- per OCR line, drop low-confidence reads and lines with no Chinese that aren't
  plain numbers/prices (kills Latin gibberish OCR'd off transitions/backgrounds);
- group *consecutive* frames that show the same subtitle by text similarity, not
  exact match (so a slightly-misread or garbage-suffixed frame doesn't split one
  subtitle into many fragments);
- emit the cleanest variant in each group (the read with the most Chinese chars).
Remaining errors get fixed at the human edit gate.
"""
from __future__ import annotations

import re
import subprocess
from difflib import SequenceMatcher

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

_CJK = re.compile(r"[一-鿿]")
# pure number/price/punct lines are legit subtitle content (e.g. "800 1+1", "2900元")
_NUMERIC = re.compile(r"^[\d\s+\-.,:;%×xX*元¥$/()~]+$")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _cjk_count(s: str) -> int:
    return len(_CJK.findall(s or ""))


def _clean_frame(res, min_conf: float) -> str:
    """Join the OCR lines of one frame, dropping low-confidence reads and lines
    that are neither Chinese nor plain numbers (typical Latin OCR noise)."""
    kept: list[str] = []
    for item in res or []:
        text = (item[1] if len(item) > 1 else "").strip()
        try:
            score = float(item[2]) if len(item) > 2 else 1.0
        except (TypeError, ValueError):
            score = 1.0
        if not text or score < min_conf:
            continue
        if _CJK.search(text) or _NUMERIC.match(text):
            kept.append(text)
    return " ".join(kept).strip()


def _similar(a: str, b: str, sim: float) -> bool:
    """Same subtitle still on screen? True if one read contains the other or the
    normalized texts are similar enough (catches misreads/garbage suffixes)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if len(min(na, nb, key=len)) >= 3 and (na in nb or nb in na):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= sim


def _best(texts: list[str]) -> str:
    """Cleanest read of a subtitle group: most Chinese chars, then longest."""
    return max(texts, key=lambda s: (_cjk_count(s), len(s))) if texts else ""


def extract_hardsubs(job: Job, *, fps: float = 2.0, crop: str = "bottom-third",
                     min_dur: float = 0.4, min_conf: float = 0.5, sim: float = 0.5) -> Job:
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
    # 1) OCR + clean each frame to a single text line
    per_frame = [(k / fps, _clean_frame(ocr(str(fp))[0], min_conf)) for k, fp in enumerate(frames)]

    # 2) group consecutive frames showing the same subtitle, keep the cleanest read
    cues: list[Cue] = []
    group: list[str] = []
    start = 0.0
    rep = ""

    def flush(end_t: float) -> None:
        if group and rep:
            cues.append(Cue(index=len(cues) + 1, start=start, end=end_t, text=rep))

    for t, text in per_frame:
        if text and (not rep or _similar(text, rep, sim)):
            if not group:
                start = t
            group.append(text)
            rep = _best(group)
        else:  # empty frame or a different subtitle → close the current group
            flush(t)
            group, rep = ([text], text) if text else ([], "")
            start = t if text else start

    flush(len(frames) / fps if frames else 0.0)

    cues = [c for c in cues if c.duration >= min_dur]
    for i, c in enumerate(cues, 1):
        c.index = i
    write_srt(cues, job.cn_srt)
    return job
