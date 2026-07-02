"""Stage: hardsub detection (the branch point between the OCR and ASR paths).

Samples ~16 frames spread across the middle 80% of the video, crops each to the
subtitle region (bottom third by default — also drops Douyin watermarks), OCRs
the crops with the same RapidOCR engine the OCR stage uses, and calls the video
"hardsubbed" when enough sampled frames show real Chinese text.

Full recognition (not detection-only) is deliberate: a detection-only box could
be scene text or a logo, while recognized CJK characters in the subtitle strip
are near-certain hardsubs. ~16 small crops keep this a few seconds even on CPU.

The pure decision logic lives in `_decide`/`_sample_times` so it's headless-testable;
`detect_hardsubs` only adds the ffmpeg/OCR I/O around it.
"""
from __future__ import annotations

import subprocess

from app.ffmpegutil import ffmpeg_bin
from app.models import Job

# A frame counts as "subtitled" only with ≥2 recognized CJK chars (one lone char
# is often a misread of background texture/scene text).
_MIN_CJK = 2


def _sample_times(duration: float, n: int) -> list[float]:
    """N timestamps evenly spread over the middle 80% of the video (skips intro/
    outro cards). Unknown duration (probe failed → 0) falls back to 1s steps;
    ffmpeg simply yields no frame past EOF and those samples are skipped."""
    if n <= 0:
        return []
    if duration <= 0:
        return [float(i + 1) for i in range(n)]
    lo, hi = 0.1 * duration, 0.9 * duration
    if n == 1:
        return [(lo + hi) / 2]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def _decide(flags: list[bool], *, min_hits: int = 3, min_ratio: float = 0.25) -> bool:
    """Hardsubs iff enough sampled frames showed Chinese text: an absolute floor
    (min_hits — so 2 lucky misreads can't flip a short sample) AND a ratio floor
    (min_ratio — sparse scene text in a long sample stays ASR)."""
    if not flags:
        return False
    hits = sum(bool(f) for f in flags)
    return hits >= min_hits and hits / len(flags) >= min_ratio


def detect_hardsubs(job: Job, *, samples: int = 16, crop: str = "bottom-third",
                    min_conf: float = 0.5) -> bool:
    """Decide OCR vs ASR for ``job``. Returns True if burned-in Chinese subtitles
    were detected (and records it on ``job.has_hardsubs``).

    Raises RuntimeError when no OCR engine is installed — callers offering an
    "auto" mode should catch that and fall back to ASR.
    """
    from app.pipeline.assemble import probe_duration
    from app.pipeline.ocr import _CROP, _build_ocr

    ocr = _build_ocr()

    try:
        duration = probe_duration(job.source_video)
    except Exception:  # noqa: BLE001 - probe failure → fixed-step sampling
        duration = 0.0

    det_dir = job.work_dir / "detect"
    det_dir.mkdir(exist_ok=True)
    vf = _CROP.get(crop, _CROP["bottom-third"])

    from app.heavy import heavy_task

    flags: list[bool] = []
    with heavy_task("dò hardsub"):
        _detect_loop(job, ocr, duration, samples, vf, det_dir, min_conf, flags)
    job.has_hardsubs = _decide(flags)
    return job.has_hardsubs


def _detect_loop(job, ocr, duration, samples, vf, det_dir, min_conf, flags) -> None:
    from app.pipeline.ocr import _cjk_count, _clean_frame, _to_lines

    for i, t in enumerate(_sample_times(duration, samples)):
        fp = det_dir / f"{i:03d}.png"
        fp.unlink(missing_ok=True)
        # input-seek (-ss before -i) → fast keyframe seek; exactness doesn't matter here
        subprocess.run(
            [ffmpeg_bin(), "-y", "-ss", f"{t:.2f}", "-i", str(job.source_video),
             "-frames:v", "1", "-vf", vf, str(fp)],
            check=False, capture_output=True,
        )
        if not fp.exists():  # past EOF / unreadable timestamp → not a sample
            continue
        text = _clean_frame(_to_lines(ocr(str(fp))), min_conf)
        flags.append(_cjk_count(text) >= _MIN_CJK)
