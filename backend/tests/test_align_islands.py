"""Lines laid on voice "islands" (pipeline/align.py): a line never bridges a long silence, and
a line alone on a stretch of real voice is no longer a blind guess. Numbers from the real
sung-ad e2e job (2026-10-29) are in the docstrings."""
from __future__ import annotations

import pytest

from app.pipeline import align


def test_voice_islands_merge_short_gaps_and_drop_slivers():
    segs = [(0.0, 1.0), (1.5, 2.0), (4.0, 4.1), (6.0, 7.0)]
    assert align.voice_islands(segs) == [(0.0, 2.0), (6.0, 7.0)]      # 0.5 s gap merges; 4.0 is a click
    assert align.voice_islands([]) == []
    assert align.voice_islands([(3.0, 2.0 + 3.1), (1.0, 3.0)]) == [(1.0, 5.1)]   # unsorted input


def test_assign_keeps_order_and_matches_pace():
    islands = [(0.0, 1.0), (10.0, 12.0)]                  # 1 s then 2 s
    assert align.assign_to_islands([4, 8], islands) == [0, 1]       # 4 chars ≈ 1 s, 8 ≈ 2 s
    assert align.assign_to_islands([4, 4, 4], islands) in ([0, 1, 1], [0, 0, 1])
    which = align.assign_to_islands([4, 4, 4, 4], [(0.0, 4.0)])
    assert which == [0, 0, 0, 0]


def test_the_real_chant_gap_no_longer_bridges_sixteen_seconds():
    """Three 4-char lines in 195.4–216.6 with voice at 196.35–199.29 and 215.68–216.64: the
    old proportional placement gave the last line 198.9–206.9 (7.6 s of silence)."""
    voice = [(195.42, 195.58), (196.35, 199.29), (215.68, 216.64)]
    spans, sure = align.place_on_islands([4, 4, 4], voice)
    for s, e in spans:
        inside = [(max(a, s), min(b, e)) for a, b in voice if a < e and b > s]
        gaps = [y[0] - x[1] for x, y in zip(inside, inside[1:])]
        assert not gaps or max(gaps) < align.ISLAND_GAP          # no silence inside a line
    assert spans == sorted(spans) and all(a[1] <= b[0] + 1e-9 for a, b in zip(spans, spans[1:]))
    assert not any(sure)                                          # lines share an island: estimates


def test_a_line_alone_on_an_island_is_sure_and_edges_are_the_voice_edges():
    spans, sure = align.place_on_islands([10], [(138.72, 143.04)])
    assert spans == [(138.72, 143.04)] and sure == [True]         # 0.43 s/char: plausible
    spans, sure = align.place_on_islands([10], [(0.0, 0.5)])      # 0.05 s/char — too fast
    assert sure == [False]
    spans, sure = align.place_on_islands([40], [(0.0, 30.0)])     # 0.75 s/char — too slow
    assert sure == [False]


def test_short_interjections_are_sure_over_a_breath_but_not_a_long_stretch():
    assert align.place_on_islands([1], [(5.0, 5.6)])[1] == [True]           # "叮，" 0.6 s
    assert align.place_on_islands([1], [(5.0, 9.0)])[1] == [False]


def test_lines_sharing_an_island_are_not_sure_but_stay_inside_it():
    spans, sure = align.place_on_islands([4, 4], [(10.0, 12.0)])
    assert spans == [(10.0, 11.0), (11.0, 12.0)] and sure == [False, False]


def test_two_islands_two_lines_are_both_sure():
    spans, sure = align.place_on_islands([4, 4], [(1.0, 2.0), (8.0, 9.0)])
    assert spans == [(1.0, 2.0), (8.0, 9.0)] and sure == [True, True]


def test_unexplained_voice_or_heard_words_withdraw_the_confidence():
    # a single island plus Whisper-heard time far outside it (the VAD missed part of the line)
    spans, sure = align.place_on_islands([12], [(159.08, 161.5)], heard=[(160.5, 164.6)],
                                         window=(159.08, 165.29))
    assert sure == [False]
    spans, sure = align.place_on_islands([12], [(159.08, 161.5)], heard=[(160.5, 161.4)],
                                         window=(159.08, 165.29))
    assert sure == [True]                                         # heard time inside the island


