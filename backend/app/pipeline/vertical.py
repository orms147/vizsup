"""Bản dọc 9:16 of a finished horizontal video (owner 2026-11-01: "cùng lúc build cả ngang và
dọc"): the picture centred on a blurred, darkened copy of itself — the same bars as the
render page's "Dọc 9:16" frame — with the subtitles, watermark and dub already in it (the
dialogue stays on the picture, over the covered Chinese band, as the owner wants it). One
encode, no TTS; the audio, chapters and title are copied. The render ("Dựng thêm bản dọc
9:16", ``o["also_vertical"]``) writes ``output.vertical.mp4`` next to output.mp4; a joined
video (``vizsup compile --vertical``) gets one too.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app import fsutil

VERTICAL = "output.vertical.mp4"
SIZE = (1080, 1920)


def wanted(o: dict) -> bool:
    """A render with these opts also makes the 9:16 copy: asked for, a real render (not the
    quick proof) of the source frame (a "Dọc 9:16" render is vertical already). Pure."""
    return bool(o.get("also_vertical")) and not o.get("preview") \
        and (o.get("frame") or "source") == "source"


def filter_graph(width: int = SIZE[0], height: int = SIZE[1]) -> str:
    """Source picture → 9:16 frame: blurred bars (quarter size, like assemble's), the
    picture fitted to the width in the middle. Pure."""
    qw, qh = width // 4 // 2 * 2, height // 4 // 2 * 2
    return (f"[0:v]split[fg][bg];"
            f"[bg]scale={qw}:{qh}:force_original_aspect_ratio=increase,crop={qw}:{qh},"
            f"boxblur=16:3,eq=brightness=-0.22:saturation=0.55,scale={width}:{height},setsar=1[bars];"
            f"[fg]scale={width}:-2:force_divisible_by=2,setsar=1[pic];"
            f"[bars][pic]overlay=(W-w)/2:(H-h)/2[v]")


def is_horizontal(path: Path) -> bool:
    """True when the video is wider than tall (a vertical source needs no second version)."""
    from app.pipeline.assemble import probe_size

    size = probe_size(path)
    return bool(size) and size[0] > size[1]


def make_vertical(src: Path, dest: Path | None = None, *, preset: str | None = None,
                  log: Callable[[str], None] | None = None,
                  progress: Callable[[float], None] | None = None,
                  cancel: Callable[[], bool] | None = None) -> Path:
    """Write the 9:16 version of ``src`` (default: ``output.vertical.mp4`` beside it) and
    return its path. Encoded into a temp name and swapped in when complete."""
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline import assemble

    src = Path(src)
    dest = Path(dest) if dest else src.with_name(VERTICAL)
    part = dest.with_name(dest.stem + assemble.RENDERING_SUFFIX)
    total = assemble.probe_duration(src)
    encoder = assemble._video_encoder()
    while True:
        cmd = [ffmpeg_bin(), "-y", "-i", str(src), "-filter_complex", filter_graph(),
               "-map", "[v]", "-map", "0:a?", *assemble._video_args(encoder, False, preset),
               "-c:a", "copy", "-map_metadata", "0", "-map_chapters", "0",
               "-movflags", "+faststart", str(part)]
        try:
            assemble._run_ffmpeg(cmd, dest.parent, total=total, progress=progress, cancel=cancel)
            break
        except RuntimeError:
            part.unlink(missing_ok=True)
            if encoder == "libx264":
                raise
            encoder = "libx264"
            if log:
                log("⚠ Mã hóa bằng GPU (NVENC) lỗi — dựng bản dọc bằng CPU.")
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    if part.exists():                    # (always, unless ffmpeg is faked in a test)
        fsutil.replace(part, dest)
    if log:
        log(f"✓ Bản dọc 9:16 (1080×1920): {dest.name}")
    return dest
