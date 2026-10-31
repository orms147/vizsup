"""The fan-sub look the owner showed (2026-10-31): "CHỮ IN HOA" (only the burned line is in
capitals), the bundled Bangers / Coiny faces, the built-in "Mẫu kiểu" and a style that
brings its own cover (the blurred Chinese band)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import Cue  # noqa: E402
from app.pipeline import srt  # noqa: E402

FONTS = Path(__file__).resolve().parents[2] / "desktop" / "assets" / "fonts"


def test_display_text_capitalises_vietnamese_but_not_tags():
    up = {"uppercase": True}
    assert srt.display_text("Thiên Mệnh Chi Tử vu khống ta trộm bảo vật.", up) == \
        "THIÊN MỆNH CHI TỬ VU KHỐNG TA TRỘM BẢO VẬT."
    assert srt.display_text("đừng ư ơi", up) == "ĐỪNG Ư ƠI"
    assert srt.display_text("{\\i1}nghiêng{\\i0} rồi\\Ndòng hai", up) == \
        "{\\i1}NGHIÊNG{\\i0} RỒI\\NDÒNG HAI"
    assert srt.display_text("giữ nguyên", {}) == "giữ nguyên"


def test_only_the_burned_line_is_in_capitals(tmp_path):
    cues = [Cue(1, 0, 1, "Ta sai rồi"), Cue(2, 1, 2, "sau này không dám nữa")]
    out = srt.write_ass(cues, tmp_path / "a.ass", font="Bangers",
                        style={"uppercase": True}, cue_styles={2: {"uppercase": False}})
    body = out.read_text(encoding="utf-8")
    assert ",,TA SAI RỒI" in body and ",,sau này không dám nữa" in body
    assert cues[0].text == "Ta sai rồi"                     # vi.srt / TTS keep the words
    plain = srt.write_ass(cues[:1], tmp_path / "b.ass").read_text(encoding="utf-8")
    assert ",,Ta sai rồi" in plain


def test_the_comic_fonts_are_bundled_with_their_licence():
    for name in ("Bangers-Regular.ttf", "Coiny-Regular.ttf", "Bangers-OFL.txt", "Coiny-OFL.txt"):
        assert (FONTS / name).is_file(), name
    from app import fontfiles

    names = {p.name for p in fontfiles.files_for("Bangers", FONTS.parent / "nope", FONTS)}
    assert {"Bangers-Regular.ttf", "Coiny-Regular.ttf"} <= names   # libass finds them


@pytest.fixture()
def job(tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import assemble

    j = Job(id="c", work_dir=tmp_path / "job")
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 1.2)
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(assemble, "probe_size", lambda p: (1920, 1080))
    monkeypatch.setattr(assemble, "_render_dub", lambda *a, **k: "dub/list.txt")
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda *a, **k: None)
    j.vi_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nta sai rồi\n", encoding="utf-8")
    j.source_video.write_bytes(b"v")
    return j


def test_a_style_with_its_own_cover_replaces_a_stale_glass_cover(job):
    """The series' render settings still say "glass" (the old look); the new comic style
    asks for the blurred band — white outlined capitals, no glass."""
    from app.pipeline import assemble

    assemble.assemble(job, [], cover_mode="glass",
                      style={"primary": "#FFFFFF", "cover": "blur", "uppercase": True})
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    subs = (job.work_dir / "subs.ass").read_text(encoding="utf-8")
    assert "boxblur" in graph and "glass" not in graph
    assert ",,TA SAI RỒI" in subs
    assert "&H00FFFFFF" in subs.split("Style: Default")[1].splitlines()[0]   # white text


# --- the panel ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    load_fonts(app)
    return app


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    from app.config import settings

    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


def test_builtin_presets_apply_the_look_and_cannot_be_deleted(qapp, storage):
    from desktop.widgets.style_panel import BUILTIN_PRESETS, StylePanel

    sp = StylePanel()
    names = [sp.preset.itemText(i) for i in range(sp.preset.count())]
    for name in BUILTIN_PRESETS:
        assert name in names
    comic = next(n for n in BUILTIN_PRESETS if "Bangers" in n)
    sp.preset.setCurrentIndex(sp.preset.findText(comic))
    sp._on_preset(sp.preset.currentIndex())
    s = sp.get_style()
    assert sp.get_font() == "Bangers" and sp.caps.isChecked()
    assert s["uppercase"] is True and s["cover"] == "blur" and s["shadow"] == 2
    assert s["border_style"] == 1 and s["outline_w"] == 7
    sp._delete_preset()                                     # a built-in stays
    assert sp.preset.findText(comic) >= 0

    sp.caps.setChecked(False)                               # the owner's own tweak
    assert sp.get_style()["uppercase"] is False


def test_bundled_comic_faces_are_offered_first(qapp, storage):
    from desktop import fonts

    bundled, _user, system = fonts.families()
    assert {"Bangers", "Coiny"} <= set(bundled) and bundled[0] == "Be Vietnam Pro"
    assert "Bangers" not in system


# --- the clean 9:16 (the Chinese band cut off, the dialogue in the bar) ---------------------

def _region(job):
    import json as _json

    (job.work_dir / "hardsub_region.json").write_text(
        _json.dumps({"x": 0.25, "y": 0.8465, "w": 0.5, "h": 0.1376}), encoding="utf-8")


def test_a_short_cuts_the_chinese_band_off_instead_of_covering_it(job):
    """2026-10-31: an empty glass box in the middle of a Short looked dirty. With the dialogue
    in the bar the measured Chinese band is cut off the picture; nothing is covered."""
    from app.pipeline import assemble

    _region(job)
    assemble.assemble(job, [], frame="tiktok", cover_mode="glass", style={"glass": True},
                      canvas_text=True, bar_subs=True, safe_caption="tiktok")
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "crop=iw:trunc(ih*0.8365/2)*2:0:0[vcut]" in graph
    assert graph.index("[vcut]") < graph.index("[vbgfill]")         # the bars lose the band too
    assert "glass" not in graph and "boxblur=10:2" not in graph       # nothing left to cover
    canvas = (job.work_dir / "canvas.ass").read_text(encoding="utf-8")
    assert "CanvasSub" in canvas and ",,ta sai rồi" in canvas.split("[Events]")[1]
    assert assemble.band_cut({"x": 0, "y": 0.3, "w": 1, "h": 0.1}) is None   # not a bottom band
    assert assemble.band_cut(None) is None


def test_without_the_bar_the_picture_stays_whole(job):
    from app.pipeline import assemble

    _region(job)
    assemble.assemble(job, [], frame="tiktok", cover_mode="blur")
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[vcut]" not in graph and "boxblur=10:2" in graph


def test_a_short_renders_at_1080x1920(tmp_path, monkeypatch):
    import json as _json

    from app.config import settings
    from app.models import Job
    from app.pipeline import autopilot, series

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    d = tmp_path / "ep"
    d.mkdir()
    (d / "metadata.json").write_text(_json.dumps({"title": "《末日校车》03"}), encoding="utf-8")
    (d / "series_link.json").write_text(_json.dumps({"slug": "s", "episode": 3, "last": 3}),
                                        encoding="utf-8")
    p = series.Profile(slug="s", name="s", render={"res": "720", "frame": "source"})
    series.save(p)
    job = Job(id="ep", work_dir=d)
    assert autopilot.render_opts(job)["res"] == "720"                # the episode: as set
    assert autopilot.render_opts(job, "short")["res"] == "source"    # its Short: full 1080×1920
