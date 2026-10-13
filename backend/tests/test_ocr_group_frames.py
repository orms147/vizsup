"""ENG-4: the OCR frame-grouping state machine, extracted from extract_hardsubs
into a pure, testable group_frames(). Locks the behavior (grouping, group-break
start-time handling, min_dur filter, cleanest-read selection) and the new rep_frame
index that ocr_report.json exposes.
"""
from __future__ import annotations

from app.pipeline.ocr import _lines_with_boxes, group_frames


class _FakeArray:
    """Mimics a numpy ndarray: bool() raises (ambiguous truth), but it's iterable."""

    def __init__(self, items):
        self._items = items

    def __bool__(self):
        raise ValueError("The truth value of an array with more than one element is ambiguous")

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class _FakeOutput:
    def __init__(self, txts, scores, boxes):
        self.txts, self.scores, self.boxes = txts, scores, boxes


def test_lines_with_boxes_survives_ndarray_boxes():
    # RapidOCROutput.boxes is a numpy ndarray — `boxes or []` would raise ValueError.
    out = _FakeOutput(("你好", "世界"), (0.9, 0.8),
                      _FakeArray([[[0, 0], [1, 0], [1, 1], [0, 1]],
                                  [[2, 2], [3, 2], [3, 3], [2, 3]]]))
    rows = _lines_with_boxes(out)                       # must NOT raise
    assert [t for t, _, _ in rows] == ["你好", "世界"]
    assert rows[0][2] == [[0, 0], [1, 0], [1, 1], [0, 1]]


def test_lines_with_boxes_blank_frame():
    assert _lines_with_boxes(_FakeOutput((), (), _FakeArray([]))) == []
    assert _lines_with_boxes(None) == []


def _f(*rows):
    """rows: (t, text, conf) tuples."""
    return list(rows)


def test_consecutive_identical_frames_become_one_cue():
    out = group_frames(_f((0.0, "你好", 0.9), (0.5, "你好", 0.9), (1.0, "你好", 0.9)),
                       sim=0.5, fps=2.0, min_dur=0.4)
    assert len(out) == 1
    cue, conf, rep = out[0]
    assert cue.text == "你好" and cue.start == 0.0 and cue.end == 1.5   # final flush = n/fps
    assert conf == 0.9 and rep == 0


def test_different_subtitle_splits():
    out = group_frames(_f((0.0, "你好世界", 0.9), (0.5, "完全不同一句", 0.9)),
                       sim=0.5, fps=2.0, min_dur=0.4)
    assert [c.text for c, _, _ in out] == ["你好世界", "完全不同一句"]
    assert [(c.start, c.end) for c, _, _ in out] == [(0.0, 0.5), (0.5, 1.0)]


def test_empty_frame_closes_group():
    out = group_frames(_f((0.0, "你好", 0.9), (0.5, "", 1.0), (1.0, "你好", 0.9)),
                       sim=0.5, fps=2.0, min_dur=0.4)
    assert [(c.start, c.end) for c, _, _ in out] == [(0.0, 0.5), (1.0, 1.5)]


def test_min_dur_filter_drops_short_cues():
    out = group_frames(_f((0.0, "你好", 0.9), (0.1, "完全不同", 0.9)),
                       sim=0.5, fps=10.0, min_dur=0.4)
    assert out == []                                   # both cues are 0.1 s < 0.4 s


def test_cleanest_read_and_rep_frame_index():
    # 你好 is contained in 你好世界, so all three frames group; the read with the most
    # CJK wins, and rep points at the frame that produced it.
    out = group_frames(_f((0.0, "你好", 0.8), (0.5, "你好世界", 0.9), (1.0, "你好", 0.7)),
                       sim=0.5, fps=2.0, min_dur=0.4)
    assert len(out) == 1
    cue, conf, rep = out[0]
    assert cue.text == "你好世界"                        # most Chinese chars
    assert conf == 0.9 and rep == 1                     # confidence + frame of the chosen read


def test_empty_input_is_empty():
    assert group_frames([], sim=0.5, fps=2.0, min_dur=0.4) == []


# --- E-0: static overlays / watermarks / blips -------------------------------------

from app.models import Cue  # noqa: E402
from app.pipeline.ocr import drop_overlays  # noqa: E402


def _c(i, s, e, t):
    return Cue(i, s, e, t)


def test_a_flickering_banner_is_dropped_but_real_lines_stay():
    banner = ["School life Welcome to my channel", "School life Welcome to channel muy",
              "Welcome channel to my", "life Welcome channel to my",
              "My School life Welcome to my channel", "School life Welcome to my channe"]
    cues = [_c(k + 1, k * 0.5, k * 0.5 + 0.5, t) for k, t in enumerate(banner)]
    cues += [_c(7, 4.0, 6.0, "小姐姐你知道读哪里了吗"), _c(8, 6.0, 8.0, "从我的大脑皮层划过")]
    kept, dropped = drop_overlays(cues)
    assert [c.text for c in kept] == ["小姐姐你知道读哪里了吗", "从我的大脑皮层划过"]
    assert [c.index for c in kept] == [1, 2]
    assert {d["why"] for d in dropped} == {"overlay"} and len(dropped) == 6


def test_a_repeated_chorus_is_not_an_overlay():
    line = "我说再喝一碗我熬的茶汤"
    cues = [_c(k + 1, k * 20.0, k * 20.0 + 4.0, line) for k in range(6)]
    kept, dropped = drop_overlays(cues)
    assert len(kept) == 6 and dropped == []


def test_watermarks_and_single_frame_latin_blips_are_dropped():
    cues = [_c(1, 0.0, 0.5, "CAS"), _c(2, 1.0, 2.0, "豆包AI生成"), _c(3, 2.0, 3.0, "@小王同学"),
            _c(4, 3.0, 3.5, "2900元"), _c(5, 4.0, 6.0, "Good morning everyone"),
            _c(6, 6.0, 8.0, "早上新学期过了两个月")]
    kept, dropped = drop_overlays(cues)
    assert [c.text for c in kept] == ["2900元", "Good morning everyone", "早上新学期过了两个月"]
    assert sorted(d["why"] for d in dropped) == ["blip", "watermark", "watermark"]
