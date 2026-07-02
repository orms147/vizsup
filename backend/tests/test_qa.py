"""N-2: the QA metric primitives (app/qa.py) every eval script builds on.

Run from repo root or backend/:  pytest backend/tests/test_qa.py
"""
from __future__ import annotations

from app.models import Cue
from app.qa import cer, srt_text_cer, timing_stats


# --- cer ------------------------------------------------------------------------

def test_cer_identical_is_zero():
    assert cer("你好世界", "你好世界") == 0.0


def test_cer_one_sub_in_three():
    assert cer("abd", "abc") == 1 / 3


def test_cer_whitespace_insensitive():
    assert cer("你好 世界", "你好世界") == 0.0
    assert cer("xin chào", "xinchào") == 0.0


def test_cer_empty_edges():
    assert cer("", "") == 0.0
    assert cer("", "abc") == 1.0        # everything deleted
    assert cer("abc", "") == 1.0        # hallucinated content vs empty ref


def test_cer_insert_delete():
    assert cer("abcd", "abc") == 1 / 3  # one insertion
    assert cer("ab", "abc") == 1 / 3    # one deletion


def test_srt_text_cer(tmp_path):
    hyp, ref = tmp_path / "h.srt", tmp_path / "r.srt"
    hyp.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好世界\n", encoding="utf-8")
    ref.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好世间\n", encoding="utf-8")
    assert srt_text_cer(hyp, ref) == 0.25  # 1 of 4 chars wrong


# --- timing_stats -----------------------------------------------------------------

def _c(i, s, e):
    return Cue(index=i, start=s, end=e, text=f"c{i}")


def test_timing_exact_match():
    ref = [_c(1, 0.0, 2.0), _c(2, 3.0, 5.0)]
    st = timing_stats(ref, ref)
    assert st["matched"] == 2 and st["ref_total"] == 2
    assert st["d_start"]["mean"] == 0.0 and st["d_end"]["p95"] == 0.0


def test_timing_known_offsets():
    ref = [_c(1, 0.0, 2.0), _c(2, 3.0, 5.0)]
    hyp = [_c(1, 0.2, 2.1), _c(2, 3.4, 5.0)]
    st = timing_stats(hyp, ref)
    assert st["matched"] == 2
    assert abs(st["d_start"]["mean"] - 0.3) < 1e-9   # (0.2 + 0.4) / 2
    assert abs(st["d_end"]["mean"] - 0.05) < 1e-9    # (0.1 + 0.0) / 2


def test_timing_unmatched_ref_counted_without_deltas():
    ref = [_c(1, 0.0, 2.0), _c(2, 10.0, 12.0)]      # second ref has no overlap
    hyp = [_c(1, 0.0, 2.0)]
    st = timing_stats(hyp, ref)
    assert st["matched"] == 1 and st["ref_total"] == 2
    assert st["d_start"]["n"] == 1


def test_timing_picks_max_overlap():
    ref = [_c(1, 0.0, 4.0)]
    hyp = [_c(1, 0.0, 1.0), _c(2, 0.9, 4.1)]         # second overlaps more
    st = timing_stats(hyp, ref)
    assert abs(st["d_start"]["mean"] - 0.9) < 1e-9   # matched to hyp[1]


def test_timing_empty_inputs():
    st = timing_stats([], [])
    assert st == {"matched": 0, "ref_total": 0,
                  "d_start": {"n": 0, "mean": None, "median": None, "p95": None},
                  "d_end": {"n": 0, "mean": None, "median": None, "p95": None}}
