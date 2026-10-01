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


# --- align_text_to_words: global alignment for text-only cloud engines ---------

from app.pipeline.align import align_text_to_words  # noqa: E402


def _words(text: str, start: float, step: float = 0.25):
    """One timing word per hanzi, `step` seconds apart."""
    return [(ch, start + i * step, start + (i + 1) * step) for i, ch in enumerate(text)]


def test_exact_text_takes_word_times():
    w = _words("大家好欢迎来到我的频道", 1.0)
    cues = align_text_to_words(["大家好，", "欢迎来到我的频道。"], w, (0.0, 10.0))
    assert (cues[0].start, cues[0].end) == (1.0, 1.75)
    assert (cues[1].start, cues[1].end) == (1.75, 3.75)


def test_one_missing_sentence_does_not_drag_the_rest_out_of_sync():
    """The failure seen on a real vlog: whisper heard nothing for a stretch of music
    the LLM transcribed; with per-sentence windows everything after slid 13 s off."""
    w = _words("今天去上学", 0.0) + _words("然后我们去便利店买了柠檬水", 40.0)
    cues = align_text_to_words(
        ["今天去上学。", "这一段耳语谁也没听清楚呢真的很长很长很长的一句话。", "然后我们去便利店买了柠檬水。"],
        w, (0.0, 60.0))
    last = cues[-1]
    assert abs(last.start - 40.0) < 0.01 and abs(last.end - 43.25) < 0.01
    mid = cues[1]                                   # interpolated BETWEEN its anchors
    assert 1.25 <= mid.start <= mid.end <= 40.0


def test_garbled_whisper_text_still_anchors_on_the_matching_runs():
    w = _words("哈喽闺米们现在是韩国时间凌晨四点", 5.0)   # 蜜→米 misheard
    cues = align_text_to_words(["哈喽闺蜜们，", "现在是韩国时间凌晨4点。"], w, (0.0, 20.0))
    assert abs(cues[0].start - 5.0) < 0.01
    assert abs(cues[1].start - 6.25) < 0.3


def test_no_anchors_spreads_over_the_span():
    cues = align_text_to_words(["你好。", "再见。"], [], (10.0, 14.0))
    assert [(c.start, c.end) for c in cues] == [(10.0, 12.0), (12.0, 14.0)]


def test_cues_are_monotonic_and_never_overlap():
    w = _words("一二三四五六七八九十", 0.0)
    cues = align_text_to_words(["一二三", "四五六", "七八九十"], w, (0.0, 5.0))
    for a, b in zip(cues, cues[1:]):
        assert a.start <= b.start and a.end <= b.start + 1e-9
    assert all(c.end - c.start >= 0.3 - 1e-9 for c in cues)


def test_lone_common_characters_are_not_trusted_as_anchors():
    """的/了/我 appear everywhere; a single-char match far away must not pin a cue."""
    w = _words("我", 50.0)
    cues = align_text_to_words(["我今天很开心。"], w, (0.0, 10.0))
    assert cues[0].end <= 10.0                    # spread inside the span, not pulled to 50 s
