"""2026-11-02: the owner marks logo regions before OCR (OCR skips them), covers any region of the
picture with their own text at render time, and "AI soát bản dịch" is off by default."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models import Cue, Job  # noqa: E402
from app.pipeline import assemble, disclosure, ocr, ocr_area  # noqa: E402
from app.pipeline.srt import write_ass  # noqa: E402


# --- logo regions (backend) --------------------------------------------------------------

def test_clean_logos_drops_slivers_and_caps_the_count():
    rects = [{"x": 0.8, "y": 0.9, "w": 0.15, "h": 0.08}, {"x": 0.1, "y": 0.1, "w": 0.001, "h": 0.5},
             "junk", None] + [{"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.2}] * 20
    out = ocr_area.clean_logos(rects)
    assert len(out) == ocr_area.MAX_LOGOS and out[0] == {"x": 0.8, "y": 0.9, "w": 0.15, "h": 0.08}


def test_blank_logos_paints_only_the_part_inside_the_strip():
    strip = np.full((90, 200, 3), 255, np.uint8)              # the bottom third of the frame
    box = ocr.strip_box("bottom-third", None)
    logo = {"x": 0.75, "y": 0.8, "w": 0.25, "h": 0.1}         # inside it (y 0.8–0.9 of the frame)
    out = ocr_area.blank_logos(strip, [logo], box)
    assert out is not strip and strip.min() == 255            # the input is never touched
    assert out[:, :140].min() == 255 and out[:, 150:, :].min() == 0
    assert int((out == 0).all(axis=2).sum()) > 0
    above = {"x": 0.75, "y": 0.1, "w": 0.2, "h": 0.1}         # outside the strip: nothing to paint
    assert ocr_area.blank_logos(strip, [above], box) is strip
    assert ocr_area.blank_logos(strip, [], box) is strip


def test_the_owners_band_is_the_strip_when_drawn():
    area = {"x": 0.2, "y": 0.8, "w": 0.7, "h": 0.15}
    assert ocr.strip_box("bottom-third", area) == (0.2, 0.8, 0.7, 0.15)
    assert ocr.strip_box("full", None) == (0, 0, 1, 1)


def test_other_logo_regions_mean_another_checkpoint(tmp_path):
    src = tmp_path / "s.mp4"
    src.write_bytes(b"v")
    base = ocr._checkpoint_key(src, 2.0, "vf", 0.5)
    assert ocr._checkpoint_key(src, 2.0, "vf", 0.5, logos=[]) == base       # old checkpoints stay valid
    a = ocr._checkpoint_key(src, 2.0, "vf", 0.5, logos=[{"x": 0.8, "y": 0.9, "w": 0.1, "h": 0.05}])
    assert a != base


def test_logos_belong_to_the_project_then_to_its_series(tmp_path, monkeypatch):
    from app.pipeline import series

    job = Job(id="j", work_dir=tmp_path / "j")
    prof = series.Profile(slug="s", name="S", ocr_logos=[{"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.1}])
    monkeypatch.setattr(series, "profile_of", lambda j: prof)
    saved = []
    monkeypatch.setattr(series, "save", lambda p: saved.append(list(p.ocr_logos)))
    assert ocr_area.logos_for_job(job) == [{"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.1}]   # inherited
    ocr_area.remember_logos(job, [{"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1}])
    assert ocr_area.logos_for_job(job)[0]["x"] == 0.7 and saved[-1][0]["x"] == 0.7     # own, and kept for the series
    ocr_area.remember_logos(job, [], series_too=False)                                   # "no logo" is an answer too
    assert ocr_area.logos_for_job(job) == []


def test_a_series_profile_round_trips_its_logos():
    from app.pipeline import series

    p = series.Profile(slug="s", name="S", ocr_logos=[{"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}])
    back = series._from_dict("s", p.to_dict())
    assert back.ocr_logos == p.ocr_logos
    assert series._from_dict("s", {"name": "S"}).ocr_logos == []


def test_extract_hardsubs_hands_the_ocr_blanked_frames(tmp_path, monkeypatch):
    """The frames given to the OCR engine have the logo region black."""
    import types

    job = Job(id="j", work_dir=tmp_path / "j")
    job.source_video.write_bytes(b"v")
    seen = []

    class Prov:
        display_name, local, crop_to_band, batch_frames = "fake", False, False, 4

        def available(self):
            return True

        def cache_tag(self):
            return "rapidocr"

        def reader(self, **_k):
            import contextlib

            @contextlib.contextmanager
            def cm():
                def read(frames):
                    seen.extend(frames)
                    return [[("你好", 0.99)] for _ in frames]
                yield read
            return cm()

    monkeypatch.setattr("app.providers.registry.get_ocr", lambda e: Prov())
    monkeypatch.setattr("app.pipeline.assemble.probe_duration", lambda p: 2.0)
    monkeypatch.setattr(ocr, "_frame_stream",
                        lambda *a, **k: iter([np.full((30, 100, 3), 200, np.uint8) for _ in range(4)]))
    monkeypatch.setattr(ocr, "_ensure_region", lambda *a, **k: None)
    monkeypatch.setattr("app.heavy.heavy_task", lambda *_a: types.SimpleNamespace(
        __enter__=lambda s: None, __exit__=lambda s, *a: None))
    ocr.extract_hardsubs(job, crop="bottom-third", engine="rapidocr",
                         logos=[{"x": 0.5, "y": 0.7, "w": 0.5, "h": 0.3}])
    assert len(seen) == 4
    assert seen[0][:, :50].min() == 200 and seen[0][5:, 50:].max() == 0


# --- the cover box ("Che logo, ghi tên phim") --------------------------------------------------

BOX = {"x": 0.78, "y": 0.9, "w": 0.2, "h": 0.08}


def test_cover_events_are_in_canvas_pixels_and_fit_the_text():
    ev = disclosure.cover_events({"on": True, "text": "Chuyến Xe Tận Thế", "box": BOX,
                                  "fill": "#102030", "opacity": 90, "color": "#FFFFFF"}, 120)
    assert len(ev) == 1
    e = ev[0]
    assert (e["x0"], e["y0"], e["x1"], e["y1"]) == (1498, 972, 1882, 1058)
    assert e["end"] == 120 and e["fill"] == "102030" and e["alpha"] == 26
    assert e["tx"] == 1690 and e["ty"] == 1015
    assert 14 <= e["fs"] <= 86 * 0.82 / 1.2 + 1                # never taller than the box
    assert disclosure.cover_font_px(400, 100, "x" * 80) < disclosure.cover_font_px(400, 100, "x" * 8)


def test_cover_is_off_without_the_switch_a_box_or_text():
    assert disclosure.cover_events(None, 10) == []
    assert disclosure.cover_events({"on": False, "text": "x", "box": BOX}, 10) == []
    assert disclosure.cover_events({"on": True, "text": "x", "box": None}, 10) == []
    assert disclosure.cover_events({"on": True, "text": "x", "box": {"x": 0.1, "y": 0.1, "w": 0.0, "h": 0.1}}, 10) == []
    only_box = disclosure.cover_events({"on": True, "text": "", "box": BOX}, 10)
    assert len(only_box) == 1 and only_box[0]["text"] == ""    # a plain cover, no text, is allowed


def test_write_ass_draws_the_box_then_the_text_on_top(tmp_path):
    ev = disclosure.cover_events({"on": True, "text": "Tên phim", "box": BOX, "fill": "#FF0000",
                                 "opacity": 100, "color": "#00FF00"}, 60)
    p = write_ass([Cue(index=1, start=0, end=2, text="xin chào")], tmp_path / "a.ass", cover=ev)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert any(ln.startswith("Style: Cover,") for ln in lines)
    box = next(ln for ln in lines if ",Cover,,0,0,0,,{\\an7" in ln)
    txt = next(ln for ln in lines if ",Cover,,0,0,0,,{\\an5" in ln)
    assert "\\1c&H0000FF&" in box and "\\p1" in box and "m 1498 972 l 1882 972" in box   # ASS is BGR
    assert box.startswith("Dialogue: 3,") and txt.startswith("Dialogue: 4,")             # text above the box
    assert "\\1c&H00FF00&" in txt and txt.endswith("Tên phim")
    plain = write_ass([Cue(index=1, start=0, end=2, text="x")], tmp_path / "b.ass").read_text(encoding="utf-8")
    assert "Cover" not in plain                                                           # nothing without it


def test_render_hands_the_cover_to_assemble(monkeypatch, tmp_path):
    job = Job(id="r", work_dir=tmp_path / "r")
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    job.source_video.write_bytes(b"v")
    segs = [(Cue(index=1, start=0, end=1, text="x"), job.tts_dir / "seg_0001.mp3")]
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 1.2)
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(assemble, "probe_size", lambda p: (1920, 1080))
    monkeypatch.setattr(assemble, "_render_dub", lambda *a, **k: "dub/list.txt")
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda *a, **k: None)
    cover = {"on": True, "text": "Tên phim", "box": BOX}
    assemble.assemble(job, segs, cover=cover)
    ass = (job.work_dir / assemble._ass_name(False)).read_text(encoding="utf-8")
    assert "Tên phim" in ass and ",Cover,," in ass


# --- the screens -------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_render_page_sends_the_cover_only_with_a_box(qapp):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    got: list[dict] = []
    rv.renderRequested.connect(got.append)
    rv._emit_render()
    assert got[-1]["logo_cover"] is None                     # off by default
    rv.cover_cb.setChecked(True)
    rv.cover_text.setText("Chuyến Xe Tận Thế")
    rv._emit_render()
    assert got[-1]["logo_cover"] is None                     # no box yet: nothing to burn
    rv._cover_box = BOX
    rv._on_cover()
    rv._emit_render()
    cv = got[-1]["logo_cover"]
    assert cv["text"] == "Chuyến Xe Tận Thế" and cv["box"] == BOX and cv["on"]
    assert RenderView().cover_text.text() == "Chuyến Xe Tận Thế"     # remembered for the next video


def test_area_dialog_draws_several_logo_regions_and_returns_them(qapp, tmp_path):
    from PySide6.QtCore import QPointF

    from desktop.widgets.area_dialog import AreaDialog

    dlg = AreaDialog([], None, "Phim", None)
    pk = dlg.picker
    pk.resize(800, 450)
    dlg._set_mode("logo")
    for a, b in (((0.1, 0.1), (0.3, 0.2)), ((0.7, 0.8), (0.95, 0.95))):
        v = pk._view()
        def at(p):
            return QPointF(v.x() + p[0] * v.width(), v.y() + p[1] * v.height())

        class Ev:
            def __init__(self, p):
                self._p = p

            def position(self):
                return self._p

        pk.mousePressEvent(Ev(at(a)))
        pk.mouseMoveEvent(Ev(at(b)))
        pk.mouseReleaseEvent(Ev(at(b)))
    assert len(pk.logos) == 2
    pk.mousePressEvent(Ev(at((0.5, 0.5))))                   # a bare click draws nothing
    pk.mouseReleaseEvent(Ev(at((0.5, 0.5))))
    assert len(pk.logos) == 2
    pk.logo_sel = 0
    pk.remove_logo()
    assert len(pk.logos) == 1
    dlg.picker.rect = None
    dlg.accept()
    assert dlg.result_logos() == [{"x": pytest.approx(0.7, abs=0.01), "y": pytest.approx(0.8, abs=0.01),
                                   "w": pytest.approx(0.25, abs=0.01), "h": pytest.approx(0.15, abs=0.01)}]


# --- AI review is off by default -----------------------------------------------------------------

def test_ai_review_is_off_by_default_everywhere(qapp, tmp_path, monkeypatch):
    from app import estimate
    from app.config import settings
    from app.providers.registry import list_providers
    from desktop.views.input_view import InputView

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    assert not InputView(list_providers()).ai_review.isChecked()
    base = {"subtitle_source": "asr", "asr": "audio_llm", "translator": "openrouter", "refine": "openrouter"}
    off = estimate.estimate(600.0, base)
    on = estimate.estimate(600.0, {**base, "ai_review": True})
    assert on.cost[1] > off.cost[1]                          # only a review the owner asked for is priced


def test_a_saved_old_default_is_switched_off_once(tmp_path, monkeypatch):
    from app.config import settings
    from desktop import store

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    (tmp_path / "ui_state.json").write_text(json.dumps({"ai_review": True, "translator": "x"}),
                                            encoding="utf-8")
    st = store.load_ui_state()
    assert st["ai_review"] is False and st["translator"] == "x"
    store.save_ui_state({"ai_review": True})                 # the owner turns it on again…
    assert store.load_ui_state()["ai_review"] is True        # …and it sticks


def test_cli_runs_the_ai_review_only_when_asked():
    from app import cli

    p = cli.build_parser()
    assert not p.parse_args(["run", "u"]).qa
    assert p.parse_args(["run", "u", "--qa"]).qa
    opts = cli.autopilot_opts({}, {})
    assert opts["ai_review"] is False
    a = cli.autopilot_args("u", "s", {**opts, "ai_review": True}, Path("."))
    assert a.qa and not a.no_qa
