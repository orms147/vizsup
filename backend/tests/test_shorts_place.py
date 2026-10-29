"""Shorts, S1 + S2: subtitles above the platform's bottom UI strip and clear of its button column —
on a 16:9 picture in the 9:16 frame (S1) and on a native vertical source (S2). The default margin
(60/1080) sits UNDER TikTok's caption strip on a vertical picture: the real render's text bottom must
land above the strip (checked on pixels of a synthetic 1080×1920 clip)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import Job  # noqa: E402
from app.pipeline import assemble, safezones, shorts  # noqa: E402

TT = safezones.SAFE_ZONES["tiktok"]


# --- the pure rule -----------------------------------------------------------------------------

def test_vertical_source_is_lifted_above_the_caption_strip_and_off_the_buttons():
    st, size = shorts.place({"margin_v": 60, "margin_l": 40, "margin_r": 40}, 60, "tiktok",
                            (1080, 1920), "source", "source")
    assert st["margin_v"] == round((TT.bottom + shorts.GAP) * 1080) == 259
    assert st["margin_l"] == st["margin_r"] == round((TT.right + shorts.GAP) * 1920) == 307
    assert size == 60                                       # a vertical picture: text size kept
    # the text's bottom edge (frame fraction) is above the strip's top edge
    assert 1 - st["margin_v"] / 1080 < 1 - TT.bottom


def test_a_landscape_picture_in_the_9_16_frame_keeps_its_margin_but_gets_bigger_text():
    st, size = shorts.place({"margin_v": 60}, 60, "tiktok", (1920, 1080), "tiktok", "source")
    assert st["margin_v"] == 60                             # the picture's bottom is above the strip
    assert size == 78 and st["margin_l"] >= 307             # 1.3× and clear of the button column
    # a 4:3 picture reaches lower: lifted just enough
    st43, _ = shorts.place({"margin_v": 60}, 60, "tiktok", (1440, 1080), "tiktok", "source")
    assert 60 <= st43["margin_v"] < 259


def test_nothing_changes_for_a_landscape_render_or_without_a_vertical_platform():
    style = {"margin_v": 60}
    assert shorts.place(style, 60, "tiktok", (1920, 1080), "source", "source") == (style, 60)
    assert shorts.place(style, 60, None, (1080, 1920), "source", "source") == (style, 60)
    assert shorts.place(style, 60, "youtube", (1080, 1920), "source", "source") == (style, 60)
    assert shorts.place(style, 60, "nope", (1080, 1920), "source", "source") == (style, 60)


def test_a_larger_user_margin_is_kept_and_other_platforms_use_their_own_strip():
    st, _ = shorts.place({"margin_v": 400, "margin_l": 500, "margin_r": 500}, 60, "tiktok",
                         (1080, 1920), "source", "source")
    assert (st["margin_v"], st["margin_l"], st["margin_r"]) == (400, 500, 500)
    reels, _ = shorts.place({}, 60, "reels", (1080, 1920), "source", "source")
    assert reels["margin_v"] == round((safezones.SAFE_ZONES["reels"].bottom + shorts.GAP) * 1080)
    # unknown source size in the 9:16 frame: assume the picture fills it
    unk, _ = shorts.place({}, 60, "tiktok", None, "tiktok", "source")
    assert unk["margin_v"] == 259


def test_size_is_capped():
    _, size = shorts.place({}, 100, "tiktok", (1920, 1080), "tiktok", "source")
    assert size == shorts.SIZE_MAX


def test_the_limits_table_covers_every_vertical_platform():
    assert set(shorts.LIMITS_S) == {"tiktok", "reels", "shorts"}
    assert shorts.SHORT_MAX_S <= min(shorts.LIMITS_S.values())


# --- assemble hands the placed style to the .ass -----------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _clip(path: Path, w: int, h: int, color: str = "black", secs: float = 2.0) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s={w}x{h}:r=10:d={secs}", "-f", "lavfi", "-i",
                    f"sine=f=220:r=48000:d={secs}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def _job(tmp_path: Path, w: int, h: int) -> Job:
    (tmp_path / "metadata.json").write_text(json.dumps({"title": "Tập 3"}), encoding="utf-8")
    (tmp_path / "vi.srt").write_text("1\n00:00:00,200 --> 00:00:01,800\nXin chào các bạn\n",
                                     encoding="utf-8")
    job = Job(id="s2", work_dir=tmp_path)
    _clip(job.source_video, w, h)
    return job


@needs_ffmpeg
def test_assemble_passes_the_lifted_style_to_the_ass(tmp_path, monkeypatch):
    seen = {}

    def fake_write_ass(cues, path, **kw):
        seen.update(style=kw.get("style"), size=kw.get("size"))
        raise RuntimeError("stop after the subtitles")

    monkeypatch.setattr(assemble, "write_ass", fake_write_ass)
    job = _job(tmp_path, 1080, 1920)
    with pytest.raises(RuntimeError):
        assemble.assemble(job, [], safe_caption="tiktok", size=60, style={"margin_v": 60})
    assert seen["style"]["margin_v"] == 259 and seen["style"]["margin_l"] == 307
    seen.clear()
    with pytest.raises(RuntimeError):
        assemble.assemble(job, [], size=60, style={"margin_v": 60})          # no Short: untouched
    assert seen["style"] == {"margin_v": 60} and seen["size"] == 60


def _white_rows(png: Path) -> tuple[int, int] | None:
    from PySide6.QtGui import QImage

    img = QImage(str(png))
    rows = [y for y in range(img.height())
            if any(img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() > 200
                   for x in range(0, img.width(), 3))]
    return (rows[0], rows[-1]) if rows else None


@needs_ffmpeg
@pytest.mark.parametrize("safe", [False, True])
def test_a_native_vertical_render_puts_the_text_above_the_strip(tmp_path, safe):
    """S2 on pixels: black 1080×1920 source, white subtitle; the lowest white row is above
    TikTok's bottom strip (78 % of 1920 = 1498) only with the placement."""
    job = _job(tmp_path, 1080, 1920)
    assemble.assemble(job, [], audio_mode="replace", size=60, style={"margin_v": 60},
                      safe_caption="tiktok" if safe else None, res="source",
                      overlays=None, preset="light")
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.0", "-i", str(job.output_video),
                    "-frames:v", "1", str(frame)], check=True)
    rows = _white_rows(frame)
    assert rows is not None, "no subtitle drawn"
    strip_top = round(1920 * (1 - TT.bottom))
    if safe:
        assert rows[1] < strip_top, rows            # the text ends above the caption strip
    else:
        assert rows[1] > strip_top, rows            # today's default sits under it
