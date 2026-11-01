"""Bản dọc 9:16 of a finished horizontal video (pipeline/vertical.py): one encode, the picture
on blurred bars; the render makes it when "Dựng thêm bản dọc 9:16" is on."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.ffmpegutil import ffmpeg_bin
from app.pipeline import assemble, vertical


def test_filter_graph_fits_the_width_on_blurred_bars():
    g = vertical.filter_graph()
    assert "[0:v]split[fg][bg]" in g and "boxblur=16:3" in g
    assert "scale=1080:1920,setsar=1[bars]" in g and "[fg]scale=1080:-2" in g
    assert g.endswith("overlay=(W-w)/2:(H-h)/2[v]")


def _clip(path: Path, w: int, h: int) -> Path:
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={w}x{h}:rate=10:duration=1", "-f", "lavfi", "-i",
                    "sine=duration=1", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)
    return path


def test_make_vertical_writes_1080x1920_with_the_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(assemble, "_video_encoder", lambda: "libx264")
    src = _clip(tmp_path / "output.mp4", 320, 180)
    logs: list[str] = []
    out = vertical.make_vertical(src, log=logs.append)
    assert out == tmp_path / vertical.VERTICAL and out.exists()
    assert assemble.probe_size(out) == (1080, 1920)
    assert not list(tmp_path.glob("*.rendering.mp4"))
    probe = subprocess.run([ffmpeg_bin(), "-i", str(out)], capture_output=True, text=True).stderr
    assert "Audio:" in probe
    assert vertical.is_horizontal(src) and not vertical.is_horizontal(out)
    assert any("Bản dọc 9:16" in m for m in logs)


@pytest.mark.parametrize("opts,made", [
    ({"also_vertical": True}, True),
    ({"also_vertical": True, "frame": "source"}, True),
    ({"also_vertical": True, "preview": True}, False),       # the quick proof: never
    ({"also_vertical": True, "frame": "tiktok"}, False),     # already vertical
    ({}, False),
])
def test_a_render_makes_it_only_when_asked(opts, made):
    assert vertical.wanted(opts) is made
