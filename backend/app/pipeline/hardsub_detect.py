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

import json
import struct
import subprocess

from app.ffmpegutil import ffmpeg_bin
from app.models import Job

# A frame counts as "subtitled" only with ≥2 recognized CJK chars (one lone char
# is often a misread of background texture/scene text).
_MIN_CJK = 2

# Normalized (offset_y, height_fraction) of each crop within the FULL frame; the
# crops are full-width, so x-offset is 0 and width-fraction is 1. A crop-pixel (x,y)
# then maps to full-frame normalized (x/crop_w, off_y + y/crop_h * h_frac).
_CROP_FRAC = {
    "bottom-third": (2 / 3, 1 / 3),
    "bottom-quarter": (3 / 4, 1 / 4),
    "bottom-half": (1 / 2, 1 / 2),
    "middle-third": (1 / 3, 1 / 3),
    "top-third": (0.0, 1 / 3),
    "full": (0.0, 1.0),
}


def _poly_bbox(box) -> tuple[float, float, float, float] | None:
    """Axis-aligned pixel bbox (x0,y0,x1,y1) of a RapidOCR 4-point polygon
    (list-of-points or numpy array). None if the box isn't parseable."""
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _png_size(path) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR header — stdlib only, no image library."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return int(w), int(h)


def region_from_boxes(bboxes, crop_w: int, crop_h: int, *, crop: str = "bottom-third",
                      pad: float = 0.01) -> dict | None:
    """Union of recognized-text boxes → a normalized full-frame band {x,y,w,h}
    (each in [0,1]). ``bboxes`` are (x0,y0,x1,y1) in CROP-image pixels; (crop_w,
    crop_h) is the crop image size. Pure. Returns None when there are no boxes or
    the crop size is degenerate. A small ``pad`` widens the band so the blur fully
    covers glyph edges."""
    if not bboxes or crop_w <= 0 or crop_h <= 0:
        return None
    off_y, h_frac = _CROP_FRAC.get(crop, _CROP_FRAC["bottom-third"])
    xs0 = [b[0] / crop_w for b in bboxes]
    xs1 = [b[2] / crop_w for b in bboxes]
    ys0 = [off_y + (b[1] / crop_h) * h_frac for b in bboxes]
    ys1 = [off_y + (b[3] / crop_h) * h_frac for b in bboxes]
    x = max(0.0, min(xs0) - pad)
    y = max(0.0, min(ys0) - pad)
    right = min(1.0, max(xs1) + pad)
    bottom = min(1.0, max(ys1) + pad)
    if right <= x or bottom <= y:
        return None
    return {"x": round(x, 4), "y": round(y, 4),
            "w": round(right - x, 4), "h": round(bottom - y, 4)}


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
    boxes: list[tuple[float, float, float, float]] = []
    with heavy_task("dò hardsub"):
        _detect_loop(job, ocr, duration, samples, vf, det_dir, min_conf, flags, boxes)
    job.has_hardsubs = _decide(flags)

    # ENG-5: when hardsubs are present, persist the ACTUAL text band (union of the
    # recognized-text boxes) so the render's blur can cover just that band instead of
    # the whole bottom-third. Best-effort — a missing/failed region just falls back.
    region_path = job.work_dir / "hardsub_region.json"
    region_path.unlink(missing_ok=True)
    if job.has_hardsubs and boxes:
        png = next(iter(sorted(det_dir.glob("*.png"))), None)
        size = _png_size(png) if png else None
        if size:
            region = region_from_boxes(boxes, size[0], size[1], crop=crop)
            if region:
                region_path.write_text(json.dumps(region, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return job.has_hardsubs


def _detect_loop(job, ocr, duration, samples, vf, det_dir, min_conf, flags, boxes) -> None:
    from app.pipeline.ocr import _CJK, _cjk_count, _clean_frame_conf, _keep_line, _lines_with_boxes

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
        rows = _lines_with_boxes(ocr(str(fp)))
        text, _ = _clean_frame_conf([(t_, s_) for t_, s_, _ in rows], min_conf)
        has_cjk = _cjk_count(text) >= _MIN_CJK
        flags.append(has_cjk)
        if has_cjk:   # collect boxes of the real CJK reads for the band union
            for tx, sc, bx in rows:
                tx = (tx or "").strip()
                if sc >= min_conf and bx is not None and _CJK.search(tx) and _keep_line(tx):
                    bb = _poly_bbox(bx)
                    if bb:
                        boxes.append(bb)
