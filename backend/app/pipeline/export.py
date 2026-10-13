"""E-16 export conveniences: file names from the project title, and a soft-subtitle
MKV (source picture untouched + the rendered audio + the styled .ass as a switchable
track, fonts attached) for re-editing elsewhere. Remux only — no re-encode.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin
from app.models import Job

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_name(title: str | None, fallback: str, *, limit: int = 80) -> str:
    """A Windows-safe file stem from a video title: hashtags and characters Windows
    forbids are dropped, whitespace collapsed, length capped. Falls back when empty."""
    t = re.sub(r"#\S+", " ", title or "")
    t = _BAD.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    t = t[:limit].rstrip(" .")
    if t.upper() in _RESERVED:
        t += "_"
    return t or fallback


def _fonts(job: Job) -> list[Path]:
    """The subtitle fonts copied next to the job by the render (assemble._ass_fonts);
    the mono UI font is not a subtitle font."""
    d = job.work_dir / "fonts"
    return sorted(f for f in d.glob("*.ttf") if "mono" not in f.name.lower()) if d.is_dir() else []


def softsub_cmd(job: Job, dest: Path) -> list[str]:
    """ffmpeg argv (cwd = job dir): video from source.mp4 (no burned subs), audio from
    the rendered output.mp4 (the dub mix), subs.ass as the default subtitle track."""
    cmd = [ffmpeg_bin(), "-y", "-i", "source.mp4", "-i", "output.mp4", "-i", "subs.ass",
           "-map", "0:v:0", "-map", "1:a:0?", "-map", "2:0",
           "-c:v", "copy", "-c:a", "copy", "-c:s", "ass",
           "-metadata:s:s:0", "language=vie", "-metadata:s:s:0", "title=Tiếng Việt",
           "-metadata:s:a:0", "language=vie", "-disposition:s:0", "default"]
    for i, f in enumerate(_fonts(job)):
        cmd += ["-attach", f"fonts/{f.name}", f"-metadata:s:t:{i}", "mimetype=font/ttf"]
    return cmd + [str(dest)]


def export_softsub(job: Job, dest: Path) -> Path:
    """Write ``dest`` (.mkv). Needs a finished render (output.mp4 + subs.ass)."""
    wd = job.work_dir
    for need in ("source.mp4", "output.mp4", "subs.ass"):
        if not (wd / need).exists():
            raise FileNotFoundError(f"Thiếu {need} — dựng video trước.")
    dest = Path(dest).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(softsub_cmd(job, dest), cwd=str(wd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-1:] or ["ffmpeg lỗi"]
        raise RuntimeError(tail[0])
    return dest
