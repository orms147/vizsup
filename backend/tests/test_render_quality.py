"""“Chất lượng” on the render page: output resolution (short side, never upscaled,
9:16 scaled proportionally) × a size/sharpness preset mapped to libx264 CRF / NVENC
CQ. Construct-only — probe/ffmpeg are monkeypatched, nothing encodes."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.models import Cue, Job
from app.pipeline import assemble

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`


@pytest.fixture()
def job(tmp_path, monkeypatch):
    j = Job(id="q", work_dir=tmp_path / "job")
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 1.2)
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    segs = [(Cue(index=i, start=2.0 * i, end=2.0 * i + 1.5, text=f"c{i}"),
             j.tts_dir / f"seg_{i:04d}.mp3") for i in range(1, 4)]
    return j, segs


def _build(j, segs, **kw):
    cmd = assemble._build_render(j, segs, audio_mode="replace", orig_volume=1.0,
                                 dub_volume=1.0, **kw)
    return cmd, (j.work_dir / "filter_complex.txt").read_text(encoding="utf-8")


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_default_quality_is_unchanged(job):
    j, segs = job
    cmd, graph = _build(j, segs)
    assert "scale" not in graph                                 # source size kept
    assert _arg(cmd, "-crf") == "20" and _arg(cmd, "-b:a") == "192k"


def test_720p_scales_before_the_burn_and_never_upscales(job):
    """Downscale BEFORE the ass filter (text drawn at the final size, layout scales with
    PlayRes); ``min`` keeps a smaller source as it is; portrait clips use the width."""
    j, segs = job
    _cmd, graph = _build(j, segs, res="720")
    assert ("[0:v]scale=w='if(gte(iw,ih),-2,min(iw,720))':h='if(gte(iw,ih),min(ih,720),-2)'"
            ":flags=lanczos[vscl]") in graph
    assert graph.index("scale=") < graph.index("ass=subs.ass")
    assert "[vscl]ass=subs.ass" in graph
    # with the source size known, the exact (16:9-snapped) size is used
    _cmd, graph = _build(j, segs, res="720", burn_w=1280, burn_h=720)
    assert "[0:v]scale=1280:720:flags=lanczos,setsar=1[vscl]" in graph


def test_presets_map_to_crf_cq_and_audio():
    for preset, crf, cq, audio in (("light", "27", "30", "128k"), ("balanced", "23", "25", "192k"),
                                   ("sharp", "20", "21", "192k")):
        cpu = assemble._video_args("libx264", False, preset)
        gpu = assemble._video_args("h264_nvenc", False, preset)
        assert cpu[cpu.index("-crf") + 1] == crf
        assert gpu[gpu.index("-cq") + 1] == cq
        assert assemble._audio_bitrate(False, preset) == audio
    # the quick preview ignores the preset
    assert assemble._video_args("libx264", True, "sharp")[3] == "30"
    assert assemble._audio_bitrate(True, "sharp") == "128k"


def test_preset_rides_into_the_command(job):
    j, segs = job
    cmd, _g = _build(j, segs, preset="light")
    assert _arg(cmd, "-crf") == "27" and _arg(cmd, "-b:a") == "128k"


def test_tiktok_canvas_scales_proportionally(job):
    assert assemble.tiktok_canvas("source") == (1080, 1920)
    assert assemble.tiktok_canvas("1080") == (1080, 1920)
    assert assemble.tiktok_canvas("720") == (720, 1280)
    assert assemble.tiktok_canvas("480") == (480, 854)
    j, segs = job
    _cmd, graph = _build(j, segs, frame="tiktok", res="720")
    assert "[vfi]scale=720:1280:force_original_aspect_ratio=decrease" in graph  # blur-fill split
    assert ("crop=180:320,boxblur=16:3,eq=brightness=-0.22:saturation=0.55,scale=720:1280"
            in graph)                                           # bars
    assert "[vscl]" not in graph                                # the fit IS the scale


def test_preview_stays_640p_whatever_the_quality(job):
    j, segs = job
    _cmd, graph = _build(j, segs, res="480", preview=True)
    assert "[vscl]" not in graph and "scale=-2:640[vp]" in graph


def test_burn_and_output_sizes():
    src = (1920, 1080)
    assert assemble.burn_size(src, "source", "source") == (1920, 1080)
    assert assemble.burn_size(src, "source", "720") == (1280, 720)
    assert assemble.burn_size(src, "source", "480") == (854, 480)
    assert assemble.burn_size((1280, 720), "source", "1080") == (1280, 720)   # no upscale
    assert assemble.burn_size((1080, 1920), "source", "720") == (720, 1280)   # portrait
    assert assemble.burn_size(src, "tiktok", "720") == (720, 404)    # as ffmpeg's fit does
    assert assemble.output_size(src, "tiktok", "720") == (720, 1280)
    assert assemble.burn_size(None, "source", "720") is None


def test_a_hair_off_16_9_snaps_so_episodes_join_without_reencoding():
    # 2026-10-29 E2E: two 《末日校车》 episodes came out 1282×720, two 1280×720 → "Ghép
    # tập" had to re-encode half of them
    assert assemble.burn_size((1924, 1080), "source", "720") == (1280, 720)
    assert assemble.burn_size((1080, 1924), "source", "720") == (720, 1280)
    assert assemble.burn_size((1440, 1080), "source", "720") == (960, 720)    # 4:3 kept
    assert assemble.burn_size((2560, 1080), "source", "720") == (1706, 720)   # 21:9 kept


def test_size_estimate_shrinks_with_lines_and_preset():
    src, dur = (1920, 1080), 28 * 60
    sharp = assemble.estimate_size_mb(src, "source", "source", "sharp", dur)
    assert 900 < sharp < 1150                                   # ≈ the measured 1 GB
    assert assemble.estimate_size_mb(src, "source", "720", "sharp", dur) < sharp
    assert assemble.estimate_size_mb(src, "source", "source", "light", dur) < sharp / 2
    assert assemble.estimate_size_mb(None, "source", "720", "sharp", dur) is None


def test_assemble_forwards_quality_and_records_it(job, monkeypatch):
    j, segs = job
    j.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    j.source_video.write_bytes(b"v")
    seen: dict = {}
    real = assemble._build_render

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(assemble, "_build_render", spy)
    monkeypatch.setattr(assemble, "_render_dub", lambda *a, **k: "dub/list.txt")
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(assemble, "probe_size", lambda p: (1920, 1080))
    assemble.assemble(j, segs, res="720", preset="balanced")
    assert seen["res"] == "720" and seen["preset"] == "balanced"
    info = json.loads((j.work_dir / assemble.RENDER_INFO).read_text(encoding="utf-8"))
    assert info["res"] == "720" and info["preset"] == "balanced" and info["size"] == [1280, 720]
    assemble.assemble(j, segs, res="4k", preset="ultra")        # junk → safe defaults
    assert seen["res"] == "source" and seen["preset"] is None


# --- the render page (offscreen Qt) ------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_render_view_quality_is_sent_and_remembered(qapp):
    from desktop import store
    from desktop.views.render_view import RenderView

    rv = RenderView()
    got: list[dict] = []
    rv.renderRequested.connect(got.append)
    rv._emit_render()
    assert got[-1]["res"] == "source" and got[-1]["preset"] == "sharp"
    rv.res.setCurrentIndex(rv.res.findData("720"))
    rv.preset.setCurrentIndex(rv.preset.findData("light"))
    rv._emit_render()
    assert got[-1]["res"] == "720" and got[-1]["preset"] == "light"
    st = store.load_ui_state()
    assert st["render_res"] == "720" and st["render_quality"] == "light"
    again = RenderView()
    assert again.res.currentData() == "720" and again.preset.currentData() == "light"


def test_render_view_greys_out_upscales_and_shows_a_size(qapp):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    rv.res.setCurrentIndex(rv.res.findData("1080"))
    rv._src_size, rv._src_dur = (1280, 720), 600.0
    rv._sync_quality()
    model = rv.res.model()
    assert not model.item(rv.res.findData("1080")).isEnabled()
    assert model.item(rv.res.findData("720")).isEnabled()
    assert rv.res.currentData() == "source"                      # moved off the upscale
    assert "1280×720" in rv.size_lbl.text() and "MB" in rv.size_lbl.text()
    rv.frame.setCurrentIndex(rv.frame.findData("tiktok"))         # 9:16: up to 1080×1920
    assert model.item(rv.res.findData("1080")).isEnabled()
    assert "1080×1920" in rv.size_lbl.text()


def test_render_view_cover_follows_the_glass_style(qapp):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    glass_i = rv.cover.findData("glass")
    assert not rv.cover.model().item(glass_i).isEnabled()         # needs the glass style
    rv.set_style({"glass": True, "primary": "#1c1c1e"}, 20, "Be Vietnam Pro")
    assert rv.cover.currentData() == "glass" and not rv.cover.isEnabled()
    got: list[dict] = []
    rv.renderRequested.connect(got.append)
    rv._emit_render()
    assert got[-1]["cover_mode"] == "glass"
    rv.set_style({"primary": "#ffffff"}, 20, "Be Vietnam Pro")    # back to a plain style
    assert rv.cover.currentData() == "box" and rv.cover.isEnabled()
