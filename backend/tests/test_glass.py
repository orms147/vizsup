"""“Kính mờ trắng”: the frosted white glass behind each subtitle (app.pipeline.glass) —
pure geometry, the mask .ass and the filter graph, how the render wires it, and the
style choice in Kiểu chữ (persisted, recolours the text, the editor preview)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.models import Cue, Job
from app.pipeline import assemble, glass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

W, H = 1920.0, 1080.0
STYLE = {"glass": True, "margin_l": 60, "margin_r": 60, "margin_v": 60, "alignment": 2}


def _plan(cues, **kw):
    kw.setdefault("hardsubs", True)
    return glass.plan(cues, W=W, H=H, font="Be Vietnam Pro", size=60,
                      style=kw.pop("style", STYLE), **kw)


def test_style_flag_and_text_colours():
    assert glass.is_glass({"glass": True}) and not glass.is_glass({}) and not glass.is_glass(None)
    plain = glass.text_style({"primary": "#FFFFFF", "margin_v": 80})
    assert plain["primary"] == glass.GLASS_TEXT["primary"] and plain["margin_v"] == 80
    mine = {"glass": True, "primary": "#003366"}
    assert glass.text_style(mine)["primary"] == "#003366"        # user's recolour kept


def test_text_is_measured_wider_for_longer_lines():
    short = glass.text_width("Xin chào", font="Be Vietnam Pro", px=60)
    long = glass.text_width("Xin chào các bạn, hôm nay", font="Be Vietnam Pro", px=60)
    assert 150 < short < long < 1100
    w, n = glass.text_block("chữ " * 80, font="Be Vietnam Pro", px=60, wrap_w=1800)
    assert n >= 2 and w <= 1800                                  # wraps, balanced


def test_pill_hugs_the_vi_line_and_covers_the_chinese_band():
    ev = _plan([Cue(index=1, start=1.0, end=3.0, text="Màn hình trong rất lớn,")])
    assert len(ev) == 1 and len(ev[0]["rects"]) == 1              # merged into one pill
    x, y, w, h, r = ev[0]["rects"][0]
    assert abs((x + w / 2) - 0.5) < 0.01                          # centred
    assert w < 0.7                                                # not a full-width bar
    y0, y1 = glass.DEFAULT_CN_BAND
    assert y <= y0 and y + h >= y1 - 0.01                         # the Chinese band is inside
    assert y + h <= 1.0 and 0 < r * H <= h * H / 2


def test_cn_text_widens_the_pill_and_region_bounds_it():
    vi = [Cue(index=1, start=1.0, end=3.0, text="Ngắn")]
    cn = [Cue(index=1, start=0.9, end=3.1, text="外屏要么太长要么太厚用着没有直板机舒服")]
    short = _plan(vi, cn_cues=[Cue(index=1, start=0.9, end=3.1, text="你好")])[0]["rects"][0]
    wide = _plan(vi, cn_cues=cn)[0]["rects"][0]
    unknown = _plan(vi)[0]["rects"][0]                  # no CN line known: a safe 60% band
    assert short[2] < 0.2 and wide[2] > short[2] + 0.4
    assert 0.6 <= unknown[2] < 0.7
    region = {"x": 0.3, "y": 0.86, "w": 0.4, "h": 0.07}
    bounded = _plan(vi, cn_cues=cn, region=region)[0]["rects"][0]
    assert bounded[2] < 0.5 and bounded[0] >= 0.28                # the detected band caps it


def test_no_hardsubs_means_just_the_text_pill():
    vi = [Cue(index=1, start=1.0, end=3.0, text="Ngắn")]
    cn = [Cue(index=1, start=0.9, end=3.1, text="外屏要么太长要么太厚用着没有直板机舒服")]
    r = _plan(vi, cn_cues=cn, hardsubs=False)[0]["rects"][0]
    assert r[2] < 0.2


def test_subtitle_moved_up_gets_two_pills():
    up = {**STYLE, "pos": [0.5, 0.3]}
    ev = _plan([Cue(index=1, start=0.0, end=2.0, text="Ở trên cao")], style=up)
    rects = ev[0]["rects"]
    assert len(rects) == 2
    assert min(r[1] for r in rects) < 0.35 and max(r[1] for r in rects) > 0.8


def test_short_gaps_are_bridged_and_empty_lines_skipped():
    ev = _plan([Cue(index=1, start=0.0, end=1.0, text="một"),
                Cue(index=2, start=1.3, end=2.0, text="hai"),
                Cue(index=3, start=2.0, end=2.5, text="  "),
                Cue(index=4, start=4.0, end=5.0, text="ba")])
    assert [e["start"] for e in ev] == [0.0, 1.3, 4.0]
    assert ev[0]["end"] == 1.3 and ev[1]["end"] == 2.0           # 0.3 s bridged, 2 s not


def test_band_covers_every_rect():
    ev = _plan([Cue(index=1, start=0.0, end=1.0, text="một câu khá dài để thử"),
                Cue(index=2, start=2.0, end=3.0, text="hai")])
    bx, by, bw, bh = glass.band(ev)
    for e in ev:
        for x, y, w, h, _r in e["rects"]:
            assert bx <= x and by <= y and x + w <= bx + bw + 1e-6 and y + h <= by + bh + 1e-6
    assert glass.band([]) is None


def test_rounded_rect_path_and_mask_file(tmp_path):
    path = glass.rounded_rect_path(10, 20, 200, 60, 30)
    assert path.startswith("m 40 20 l 180 20 b ") and path.count(" b ") == 4
    ev = _plan([Cue(index=1, start=61.5, end=63.0, text="xin chào")])
    bnd = glass.band(ev)
    p = glass.write_mask(ev, tmp_path / "m.ass", bnd, W=W, H=H)
    text = p.read_text(encoding="utf-8")
    assert f"PlayResX: {round(bnd[2] * W)}" in text and f"PlayResY: {round(bnd[3] * H)}" in text
    assert "Dialogue: 0,0:01:01.50,0:01:03.00,M,,0,0,0,,{\\an7\\pos(0,0)\\bord0\\shad0\\p1}m " \
        in text


def test_filter_parts_blur_tint_mask_overlay():
    parts = glass.filter_parts("[vscl]", (0.03, 0.82, 0.94, 0.15), "glass_mask.ass", "[vg]",
                               height=720)
    g = ";".join(parts)
    assert parts[0] == "[vscl]split=3[gmain][gblur][gmask]"
    crop = "crop=iw*0.9400:ih*0.1500:iw*0.0300:ih*0.8200"
    assert g.count(crop) == 2                                     # blur + mask: same band
    assert "gblur=sigma=14.7:steps=2" in g                        # 22 at 1080 → 14.7 at 720
    assert "drawbox=c=white@0.4:t=fill" in g                      # today's default tint
    assert "ass=glass_mask.ass,format=gray,lut=c0=val*255/235[galpha]" in g
    assert parts[-1] == "[gmain][gglass]overlay=main_w*0.0300:main_h*0.8200[vg]"


# --- render wiring ------------------------------------------------------------------------

@pytest.fixture()
def job(tmp_path, monkeypatch):
    j = Job(id="g", work_dir=tmp_path / "job")
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 1.2)
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(assemble, "probe_size", lambda p: (1920, 1080))
    monkeypatch.setattr(assemble, "_render_dub", lambda *a, **k: "dub/list.txt")
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda *a, **k: None)
    j.vi_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nxin chào\n", encoding="utf-8")
    j.cn_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\n你好\n", encoding="utf-8")
    j.source_video.write_bytes(b"v")
    return j


def test_glass_style_renders_the_glass_cover(job):
    assemble.assemble(job, [], style={"glass": True, "primary": "#1C1C1E"}, cover_mode="box",
                      res="720")
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[vscl]split=3[gmain][gblur][gmask]" in graph          # scaled, then glass…
    assert graph.index("[gmain][gglass]overlay") < graph.index("ass=subs.ass")  # …then text
    assert "gblur=sigma=14.7:" in graph and "white@0.4:" in graph   # default look
    assert (job.work_dir / glass.MASK_FILE).exists()
    subs = (job.work_dir / "subs.ass").read_text(encoding="utf-8")
    assert "Style: Default,Be Vietnam Pro,60,&H001E1C1C," in subs  # dark text…
    style_line = subs.split("Style: Default")[1].splitlines()[0]
    assert ",4,2," not in style_line                               # …and no black box
    info = json.loads((job.work_dir / assemble.RENDER_INFO).read_text(encoding="utf-8"))
    assert info["cover_mode"] == "glass" and len(info["glass_band"]) == 4


def test_glass_cover_without_the_style_gets_dark_text(job):
    assemble.assemble(job, [], cover_mode="glass", style={"primary": "#FFFFFF"})
    subs = (job.work_dir / "subs.ass").read_text(encoding="utf-8")
    assert "&H001E1C1C" in subs.split("Style: Default")[1].splitlines()[0]


def test_preview_uses_its_own_mask(job):
    assemble.assemble(job, [], style={"glass": True}, preview=True)
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "ass=preview_glass.ass" in graph and (job.work_dir / "preview_glass.ass").exists()
    assert not (job.work_dir / glass.MASK_FILE).exists()


def test_plain_styles_unchanged(job):
    assemble.assemble(job, [], style={"primary": "#FFFFFF"}, cover_mode="box")
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "gblur" not in graph and "glass" not in graph


def test_softsub_export_repeats_the_glass(job):
    from app.pipeline import export

    assemble.assemble(job, [], style={"glass": True})
    cmd = export.softsub_cmd(job, job.work_dir / "x.mkv")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "alphamerge" in graph and "ass=glass_mask.ass" in graph


def test_job_context_reads_region_hardsubs_and_cn(job):
    (job.work_dir / "hardsub_region.json").write_text(
        json.dumps({"x": 0.2, "y": 0.85, "w": 0.6, "h": 0.08}), encoding="utf-8")
    (job.work_dir / "metadata.json").write_text(json.dumps({"has_hardsubs": False}),
                                                encoding="utf-8")
    region, hardsubs, cn = glass.job_context(job)
    assert region["w"] == 0.6 and hardsubs is False and cn[0].text == "你好"


# --- Kiểu chữ: the style choice (offscreen Qt) ----------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_style_panel_glass_choice_persists_and_recolours(qapp):
    from desktop.widgets.style_panel import GLASS_STYLE, StylePanel

    sp = StylePanel()
    assert sp.box_style.currentText() == "Viền chữ (không nền)"     # default unchanged
    assert "glass" not in sp.get_style()
    sp.box_style.setCurrentText(GLASS_STYLE)                        # the user picks it
    st = sp.get_style()
    assert st["glass"] is True and st["primary"].lower() == "#1c1c1e" and st["outline_w"] == 0
    saved = json.loads(json.dumps({"font": "Be Vietnam Pro", "size": 20, "style": st}))
    other = StylePanel()
    other.set_from(saved)                                           # style.json round trip
    assert other.box_style.currentText() == GLASS_STYLE
    assert other.get_style()["glass"] is True
    sp.box_style.setCurrentText("Hộp mờ")                           # leaving it: white again
    assert sp.get_style()["primary"].lower() == "#ffffff" and "glass" not in sp.get_style()


def test_style_panel_loading_never_recolours(qapp):
    from desktop.widgets.style_panel import StylePanel

    sp = StylePanel()
    sp.set_from({"style": {"glass": True, "primary": "#ffffff", "outline_w": 2}})
    assert sp.get_style()["primary"].lower() == "#ffffff"           # their choice, kept
    sp.set_from({"style": {"border_style": 1, "box_alpha": 255, "primary": "#222222"}})
    assert sp.box_style.currentText() == "Viền chữ (không nền)"
    assert sp.get_style()["primary"].lower() == "#222222"


def test_canvas_draws_a_frosted_pill(qapp):
    from PySide6.QtGui import QColor, QImage

    from desktop.widgets.video_canvas import VideoCanvas

    c = VideoCanvas()
    c.resize(640, 360)
    frame = QImage(1920, 1080, QImage.Format_RGB32)
    frame.fill(QColor("#101010"))
    c.set_frame(frame, 1920, 1080)
    c.set_caption("Xin chào các bạn")
    c.set_style({"glass": True, "primary": "#1c1c1e", "outline_w": 0}, 20, "Be Vietnam Pro")
    img = c.grab().toImage()
    c._compute_disp()
    from PySide6.QtGui import QFont, QFontMetrics

    f = QFont("Be Vietnam Pro")
    f.setPixelSize(max(8, int(c._text_h_px())))
    pill = c.glass_rect(QFontMetrics(f))
    inside = img.pixelColor(int(pill.left() + 4), int(pill.center().y()))
    outside = img.pixelColor(int(pill.left() - 20), int(pill.center().y()))
    assert inside.lightness() > 90 > 40 > outside.lightness()      # white glass on dark video


# --- the adjustable look: "Độ trắng của nền" + "Độ nhòe phía sau" ---------------------------

def test_look_defaults_and_clamps():
    assert glass.look(None) == {"tint": glass.TINT, "blur": glass.BLUR_SIGMA}
    assert glass.look({"glass": True}) == {"tint": 0.4, "blur": 22}       # lighter than 0.6
    assert glass.look({"glass_tint": 25, "glass_blur": 30}) == {"tint": 0.25, "blur": 30}
    assert glass.look({"glass_tint": 150, "glass_blur": 99}) == {"tint": 0.9, "blur": 40}
    assert glass.look({"glass_tint": -5, "glass_blur": 0}) == {"tint": 0.0, "blur": 4}
    assert glass.look({"glass_tint": "x", "glass_blur": None}) == glass.look(None)
    assert glass.look({"glass_tint": float("nan")})["tint"] == glass.TINT
    assert glass.BLUR_RANGE[0] < glass.BLUR_SAFE < glass.BLUR_SIGMA


def test_filter_parts_takes_tint_and_blur():
    bnd = (0.03, 0.82, 0.94, 0.15)
    g = ";".join(glass.filter_parts("[v]", bnd, "m.ass", "[o]", height=1080, tint=0.25, blur=30))
    assert "gblur=sigma=30:steps=2" in g and "drawbox=c=white@0.25:t=fill" in g
    g = ";".join(glass.filter_parts("[v]", bnd, "m.ass", "[o]", height=720, tint=0, blur=16))
    assert "gblur=sigma=10.7:" in g and "white@0:" in g      # scaled, not rounded to 11
    g = ";".join(glass.filter_parts("[v]", bnd, "m.ass", "[o]", height=360, tint=1.5, blur=1))
    assert "gblur=sigma=0.5:" in g and "white@1:" in g


def test_render_follows_the_style_look(job):
    assemble.assemble(job, [], style={"glass": True, "glass_tint": 25, "glass_blur": 30},
                      res="720")
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "gblur=sigma=20:" in graph and "white@0.25:" in graph   # 30 at 1080 → 20 at 720
    info = json.loads((job.work_dir / assemble.RENDER_INFO).read_text(encoding="utf-8"))
    assert info["glass_tint"] == 25 and info["glass_blur"] == 30


def test_quick_preview_follows_the_style_look(job):
    assemble.assemble(job, [], style={"glass": True, "glass_tint": 10, "glass_blur": 18},
                      preview=True)
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "gblur=sigma=18:" in graph and "white@0.1:" in graph


def test_softsub_export_repeats_the_look(job, monkeypatch):
    from app.pipeline import export

    assemble.assemble(job, [], style={"glass": True, "glass_tint": 70, "glass_blur": 26})
    monkeypatch.setattr(assemble, "probe_size", lambda p: (1280, 720))   # source height
    cmd = export.softsub_cmd(job, job.work_dir / "x.mkv")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "white@0.7:" in graph and "gblur=sigma=17.3:" in graph
    # a render made before the sliders existed: its own 60 % / 22, not today's default
    info = json.loads((job.work_dir / assemble.RENDER_INFO).read_text(encoding="utf-8"))
    for k in ("glass_tint", "glass_blur"):
        info.pop(k)
    (job.work_dir / assemble.RENDER_INFO).write_text(json.dumps(info), encoding="utf-8")
    cmd = export.softsub_cmd(job, job.work_dir / "x.mkv")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "white@0.6:" in graph and "gblur=sigma=14.7:" in graph


def test_style_preview_frame_follows_the_look(job, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd

    monkeypatch.setattr(assemble.subprocess, "run", fake_run)
    cues = [Cue(index=1, start=1.0, end=3.0, text="xin chào")]
    assemble.style_preview_frame(job, cues, style={"glass": True, "glass_tint": 15,
                                                   "glass_blur": 34})
    graph = seen["cmd"][seen["cmd"].index("-filter_complex") + 1]
    assert "gblur=sigma=34:" in graph and "white@0.15:" in graph


def test_style_panel_glass_sliders(qapp):
    from desktop.widgets.style_panel import GLASS_STYLE, StylePanel

    sp = StylePanel()
    assert sp._glass_row.isHidden()                                # not for the plain styles
    assert "glass_tint" not in sp.get_style()
    sp.box_style.setCurrentText(GLASS_STYLE)
    assert not sp._glass_row.isHidden()
    st = sp.get_style()
    assert st["glass_tint"] == 40 and st["glass_blur"] == 22       # the new defaults
    assert sp.glass_warn.isHidden()
    sp.glass_blur.setValue(10)                                     # Chinese may show through
    assert not sp.glass_warn.isHidden()
    sp.glass_blur.setValue(glass.BLUR_SAFE)
    assert sp.glass_warn.isHidden()
    sp.glass_tint.setValue(55)
    saved = json.loads(json.dumps({"font": "Be Vietnam Pro", "size": 20, "style": sp.get_style()}))
    other = StylePanel()
    other.set_from(saved)                                          # style.json round trip
    assert (other.glass_tint.value(), other.glass_blur.value()) == (55, glass.BLUR_SAFE)
    assert not other._glass_row.isHidden()
    other.set_from({"style": {"glass": True}})                     # older style.json
    assert (other.glass_tint.value(), other.glass_blur.value()) == (40, 22)
    sp.box_style.setCurrentText("Hộp mờ")
    assert sp._glass_row.isHidden() and "glass_blur" not in sp.get_style()


def test_low_tint_turns_the_text_white_and_back(qapp):
    from desktop.widgets.style_panel import GLASS_LIGHT_TEXT_BELOW, GLASS_STYLE, StylePanel

    sp = StylePanel()
    sp.box_style.setCurrentText(GLASS_STYLE)
    assert sp.get_style()["primary"].lower() == "#1c1c1e"
    sp.glass_tint.setValue(30)                                     # still above: no change
    assert sp.get_style()["primary"].lower() == "#1c1c1e"
    sp.glass_tint.setValue(GLASS_LIGHT_TEXT_BELOW - 5)             # nearly clear glass
    st = sp.get_style()
    assert st["primary"].lower() == "#ffffff" and st["outline_w"] == 2
    assert st["outline"].lower() == "#000000"
    sp.c_text.set_hex("#f0e8d0")                                   # the user's own colour…
    sp.glass_tint.setValue(5)                                      # …sticks below the line
    assert sp.get_style()["primary"].lower() == "#f0e8d0"
    sp.glass_tint.setValue(50)                                     # whiter again: dark text
    st = sp.get_style()
    assert st["primary"].lower() == "#1c1c1e" and st["outline_w"] == 0
    sp.set_from({"style": {"glass": True, "glass_tint": 10, "primary": "#1c1c1e"}})
    assert sp.get_style()["primary"].lower() == "#1c1c1e"          # loading never recolours


def test_canvas_follows_tint_and_blur(qapp):
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage

    from desktop.widgets.video_canvas import VideoCanvas, glass_shrink

    assert glass_shrink(22, 1080) == 14 and glass_shrink(22, 540) == 7
    assert glass_shrink(40, 1080) > glass_shrink(10, 1080) >= 1

    def inside(tint: int) -> int:
        c = VideoCanvas()
        c.resize(640, 360)
        frame = QImage(1920, 1080, QImage.Format_RGB32)
        frame.fill(QColor("#101010"))
        c.set_frame(frame, 1920, 1080)
        c.set_caption("Xin chào các bạn")
        c.set_style({"glass": True, "glass_tint": tint, "outline_w": 0, "primary": "#1c1c1e"},
                    20, "Be Vietnam Pro")
        img = c.grab().toImage()
        c._compute_disp()
        f = QFont("Be Vietnam Pro")
        f.setPixelSize(max(8, int(c._text_h_px())))
        pill = c.glass_rect(QFontMetrics(f))
        return img.pixelColor(int(pill.left() + 4), int(pill.center().y())).lightness()

    clear, mid, milky = inside(0), inside(40), inside(90)
    assert clear < 40 < mid < milky and milky > 200
