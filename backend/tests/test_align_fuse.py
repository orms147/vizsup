"""N-7 (light): the E-2 fusion core — FunASR text snapped onto a word-timing
track. Synthetic streams, no models.
"""
from __future__ import annotations

from app.models import Cue
from app.pipeline.align import fuse_transcript_timing


def _s(i, text, start, end):
    return Cue(index=i, start=start, end=end, text=text)


def test_exact_match_snaps_to_word_times():
    sentences = [_s(1, "你好世界", 0.0, 5.0)]           # FunASR: coarse times
    words = [("你好", 1.2, 1.6), ("世界", 1.7, 2.3)]     # timing engine: precise
    (out,) = fuse_transcript_timing(sentences, words)
    assert (out.start, out.end) == (1.2, 2.3)
    assert out.text == "你好世界"                        # text NEVER changes


def test_partial_match_still_snaps():
    # timing engine misheard one hanzi (间 vs 界) → 3/4 anchors ≥ 50% → snap
    sentences = [_s(1, "你好世界", 0.0, 5.0)]
    words = [("你好", 1.0, 1.5), ("世间", 1.6, 2.0)]
    (out,) = fuse_transcript_timing(sentences, words)
    assert out.start == 1.0 and out.end == 2.0


def test_no_match_keeps_funasr_times():
    sentences = [_s(1, "你好世界", 3.0, 4.0)]
    words = [("完全", 0.0, 0.5), ("不同", 0.6, 1.0)]
    (out,) = fuse_transcript_timing(sentences, words)
    assert (out.start, out.end) == (3.0, 4.0)           # fusion never worse than FunASR


def test_two_sentences_consume_stream_monotonically():
    sentences = [_s(1, "你好", 0.0, 9.0), _s(2, "再见朋友", 0.0, 9.0)]
    words = [("你", 1.0, 1.2), ("好", 1.2, 1.4), ("再", 5.0, 5.2),
             ("见", 5.2, 5.4), ("朋友", 5.4, 6.0)]
    a, b = fuse_transcript_timing(sentences, words)
    assert (a.start, a.end) == (1.0, 1.4)
    assert (b.start, b.end) == (5.0, 6.0)
    assert b.start >= a.start                            # monotonic


def test_punctuation_ignored_in_anchoring():
    sentences = [_s(1, "你好，世界！", 0.0, 9.0)]
    words = [("你好", 2.0, 2.4), ("世界", 2.5, 3.0)]
    (out,) = fuse_transcript_timing(sentences, words)
    assert (out.start, out.end) == (2.0, 3.0)


def test_unmatched_middle_sentence_does_not_derail_later_ones():
    sentences = [_s(1, "你好", 0.0, 9.0), _s(2, "噪音字幕", 0.0, 9.0), _s(3, "再见", 0.0, 9.0)]
    words = [("你好", 1.0, 1.5), ("再见", 8.0, 8.5)]
    a, b, c = fuse_transcript_timing(sentences, words)
    assert (a.start, a.end) == (1.0, 1.5)
    # keeps its own FunASR times, except the start is clamped to stay monotonic
    assert (b.start, b.end) == (1.0, 9.0)
    assert (c.start, c.end) == (8.0, 8.5)               # stream position survived
