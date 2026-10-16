"""E-16 export conveniences: file names from the project title, and a soft-subtitle
MKV (source picture without burned subs + the rendered audio + the styled .ass as a
switchable track, fonts attached) for re-editing elsewhere. Remux only — unless the
render blurred the Chinese hardsub band: then the picture is re-encoded with the same
blur, so the .mkv never shows what output.mp4 hid.
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
    the rendered output.mp4 (the dub mix), subs.ass as the default subtitle track. Re-
    encodes the picture only when the render changed it (blur cover, F-4 pauses)."""
    from app.pipeline import assemble

    cmd = [ffmpeg_bin(), "-y", "-i", "source.mp4", "-i", "output.mp4", "-i", "subs.ass"]
    info = assemble.load_render_info(job)
    ins = [tuple(x) for x in info.get("inserts") or []]
    parts: list[str] = []
    v = "[0:v]"
    if ins:          # F-4 pause notes: the picture freezes exactly like output.mp4's
        pre, v, _a = assemble._pause_graph(ins, float(info.get("duration") or 0), v, [])
        parts += pre
    if info.get("cover_mode") == "blur":
        crop_expr, overlay_pos = assemble._blur_band_filters(assemble._load_region(job))
        parts += [f"{v}split[vsrc][vband]", f"[vband]{crop_expr},boxblur=10:2[vblur]",
                  f"[vsrc][vblur]{overlay_pos}[vb]"]
        v = "[vb]"
    if parts:
        cmd += ["-filter_complex", ";".join(parts), "-map", v,
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast"]
    else:
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    cmd += ["-map", "1:a:0?", "-map", "2:0", "-c:a", "copy", "-c:s", "ass",
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
