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
