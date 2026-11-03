"""The exact still: one frame of the finished picture (same .ass, same graph as the render)
for a chosen second, no voice, no encode — so the owner sees where the text lands first."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.ffmpegutil import ffmpeg_bin
from app.models import Job
from app.pipeline import assemble, render
from app.pipeline.srt import write_srt
from app.models import Cue


@pytest.fixture()
def job(tmp_path):
    j = Job(id="st", work_dir=tmp_path / "job")
    j.work_dir.mkdir(exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x336699:s=640x360:d=6:r=25", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=6", "-shortest", "-pix_fmt", "yuv420p",
                    str(j.work_dir / "source.mp4")], check=True, capture_output=True)
    write_srt([Cue(index=1, start=0.5, end=2.0, text="Câu một"),
               Cue(index=2, start=3.0, end=5.0, text="Câu hai dài hơn một chút")], j.vi_srt)
    return j


def _white_share(path: Path) -> float:
    """Share of near-white pixels (the subtitle text) in the lower half."""
    from PySide6.QtGui import QImage

    img = QImage(str(path))
    assert not img.isNull()
    n = hit = 0
    for y in range(img.height() // 2, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            n += 1
            hit += c.red() > 235 and c.green() > 235 and c.blue() > 235
    return hit / n


def test_the_still_shows_the_line_of_that_second_and_nothing_else_is_touched(job):
    (job.work_dir / "fit_report.json").write_text("{}", encoding="utf-8")
    import shutil

    on = job.work_dir / "on.png"
    shutil.copy2(render.still_job(job, {}, 1.0), on)           # still.png is overwritten each time
    off = render.still_job(job, {}, 2.5)                       # between the two lines
    assert off.name == "still.png" and on.exists() and off.exists()
    assert _white_share(on) > 0 and _white_share(off) == 0         # text only at 1.0 s
    assert (job.work_dir / "fit_report.json").read_text(encoding="utf-8") == "{}"
    assert not (job.work_dir / "output.mp4").exists() and not (job.work_dir / "subs.ass").exists()


def test_the_still_follows_the_9_16_frame_watermark_and_cover(job):
    from PySide6.QtGui import QImage

    o = {"frame": "tiktok", "watermark": {"on": True, "text": "KÊNH", "pos": "tr",
                                          "opacity": 80, "size": 20},
         "logo_cover": {"on": True, "text": "TÊN PHIM", "box": {"x": 0.1, "y": 0.3, "w": 0.5, "h": 0.1},
                        "fill": "000000", "opacity": 100, "color": "FFFFFF"}}
    png = render.still_job(job, o, 1.0, height=640)
    img = QImage(str(png))
    assert img.height() == 640 and img.width() == 360          # the 9:16 canvas, scaled
