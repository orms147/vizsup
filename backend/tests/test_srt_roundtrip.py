"""N-2: lock the SRT/ASS time formatters and the file round-trip.

The old formatters rounded the fractional part separately, so 1.9995 emitted
'00:00:01,1000' (4-digit ms) which parsed back as 1.1s — a silent ~0.9s cue
shrink on every read/write cycle (editor autosave, TTS, assemble all round-trip
through files). These tests make that class of bug impossible to reintroduce.

Run from repo root or backend/:  pytest backend/tests/test_srt_roundtrip.py
"""
from __future__ import annotations

import random

from app.models import Cue
from app.pipeline.srt import _fmt_ass_time, _fmt_srt_time, parse_srt, write_srt


# --- formatter carry cases ----------------------------------------------------

def test_srt_ms_carry():
    assert _fmt_srt_time(1.9995) == "00:00:02,000"      # the original bug
    assert _fmt_srt_time(59.9995) == "00:01:00,000"     # carries into minutes
    assert _fmt_srt_time(3599.9995) == "01:00:00,000"   # carries into hours


def test_ass_cs_carry():
    assert _fmt_ass_time(1.9995) == "0:00:02.00"        # cs==100 carry
    assert _fmt_ass_time(59.999) == "0:01:00.00"


def test_plain_values_unchanged():
    # below the carry threshold the rewrite is byte-identical to the old output
    assert _fmt_srt_time(0.0) == "00:00:00,000"
    assert _fmt_srt_time(2.0) == "00:00:02,000"
    assert _fmt_srt_time(1.5) == "00:00:01,500"
    assert _fmt_srt_time(3661.25) == "01:01:01,250"
    assert _fmt_ass_time(6.0) == "0:00:06.00"
    assert _fmt_ass_time(3661.25) == "1:01:01.25"


def test_negative_clamps_to_zero():
    assert _fmt_srt_time(-3.2) == "00:00:00,000"
    assert _fmt_ass_time(-0.5) == "0:00:00.00"


# --- file round-trip ------------------------------------------------------------

def test_roundtrip_10k_random_times(tmp_path):
    rng = random.Random(20260702)
    times = sorted(rng.uniform(0, 7200) for _ in range(20_000))
    cues = [Cue(index=i + 1, start=times[2 * i], end=times[2 * i + 1], text=f"c{i}")
            for i in range(10_000)]
    p = tmp_path / "t.srt"
    write_srt(cues, p)
    back = parse_srt(p)
    assert len(back) == len(cues)
    for a, b in zip(cues, back):
        # formatter quantizes to 1ms; anything beyond that is corruption
        assert abs(a.start - b.start) <= 0.0005 + 1e-9, (a.index, a.start, b.start)
        assert abs(a.end - b.end) <= 0.0005 + 1e-9, (a.index, a.end, b.end)
        assert a.text == b.text


def test_parse_accepts_dot_millis_and_short_ms(tmp_path):
    # '.' separator (ASS/VTT-style) and short ms digits are tolerated on parse;
    # short ms are left-anchored: ',5' means 500ms (regex + ljust behavior).
    p = tmp_path / "t.srt"
    p.write_text("1\n00:00:01.500 --> 00:00:02,5\nxin chào\n", encoding="utf-8")
    (c,) = parse_srt(p)
    assert c.start == 1.5 and c.end == 2.5 and c.text == "xin chào"
