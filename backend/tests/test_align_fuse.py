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


# --- E-1: sentences Whisper heard no words for (music) ------------------------------

def test_unheard_tail_sentences_are_not_stacked_on_the_chunk_end():
    """Regression (job 2dc712c162d7, 2026-10-13): 7 tail sentences after the last heard
    word were each clamped to the 240 s chunk end → all at 240.00–240.30."""
    w = _words("我说再喝一碗", 200.0)
    tail = ["你何时回来喝茶。", "这次我会多放老姜。", "信一直搁在桌上。", "想问你药方。"]
    cues = align_text_to_words(["我说再喝一碗。", *tail], w, (0.0, 240.0))
    starts = [c.start for c in cues]
    assert len(set(starts)) == len(starts)                 # nobody shares a start
    assert all(c.end <= 240.0 for c in cues)
    assert [c.guess for c in cues] == [False, True, True, True, True]


def test_unheard_sentences_are_laid_over_the_voice():
    w = _words("我说再喝一碗", 200.0)
    voice = [(205.0, 215.0), (220.0, 235.0)]
    cues = align_text_to_words(["我说再喝一碗。", "你何时回来喝茶。", "这次我会多放老姜。"],
                               w, (0.0, 240.0), voice=voice)
    for c in cues[1:]:
        assert 205.0 <= c.start < c.end <= 235.0
        assert any(s <= c.start < e for s, e in voice)     # starts on voice, not in silence


def test_unheard_head_sentences_end_where_the_heard_ones_begin():
    w = _words("然后我们去便利店", 30.0)
    cues = align_text_to_words(["开头没听清楚的一句。", "然后我们去便利店。"], w, (0.0, 60.0))
    head, heard = cues
    assert head.guess and not heard.guess
    assert head.end <= heard.start + 1e-9 and heard.start - head.start < 5.0


def test_a_sentence_anchored_across_two_repeats_keeps_its_densest_run():
    """Repeated chorus: the same line heard at 10 s and again at 70 s must not become a
    60 s cue (job 2dc712c162d7 had a 76 s one)."""
    w = _words("你寄来的信", 10.0) + _words("的信一直搁在桌上", 70.0)
    cues = align_text_to_words(["你寄来的信一直搁在桌上。"], w, (0.0, 100.0))
    c = cues[0]
    assert c.end - c.start < 8.0 and c.guess


def test_a_repeated_chorus_is_paired_in_order():
    """difflib paired the 2nd copy of a chorus line with the 1st copy's words (from the
    previous chunk's margin) and left the lines before it with no anchors at all."""
    w = (_words("你何时回来", 236.0)                      # previous chunk's copy (margin)
         + _words("我说再喝一碗", 250.0) + _words("你说你要渡江", 260.0)
         + _words("你何时回来", 270.0))
    cues = align_text_to_words(["我说再喝一碗。", "你说你要渡江。", "你何时回来？"],
                               w, (240.0, 300.0))
    assert [round(c.start) for c in cues] == [250, 260, 270]
    assert not any(c.guess for c in cues)


def test_collapsed_whisper_timestamps_are_not_trusted():
    """Whisper stamped a whole sung phrase with one time (19 words at 136.24 s)."""
    w = _words("开头的一句", 10.0) + [(ch, 50.0, 50.0) for ch in "这次我会多放老姜"] \
        + _words("后面的一句", 80.0)
    cues = align_text_to_words(["开头的一句。", "这次我会多放老姜。", "后面的一句。"], w, (0.0, 100.0))
    mid = cues[1]
    assert mid.guess and mid.end - mid.start > 0.3      # not a 0-length cue pinned at 50 s


def test_a_lone_guessed_line_does_not_swallow_all_the_voice():
    w = _words("开头的一句", 10.0) + _words("后面的一句", 80.0)
    cues = align_text_to_words(["开头的一句。", "中间听不清。", "后面的一句。"], w, (0.0, 100.0),
                               voice=[(20.0, 70.0)])
    assert cues[1].end - cues[1].start <= 8.0 + 1e-9


def test_repair_runs_relays_stacked_and_overlong_cues_between_good_neighbours():
    from app.models import Cue
    from app.pipeline.align import repair_runs

    cues = [Cue(1, 0.0, 3.0, "好的一句"), Cue(2, 10.0, 10.3, "一一一"), Cue(3, 10.0, 10.3, "二二二"),
            Cue(4, 12.0, 90.0, "三三三"), Cue(5, 95.0, 98.0, "好的二句")]
    assert repair_runs(cues, voice=[(4.0, 20.0)]) == 3
    assert (cues[0].start, cues[-1].start) == (0.0, 95.0)          # good cues untouched
    mid = cues[1:4]
    assert all(4.0 <= c.start < c.end <= 20.0 and c.guess for c in mid)
    assert mid[0].start < mid[1].start < mid[2].start
