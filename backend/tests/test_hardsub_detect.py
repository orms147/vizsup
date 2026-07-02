"""Headless checks for the E-1 auto hardsub-detection decision logic.

The ffmpeg/OCR I/O in detect_hardsubs can't run in CI without media + models,
so the routing decision (`_decide`) and the frame-sampling plan (`_sample_times`)
are pure functions tested here. Run from backend/:  pytest tests/test_hardsub_detect.py
"""
from __future__ import annotations

from app.pipeline.hardsub_detect import _decide, _sample_times


def test_decide_empty_is_asr():
    assert _decide([]) is False


def test_decide_hardsubbed_video():
    # subs on screen most of the time speech happens
    assert _decide([True] * 10 + [False] * 6) is True


def test_decide_clean_video():
    assert _decide([False] * 16) is False


def test_decide_min_hits_floor():
    # 2 lucky misreads in a short sample must NOT flip to OCR (needs >= 3 hits)
    assert _decide([True, True, False, False]) is False
    assert _decide([True, True, True, False]) is True


def test_decide_ratio_floor():
    # 3 stray scene-text frames out of 16 (~19%) stays ASR (< 25%)
    assert _decide([True] * 3 + [False] * 13) is False
    # 4/16 = 25% crosses the ratio floor
    assert _decide([True] * 4 + [False] * 12) is True


def test_sample_times_middle_80_percent():
    ts = _sample_times(100.0, 16)
    assert len(ts) == 16
    assert ts[0] == 10.0 and ts[-1] == 90.0          # skips intro/outro 10%
    assert all(b > a for a, b in zip(ts, ts[1:]))    # strictly increasing


def test_sample_times_unknown_duration_falls_back():
    assert _sample_times(0.0, 4) == [1.0, 2.0, 3.0, 4.0]


def test_sample_times_edge_counts():
    assert _sample_times(100.0, 0) == []
    assert _sample_times(100.0, 1) == [50.0]         # single sample → midpoint
