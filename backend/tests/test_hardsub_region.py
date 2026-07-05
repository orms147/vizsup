"""ENG-5: locate the actual hardsub band (union of recognized-text boxes) and map
crop-local pixels → full-frame normalized coords, so the render's blur covers just
that band instead of the whole bottom-third. Pure geometry — no ffmpeg/OCR.
"""
from __future__ import annotations

from app.pipeline.assemble import _blur_band_filters
from app.pipeline.hardsub_detect import _poly_bbox, region_from_boxes


def test_poly_bbox_of_polygon():
    assert _poly_bbox([[10, 20], [50, 20], [50, 40], [10, 40]]) == (10.0, 20.0, 50.0, 40.0)
    assert _poly_bbox([[10, 40], [50, 20], [55, 45], [8, 42]]) == (8.0, 20.0, 55.0, 45.0)
    assert _poly_bbox("nonsense") is None
    assert _poly_bbox([]) is None


def test_region_maps_crop_pixels_to_full_frame():
    # crop image 100x50 → full frame is 100 wide, 150 tall (bottom-third = ih/3).
    # box (10,10)-(90,40): x 0.10..0.90; y 2/3 + (10..40)/50 * 1/3.
    r = region_from_boxes([(10, 10, 90, 40)], 100, 50, crop="bottom-third", pad=0.0)
    assert r["x"] == 0.1 and round(r["w"], 4) == 0.8
    assert round(r["y"], 4) == round(2 / 3 + (10 / 50) * (1 / 3), 4)
    assert round(r["y"] + r["h"], 4) == round(2 / 3 + (40 / 50) * (1 / 3), 4)


def test_region_is_union_of_all_boxes():
    r = region_from_boxes([(10, 12, 30, 40), (60, 6, 92, 46)], 100, 50,
                          crop="bottom-third", pad=0.0)
    assert r["x"] == 0.1 and round(r["x"] + r["w"], 4) == 0.92        # spans both boxes
    # top edge from the higher box (y=6), bottom edge from the lower box (y=46)
    assert round(r["y"], 4) == round(2 / 3 + (6 / 50) * (1 / 3), 4)


def test_region_padding_clamps_to_unit_square():
    r = region_from_boxes([(0, 0, 100, 50)], 100, 50, crop="bottom-third", pad=0.05)
    assert r["x"] == 0.0 and round(r["x"] + r["w"], 4) == 1.0         # clamped, no overflow


def test_region_none_when_empty_or_degenerate():
    assert region_from_boxes([], 100, 50) is None
    assert region_from_boxes([(1, 1, 9, 9)], 0, 50) is None


def test_blur_band_uses_region_then_falls_back():
    crop, overlay = _blur_band_filters({"x": 0.1, "y": 0.7, "w": 0.8, "h": 0.25})
    assert crop == "crop=iw*0.8000:ih*0.2500:iw*0.1000:ih*0.7000"
    assert overlay == "overlay=main_w*0.1000:main_h*0.7000"
    # None or a degenerate region → the byte-identical bottom-third fallback
    assert _blur_band_filters(None) == ("crop=iw:ih/3:0:ih*2/3", "overlay=0:main_h*2/3")
    assert _blur_band_filters({"x": 0, "y": 0, "w": 0, "h": 0})[0] == "crop=iw:ih/3:0:ih*2/3"