def test_no_island_falls_back_to_the_old_proportional_placement():
    voice = [(1.0, 1.1), (3.0, 3.1)]                              # only clicks
    spans, sure = align.place_on_islands([4, 4], voice)
    assert sure == [False, False] and len(spans) == 2
    assert align.place_on_islands([4], []) == ([], [False])


def test_heard_spans_merge_and_skip_stretched_words():
    words = [("你", 1.0, 1.3), ("好", 1.4, 1.7), ("嗯", 5.0, 9.0), ("他", 6.0, 6.0)]
    assert align.heard_spans(words) == [(1.0, 1.7)]


# --- inside align_text_to_words ------------------------------------------------------------

def test_a_line_with_no_heard_words_alone_in_voice_is_no_longer_a_guess():
    words = [("你好", 0.0, 0.8), ("再见", 10.0, 10.8)]
    sents = ["你好", "啪！", "再见"]
    out = align.align_text_to_words(sents, words, (0.0, 12.0), voice=[(4.0, 4.6)])
    mid = out[1]
    assert (mid.start, mid.end) == (4.0, 4.6) and mid.guess is False
    assert out[0].guess is False and out[2].guess is False


def test_the_same_line_without_voice_data_stays_a_guess():
    words = [("你好", 0.0, 0.8), ("再见", 10.0, 10.8)]
    out = align.align_text_to_words(["你好", "啪！", "再见"], words, (0.0, 12.0), voice=None)
    assert out[1].guess is True


def test_a_run_sharing_one_island_stays_guessed_and_stays_inside_the_voice():
    words = [("你好", 0.0, 0.8), ("再见", 20.0, 20.8)]
    sents = ["你好", "岂曰无衣", "与子同袍", "再见"]
    out = align.align_text_to_words(sents, words, (0.0, 22.0), voice=[(5.0, 7.0)])
    assert out[1].guess and out[2].guess
    assert 5.0 <= out[1].start and out[2].end <= 7.0 + 1e-6


def test_heard_words_outside_the_voice_keep_the_flag():
    # text mismatch: Whisper heard other hanzi 161–165 where VAD found no voice
    words = [("前", 150.0, 150.5), ("千人", 160.5, 160.9), ("不忍", 161.1, 161.5), ("用戒", 162.0, 162.5),
             ("心和", 163.0, 163.5), ("狼传", 164.0, 164.5), ("归乡", 165.5, 166.0)]
    sents = ["前", "前人故人多今星河", "归乡"]
    out = align.align_text_to_words(sents, words, (149.0, 167.0), voice=[(159.08, 161.5)])
    assert out[1].guess is True


def test_repair_runs_keeps_repaired_lines_inside_voice():
    cues = [align.Cue(index=1, start=0.0, end=2.0, text="好的好的"),
            align.Cue(index=2, start=3.0, end=3.0, text="岂曰无衣"),
            align.Cue(index=3, start=3.0, end=3.0, text="与子同袍"),
            align.Cue(index=4, start=30.0, end=32.0, text="再见再见")]
    n = align.repair_runs(cues, voice=[(10.0, 12.0)])
    assert n == 2
    assert 10.0 <= cues[1].start and cues[2].end <= 12.0 + 1e-6 and cues[1].guess and cues[2].guess


@pytest.mark.parametrize("weights,islands", [([3], [(0.0, 5.0)]), ([1, 1, 1, 1], [(0.0, 1.0), (3.0, 4.0)]),
                                             ([5, 5, 5], [(0.0, 0.3)] * 3)])
def test_assignment_is_monotone_for_any_input(weights, islands):
    which = align.assign_to_islands(weights, align.voice_islands(islands) or [(0.0, 1.0)])
    assert which == sorted(which) and len(which) == len(weights)


def test_a_huge_run_stays_fast_and_falls_back_to_proportional():
    import time

    weights = [4] * 900
    voice = [(i * 2.0, i * 2.0 + 1.5) for i in range(600)]
    t = time.perf_counter()
    spans, sure = align.place_on_islands(weights, voice)
    assert time.perf_counter() - t < 2.0
    assert len(spans) == 900 and not any(sure)
