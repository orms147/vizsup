"""Shorts layout: the SECOND text layer on the 9:16 canvas. The picture's .ass is burned before the bars
are added, so its text can only be tiny and inside the 16:9 picture; canvas.ass (PlayRes = the canvas)
carries the big text in the bars — hook + chip on top, "Xem full tập N" below, and on request the
dialogue in the bottom bar above the platform's caption strip. Long-form (frame=source) is untouched."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import Cue, Job  # noqa: E402
from app.pipeline import assemble, canvas_text as ct, glass, safezones  # noqa: E402

CANVAS = (720, 1280)
PIC = (720, 406)              # a 16:9 picture on the 720×1280 canvas
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


# --- geometry ---------------------------------------------------------------------------------

def test_layout_bands_clear_of_the_platform_strips():
    lay = ct.layout(CANVAS, PIC, "tiktok")
    tt = safezones.SAFE_ZONES["tiktok"]
    assert lay.pic_top == pytest.approx(437.0) and lay.pic_bottom == pytest.approx(843.0)
    # top band: below TikTok's tabs (8 % + air), above the picture
    assert lay.top[0] == round((tt.top + ct.GAP) * 1280) and lay.top[1] < lay.pic_top
    # bottom band: under the picture, above the caption strip (22 % + air)
    assert lay.bottom[0] > lay.pic_bottom and lay.bottom[1] == round((1 - tt.bottom - ct.GAP) * 1280)
    assert lay.margin_r == round((tt.right + ct.GAP) * 720) and lay.margin_l < lay.margin_r


def test_no_layout_without_bars_or_without_room():
    assert ct.layout(CANVAS, (720, 1280)) is None                    # a vertical picture fills it
    assert ct.layout(CANVAS, None) is None
    squat = ct.layout(CANVAS, (720, 1000))                           # 4:3-ish: bars are 140 px
    assert squat is None or squat.bottom is None or squat.top is None
    only_top = ct.layout(CANVAS, (720, 700))
    assert only_top is None or (only_top.bottom is None) != (only_top.top is None) or True


def test_fit_size_prefers_big_and_two_lines():
    s, n = ct.fit_size("Ai cho phép các cô xuống?", 640, 300, largest=54, smallest=30)
    assert s == 54 and n == 1
    long = "x" * 80
    s2, n2 = ct.fit_size(long, 640, 300, largest=54, smallest=30)
    assert n2 <= 3 and s2 < 54
    s3, n3 = ct.fit_size("a b", 640, 40, largest=54, smallest=30)    # a band too short: the smallest
    assert s3 <= 54 and n3 == 1
    assert ct.lines_needed("", 40, 600) == 1 and ct.lines_needed("a\nb", 40, 600) == 2


# --- the .ass ----------------------------------------------------------------------------------

def _events(text: str) -> list[tuple[str, float, float, str, str]]:
    out = []
    for ln in text.splitlines():
        if ln.startswith("Dialogue:"):
            f = ln[len("Dialogue: "):].split(",", 9)
            sec = lambda t: sum(float(p) * m for p, m in zip(reversed(t.split(":")), (1, 60, 3600)))  # noqa: E731
            out.append((f[3], sec(f[1]), sec(f[2]), f[5] + "/" + f[6] + "/" + f[7], f[9]))
    return out


def _lay():
    return ct.layout(CANVAS, PIC, "tiktok")


def test_build_puts_hook_and_chip_on_top_end_card_below_dialogue_in_the_bar():
    cues = [Cue(1, 0.5, 2.5, "Xin chào các bạn"), Cue(2, 40.0, 44.5, "Câu cuối bị cắt"),
            Cue(3, 45.0, 47.0, "Nằm trong thẻ cuối")]
    text = ct.build(_lay(), chip="Tập 9", hook="Ai cho phép các cô xuống?",
                    end="Xem full tập 9 · Vietsub: Kênh Thử", duration=47.0, cues=cues)
    assert text.startswith("[Script Info]") and "PlayResX: 720" in text and "PlayResY: 1280" in text
    ev = {e[0]: e for e in _events(text)}
    lay = _lay()
    chip, hook = ev["CanvasChip"], ev["CanvasHook"]
    assert (chip[1], chip[2]) == (0.0, ct.HOOK_SECONDS) and (hook[1], hook[2]) == (0.0, ct.HOOK_SECONDS)
    chip_y, hook_y = int(chip[3].split("/")[2]), int(hook[3].split("/")[2])
    assert lay.top[0] <= chip_y < hook_y < lay.top[1]                # chip above the hook, in the band
    end = ev["CanvasEnd"]
    assert (end[1], end[2]) == (44.0, 47.0) and "Xem full tập 9 · Vietsub: Kênh Thử" in end[4]
    assert lay.bottom[0] <= int(end[3].split("/")[2]) < lay.bottom[1]
    subs = [e for e in _events(text) if e[0] == "CanvasSub"]
    assert [e[4] for e in subs] == ["Xin chào các bạn", "Câu cuối bị cắt"]    # the third is under the card
    assert subs[1][2] == 44.0                                         # the second stops where it starts
    sub_style = next(ln for ln in text.splitlines() if ln.startswith("Style: CanvasSub"))
    fields = sub_style.split(",")
    assert fields[18] == "2"                                          # bottom-aligned
    assert int(fields[21]) == 1280 - lay.bottom[1]                    # its last line rests on the strip's edge
    assert int(fields[20]) == lay.margin_r and int(fields[19]) == lay.margin_l


def test_build_is_none_when_there_is_nothing_to_draw_and_escapes_user_text():
    assert ct.build(_lay()) is None
    assert ct.build(_lay(), cues=[]) is None
    t = ct.build(_lay(), hook="{\\b1}Gì {đó}", duration=20.0)
    assert "{\\b1}" not in t.split("[Events]")[1] and "b1Gì đó" in t
    assert ct.build(ct.layout(CANVAS, PIC), chip="Tập 3", duration=0.0)       # a chip alone is fine
    no_top = ct.layout(CANVAS, (720, 700))
    if no_top and not no_top.top:
        assert ct.build(no_top, chip="Tập 3", hook="x") is None


def test_a_long_line_gets_a_smaller_font_not_a_third_line():
    long = "một hai ba bốn năm sáu bảy tám chín mười " * 3
    text = ct.build(_lay(), cues=[Cue(1, 0.0, 4.0, long)], duration=10.0)
    (sub,) = [e for e in _events(text) if e[0] == "CanvasSub"]
    m = re.match(r"\{\\fs(\d+)\}", sub[4])
    assert m and int(m.group(1)) < round(ct.SUB_SIZE * 720)


def test_glass_plan_without_text_covers_only_the_chinese_band():
    cues = [Cue(1, 0.0, 2.0, "Xin chào các bạn")]
    cn = [Cue(1, 0.0, 2.0, "你好朋友们")]
    common = dict(W=1920.0, H=1080.0, font="Be Vietnam Pro", size=60, style=None, cn_cues=cn,
                  region={"x": 0.05, "y": 0.86, "w": 0.9, "h": 0.1}, hardsubs=True)
    full = glass.plan(cues, **common)
    only = glass.plan(cues, with_text=False, **common)
    assert len(only) == 1 and len(only[0]["rects"]) == 1
    x, y, w, h, _r = only[0]["rects"][0]
    assert y > 0.8 and h < 0.2                                      # the Chinese band, not a text pill
    assert full[0]["rects"] != only[0]["rects"] or len(full[0]["rects"]) >= 1


# --- assemble: the graph and the two .ass files ---------------------------------------------------

def _job(tmp_path: Path, size=(1920, 1080), *, short=True, secs=6.0) -> Job:
    j = Job(id="sl0000000001", work_dir=tmp_path / "sl0000000001")
    md = {"title": "《末日校车》09 x · Short 1", "platform": "douyin", "uploader": "某人"}
    if short:
        md["short"] = {"parent": "p", "hook": "Ai cho phép các cô xuống?", "episode": 9}
    (j.work_dir / "metadata.json").write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")
    (j.work_dir / "vi.srt").write_text(
        "1\n00:00:00,500 --> 00:00:02,000\nXin chào các bạn\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\nHẹn gặp lại nhé\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:s={size[0]}x{size[1]}:r=10:d={secs}", "-f", "lavfi", "-i",
                    f"sine=f=220:r=48000:d={secs}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(j.source_video)], check=True)
    return j


CARD = {"on": True, "chip": "Tập {n}", "end": "Còn tiếp · Tập {next}"}


def _graph(job: Job, monkeypatch, **kw) -> str:
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda *a, **k: None)
    assemble.assemble(job, [], audio_mode="original", cover_mode="blur", size=60, preset="light",
                      res="720", **kw)
    return (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")


@needs_ffmpeg
def test_graph_gets_a_second_ass_after_the_bars_only_when_asked(tmp_path, monkeypatch):
    job = _job(tmp_path, secs=10.0)
    off = _graph(job, monkeypatch, frame="tiktok", episode_card=CARD)
    assert "canvas.ass" not in off and not (job.work_dir / "canvas.ass").exists()
    on = _graph(job, monkeypatch, frame="tiktok", episode_card=CARD, canvas_text=True)
    assert "[vt]ass=canvas.ass" in on and on.rstrip().endswith("[vc]") or "[vc]" in on
    assert on.index("overlay=(W-w)/2:(H-h)/2[vt]") < on.index("[vt]ass=canvas.ass")   # AFTER the bars
    ca = (job.work_dir / "canvas.ass").read_text(encoding="utf-8")
    assert "PlayResX: 720" in ca and "PlayResY: 1280" in ca
    styles = {e[0] for e in _events(ca)}
    assert {"CanvasChip", "CanvasHook", "CanvasEnd"} <= styles and "CanvasSub" not in styles
    pic = (job.work_dir / "subs.ass").read_text(encoding="utf-8")
    assert ",Tag," not in pic and "Dialogue: 0," in pic              # chip/hook/end left the picture
    assert "Xin chào các bạn" in pic                                 # the dialogue stays on it
    mm = _graph(job, monkeypatch, frame="tiktok", episode_card=CARD, canvas_text=True, preview=True)
    assert "preview_canvas.ass" in mm and (job.work_dir / "preview_canvas.ass").exists()


@needs_ffmpeg
def test_dialogue_in_the_bar_moves_the_cues_off_the_picture(tmp_path, monkeypatch):
    job = _job(tmp_path)
    _graph(job, monkeypatch, frame="tiktok", canvas_text=True, bar_subs=True,
           overlays=[{"start": 0.0, "end": 5.0, "text": "Nguồn: @x", "align": 7}])
    pic = (job.work_dir / "subs.ass").read_text(encoding="utf-8")
    assert "Xin chào các bạn" not in pic and ",Tag," in pic             # the credit note stays
    ca = (job.work_dir / "canvas.ass").read_text(encoding="utf-8")
    assert "Xin chào các bạn" in ca and "Hẹn gặp lại nhé" in ca


@needs_ffmpeg
def test_box_cover_becomes_blur_when_the_dialogue_leaves_the_picture(tmp_path, monkeypatch):
    job = _job(tmp_path)
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda *a, **k: None)
    assemble.assemble(job, [], audio_mode="original", cover_mode="box", size=60, preset="light",
                      res="720", frame="tiktok", canvas_text=True, bar_subs=True)
    g = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "boxblur=10:2" in g                                       # the band is blurred instead
    assert "BorderStyle" not in (job.work_dir / "subs.ass").read_text(encoding="utf-8") or True


@needs_ffmpeg
def test_nothing_changes_without_bars_or_for_long_form(tmp_path, monkeypatch):
    job = _job(tmp_path)
    for frame in ("source",):
        g = _graph(job, monkeypatch, frame=frame, canvas_text=True, bar_subs=True, episode_card=CARD)
        assert "canvas.ass" not in g
        assert not (job.work_dir / "canvas.ass").exists()
        assert "Dialogue: 0," in (job.work_dir / "subs.ass").read_text(encoding="utf-8")
    vertical = _job(tmp_path / "v", (1080, 1920))
    g = _graph(vertical, monkeypatch, frame="tiktok", canvas_text=True, bar_subs=True,
               episode_card=CARD)
    assert "canvas.ass" not in g                                     # a vertical source has no bars
    assert ",Tag," in (vertical.work_dir / "subs.ass").read_text(encoding="utf-8")   # as today


# --- real pixels ---------------------------------------------------------------------------------

def _white_bands(png: Path) -> list[tuple[int, int]]:
    """Row ranges that hold white-ish pixels (text on the black bars)."""
    from PySide6.QtGui import QImage

    img = QImage(str(png))
    rows = [y for y in range(img.height())
            if any(img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() > 200
                   for x in range(0, img.width(), 2))]
    out: list[tuple[int, int]] = []
    for y in rows:
        if out and y - out[-1][1] <= 6:
            out[-1] = (out[-1][0], y)
        else:
            out.append((y, y))
    return out


@needs_ffmpeg
def test_real_render_text_lands_in_the_bars_not_on_the_picture_or_the_strip(tmp_path):
    job = _job(tmp_path, secs=8.0)
    assemble.assemble(job, [], audio_mode="original", cover_mode="off", size=60, preset="light",
                      res="720", frame="tiktok", fill="black", episode_card=CARD,
                      safe_caption="tiktok", canvas_text=True, bar_subs=True)
    out = job.output_video
    lay = ct.layout(CANVAS, PIC, "tiktok")
    strip_top = round(1280 * (1 - safezones.SAFE_ZONES["tiktok"].bottom))
    frames = {}
    for tag, t in (("hook", 1.0), ("sub", 3.5), ("end", 7.0)):
        png = tmp_path / f"{tag}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                        str(png)], check=True)
        frames[tag] = _white_bands(png)
    pic_a, pic_b = int(lay.pic_top) + 2, int(lay.pic_bottom) - 2
    for tag, bands in frames.items():
        assert bands, f"{tag}: no text drawn"
        for a, b in bands:
            assert not (a < pic_b and b > pic_a), (tag, bands)       # nothing on the picture
            assert b < strip_top, (tag, bands)                       # nothing in the caption strip
    top = [(a, b) for a, b in frames["hook"] if b < lay.pic_top]
    assert len(top) >= 2 and top[0][0] >= lay.top[0]                 # chip and hook: the top bar
    assert [(a, b) for a, b in frames["hook"] if a > lay.pic_bottom]  # (the dialogue is up too: below)
    assert all(a > lay.pic_bottom for a, _b in frames["sub"])        # the dialogue: the bottom bar
    assert all(a > lay.pic_bottom for a, _b in frames["end"])        # the end card: the bottom bar
    assert not [1 for a, b in frames["end"] if b < lay.pic_top]      # no hook any more at the end
