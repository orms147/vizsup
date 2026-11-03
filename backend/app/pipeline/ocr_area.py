""""Khoanh vùng chữ Trung": the rectangle OCR reads, chosen by the owner on a frame after the
download (owner, 2026-11-01). Reading the whole bottom third also read a game panel of the
anime ("修复功量 升点 司机权…" merged into a subtitle in 《末日校车》03) — a tight band
around the subtitles reads the subtitles only.

The rectangle is normalized to the FULL frame ``{x, y, w, h}`` (0..1) and kept
- in the project (``ocr_area.json``) — the OCR crop, and the band the render covers
  (``hardsub_region.json`` is written from it), and
- in the series profile (``Profile.ocr_area``): every later episode — the watcher's, the
  Telegram bot's — reads the same band without asking again.

Pure helpers + small files; the desktop shows the picker (``desktop/widgets/area_dialog.py``).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.fsutil import atomic_write_text

FILE = "ocr_area.json"
LOGOS_FILE = "ocr_logos.json"
MAX_LOGOS = 8
MIN_SIDE = 0.03          # a rectangle thinner than this (÷ frame) is a mis-click, not a band


def clean(rect) -> dict | None:
    """``{x, y, w, h}`` clamped to the frame, or None when it is not a usable band. Pure."""
    try:
        x, y, w, h = (float(rect[k]) for k in ("x", "y", "w", "h"))
    except (TypeError, KeyError, ValueError):
        return None
    x, y = min(max(0.0, x), 1.0), min(max(0.0, y), 1.0)
    w, h = min(max(0.0, w), 1.0 - x), min(max(0.0, h), 1.0 - y)
    if w < MIN_SIDE or h < MIN_SIDE:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def crop_filter(rect: dict) -> str:
    """The ffmpeg crop for ``rect`` (even sizes). Pure."""
    return (f"crop=trunc(iw*{rect['w']:.4f}/2)*2:trunc(ih*{rect['h']:.4f}/2)*2:"
            f"trunc(iw*{rect['x']:.4f}):trunc(ih*{rect['y']:.4f})")


def clean_logos(rects) -> list[dict]:
    """The usable rectangles of ``rects`` (at most ``MAX_LOGOS``). Pure."""
    out = []
    for r in rects or []:
        c = clean(r)
        if c is not None:
            out.append(c)
    return out[:MAX_LOGOS]


def load_logos(work_dir: Path) -> list[dict] | None:
    """The project's own logo regions; None when it never had any (an empty list = "none")."""
    try:
        data = json.loads((Path(work_dir) / LOGOS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return clean_logos(data) if isinstance(data, list) else None


def logos_for_job(job) -> list[dict]:
    """The logo regions OCR must not read: the project's, else its series'. A logo (creator
    name, platform mark) sits in the same corner of every episode."""
    mine = load_logos(job.work_dir)
    if mine is not None:
        return mine
    from app.pipeline import series

    p = series.profile_of(job)
    return clean_logos(p.ocr_logos) if p is not None else []


def remember_logos(job, rects, *, series_too: bool = True) -> list[dict]:
    """Keep the logo regions (possibly none) for this project and, when asked, its series."""
    logos = clean_logos(rects)
    atomic_write_text(job.work_dir / LOGOS_FILE, json.dumps(logos, indent=1))
    if series_too:
        from app.pipeline import series

        p = series.profile_of(job)
        if p is not None:
            p.ocr_logos = logos
            series.save(p)
    return logos


def blank_logos(img, logos: list[dict], box: tuple[float, float, float, float]):
    """``img`` (a BGR strip cut out of the frame at ``box`` = x, y, w, h normalized to the full
    frame) with every logo region painted black — the OCR then has nothing to read there. Works
    on a copy only when something is painted. Pure."""
    if not logos:
        return img
    bx, by, bw, bh = box
    h, w = img.shape[:2]
    out = None
    for lg in logos:
        x0 = max(lg["x"], bx)
        y0 = max(lg["y"], by)
        x1 = min(lg["x"] + lg["w"], bx + bw)
        y1 = min(lg["y"] + lg["h"], by + bh)
        if x1 <= x0 or y1 <= y0:
            continue
        if out is None:
            out = img.copy()
        out[int((y0 - by) / bh * h):int(round((y1 - by) / bh * h)),
            int((x0 - bx) / bw * w):int(round((x1 - bx) / bw * w))] = 0
    return img if out is None else out


def load(work_dir: Path) -> dict | None:
    try:
        return clean(json.loads((Path(work_dir) / FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def for_job(job) -> dict | None:
    """The project's own band, else its series' (a later episode inherits it)."""
    mine = load(job.work_dir)
    if mine:
        return mine
    from app.pipeline import series

    p = series.profile_of(job)
    return clean(p.ocr_area) if p is not None and p.ocr_area else None


def remember(job, rect: dict, *, series_too: bool = True) -> dict | None:
    """Keep ``rect`` for this project (and its series). The render's cover band follows it.
    Returns the cleaned rectangle, or None when it was unusable (nothing written)."""
    r = clean(rect)
    if r is None:
        return None
    atomic_write_text(job.work_dir / FILE, json.dumps(r, indent=1))
    atomic_write_text(job.work_dir / "hardsub_region.json", json.dumps(r, indent=1))
    if series_too:
        from app.pipeline import series

        p = series.profile_of(job)
        if p is not None:
            p.ocr_area = r
            series.save(p)
    return r


def sample_frames(job, n: int = 6) -> list[Path]:
    """``n`` full frames (JPEG, ≤ 1280 wide) spread over the middle of the video, for the
    picker. Blocking — call off the GUI thread. Missing ones are skipped."""
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline.assemble import probe_duration
    from app.pipeline.hardsub_detect import _sample_times

    out_dir = job.work_dir / "detect"
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = probe_duration(job.source_video) or 0.0
    out = []
    for i, t in enumerate(_sample_times(dur, n)):
        p = out_dir / f"area_{i:02d}.jpg"
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{t:.2f}", "-i",
                            str(job.source_video), "-frames:v", "1", "-vf",
                            "scale='min(1280,iw)':-2", "-q:v", "3", str(p)],
                           capture_output=True, timeout=60)
        if r.returncode == 0 and p.exists():
            out.append(p)
    return out


# what a re-read replaces: the Chinese lines and everything derived from them line by line
REREAD_FILES = ("cn.srt", "vi.srt", "vi.mt.srt", "ocr_frames.json", "ocr_report.json",
                "ocr_dropped.json", "context_brief.json", "qa_report.json", "fit_report.json",
                "glossary.suggested.json", "overrides.json", "style_overrides.json",
                "timing_report.json", "zh_ref.json", FILE)


def set_aside(work_dir: Path) -> Path | None:
    """"Đọc lại phụ đề": move the subtitles and what was built line by line on them into
    ``backup/reread-<time>`` (nothing is deleted), so the next run reads, translates and stops
    at the gate again. Style, render settings, the source and the voice cache stay. Returns
    the backup folder, or None when there was nothing to move."""
    import shutil
    import time

    d = Path(work_dir)
    present = [d / n for n in REREAD_FILES if (d / n).exists()]
    if not present:
        return None
    dest = d / "backup" / time.strftime("reread-%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for f in present:
        shutil.move(str(f), str(dest / f.name))
    return dest
