"""Platform safe zones (wave 3, item 1): one table of the strips each platform's UI covers, drawn
translucent on the video, an amber hint when the subtitle box sits in one, the 9:16 composition
for "Dọc 9:16", the choice remembered, and the render page keeping the preview's frame in step."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


# --- the table ----------------------------------------------------------------------

def test_table_and_zones():
    from desktop.widgets import safe_zones as sz

    assert set(sz.SAFE_ZONES) == {"tiktok", "reels", "shorts", "youtube"}
    tk = {z.name: z for z in sz.zones("tiktok")}
    assert (tk["top"].y1, tk["bottom"].y0) == (0.08, 0.78)               # top 8 %, bottom 22 %
    assert tk["right"].x0 == pytest.approx(0.86) and tk["right"].y0 == 0.45  # 14 % from 45 % down
    assert tk["right"].y1 == 0.78                                         # stops at the caption strip
    yt = sz.zones("youtube")
    assert [z.name for z in yt] == ["bottom"] and yt[0].y0 == pytest.approx(0.90)
    assert sz.zones(None) == [] and sz.zones("nope") == []


def test_hint_names_the_thing_that_covers_the_box():
    from desktop.widgets import safe_zones as sz

    assert sz.hint_for("tiktok", (0.1, 0.30, 0.8, 0.40)) == ""                        # middle: clear
    assert sz.hint_for("tiktok", (0.1, 0.60, 0.95, 0.66)) == "Phụ đề nằm dưới nút của TikTok"
    assert sz.hint_for("tiktok", (0.1, 0.85, 0.8, 0.95)) == "Phụ đề nằm dưới tên và mô tả của TikTok"
    assert sz.hint_for("tiktok", (0.1, 0.02, 0.8, 0.06)) == "Phụ đề nằm dưới thanh tab của TikTok"
    assert "thanh điều khiển của YouTube" in sz.hint_for("youtube", (0.1, 0.92, 0.9, 0.98))
    assert sz.hint_for(None, (0, 0, 1, 1)) == ""


# --- the canvas ---------------------------------------------------------------------

def _canvas(qapp, w=300, h=540, size=(540, 960), margin_v=60):
    from PySide6.QtGui import QColor, QImage

    from desktop.widgets.video_canvas import VideoCanvas

    c = VideoCanvas()
    c.resize(w, h)
    img = QImage(size[0], size[1], QImage.Format_RGB32)
    img.fill(QColor("#3a6ea5"))
    c.set_frame(img, *size)
    c.set_style({"margin_l": 100, "margin_r": 100, "margin_v": margin_v}, 20, "Be Vietnam Pro")
    c.set_caption("Xin chào")
    c.show()
    qapp.processEvents()
    return c


def test_default_subtitle_position_is_under_tiktok_ui_and_moving_it_clears_the_hint(qapp):
    c = _canvas(qapp)                                   # margin_v 60 → the very bottom
    assert c.safe_zone_hint() == ""                     # no zone chosen: no hint
    c.set_safe_zone("tiktok")
    assert c.safe_zone_hint() == "Phụ đề nằm dưới tên và mô tả của TikTok"
    c.set_style({"margin_l": 100, "margin_r": 100, "margin_v": 560}, 20, "Be Vietnam Pro")
    assert c.safe_zone_hint() == "Phụ đề nằm dưới nút của TikTok"     # mid-height, but under the buttons
    c.set_style({"margin_l": 100, "margin_r": 300, "margin_v": 560}, 20, "Be Vietnam Pro")
    assert c.safe_zone_hint() == ""                     # narrower, left of the button column
    c.set_safe_zone("youtube")                          # landscape bottom strip only
    assert c.safe_zone_hint() == ""
    c.set_safe_zone(None)
    assert c.safe_zone_hint() == "" and c.safe_zone() is None


def test_zone_is_painted_translucent_over_the_picture(qapp):
    c = _canvas(qapp)
    before = c.grab().toImage()
    c.set_safe_zone("tiktok")
    after = c.grab().toImage()
    d = c._disp
    x = int(d.x() + d.width() * 0.02)                    # left of the caption, on the picture
    y_bottom, y_mid = int(d.y() + d.height() * 0.9), int(d.y() + d.height() * 0.3)
    b, a = before.pixelColor(x, y_bottom), after.pixelColor(x, y_bottom)
    assert (a.red(), a.green(), a.blue()) != (b.red(), b.green(), b.blue())   # tinted
    assert a.red() > b.red()                                                  # towards red
    assert after.pixelColor(x, y_mid) == before.pixelColor(x, y_mid)           # clear area untouched


def test_nine_sixteen_composition_around_a_landscape_clip(qapp):
    c = _canvas(qapp, w=400, h=700, size=(1920, 1080))
    assert abs(c._disp.width() / c._disp.height() - 16 / 9) < 0.02           # the source's own frame
    c.set_frame_aspect(9 / 16)
    c.grab()
    d, pic = c._disp, c._pic
    assert abs(d.width() / d.height() - 9 / 16) < 0.02                        # the 9:16 canvas
    assert abs(pic.width() / pic.height() - 16 / 9) < 0.02                    # picture inside it
    assert d.contains(pic.center()) and pic.width() <= d.width() + 0.5
    c.set_safe_zone("tiktok")
    c.set_style({"margin_l": 100, "margin_r": 100, "margin_v": 60}, 20, "Be Vietnam Pro")
    assert "TikTok" in c.safe_zone_hint()                                     # judged on the 9:16 frame
    c.set_frame_aspect(None)
    c.grab()
    assert abs(c._disp.width() / c._disp.height() - 16 / 9) < 0.02


# --- the panel + render page -------------------------------------------------------------

def test_choice_is_remembered_and_reaches_the_canvas(qapp, storage):
    from desktop import store
    from desktop.widgets.video_panel import VideoPanel

    p = VideoPanel("Video nguồn")
    assert p.safe_zone() == "" and not p._zone_btn.isChecked()
    p._zone_actions["reels"].trigger()
    assert p.safe_zone() == "reels" and p._canvas.safe_zone() == "reels" and p._zone_btn.isChecked()
    assert store.load_ui_state()["safe_zone"] == "reels"
    other = VideoPanel("Video nguồn")                          # the next editor / render page
    assert other.safe_zone() == "reels" and other._canvas.safe_zone() == "reels"
    other._zone_actions[""].trigger()
    assert other.safe_zone() == "" and other._canvas.safe_zone() is None
    assert store.load_ui_state()["safe_zone"] == ""


def test_render_page_keeps_the_preview_frame_in_step_with_the_frame_option(qapp, storage):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    assert rv.preview._canvas._comp_aspect is None
    rv.frame.setCurrentIndex(rv.frame.findData("tiktok"))
    assert rv.preview._canvas._comp_aspect == pytest.approx(9 / 16)
    rv.frame.setCurrentIndex(rv.frame.findData("source"))
    assert rv.preview._canvas._comp_aspect is None
