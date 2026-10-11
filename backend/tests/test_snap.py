"""Tự canh lời: cue edges snap to the nearest voice start / stop — bounded, so a cue
never jumps to another sentence, never steals a neighbour's voice, never overlaps."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.models import Cue, Job
from app.pipeline import snap
from app.pipeline.srt import parse_srt, write_srt


@dataclass
class C:
    start: float
    end: float
    locked: bool = False


def run(cues, spans, **kw):
    times, rep = snap.plan(cues, spans, **kw)
    return times, rep


def test_trailing_silence_is_trimmed_and_start_follows_the_voice():
    times, rep = run([C(1.0, 4.0)], [(1.0, 3.2)])
    assert times[0] == (0.92, 3.35)            # onset − PAD_IN, offset + PAD_OUT
    assert rep.changed == [0]


def test_end_extends_while_the_voice_keeps_going():
    times, _ = run([C(1.0, 3.0)], [(0.95, 3.6)])
    assert times[0] == (0.87, 3.75)


def test_late_asr_within_reach_is_fixed():
    times, _ = run([C(3.8, 5.6)], [(3.0, 5.0)])
    assert times[0] == (2.92, 5.15)


def test_far_off_asr_is_left_alone():
    """2.5 s off — beyond REACH, so it might be another sentence: don't guess."""
    times, rep = run([C(5.5, 7.5)], [(3.0, 5.0)])
    assert times[0] == (5.5, 7.5) and rep.changed == []


def test_continuous_singing_leaves_the_cue_alone():
    times, rep = run([C(5.0, 8.0)], [(0.0, 20.0)])
    assert times[0] == (5.0, 8.0) and rep.changed == [] and rep.no_voice == []


def test_no_voice_nearby_is_reported_not_moved():
    times, rep = run([C(5.0, 6.0)], [(0.0, 1.0), (20.0, 21.0)])
    assert times[0] == (5.0, 6.0) and rep.no_voice == [0]


def test_neighbours_keep_their_own_voice():
    a, b = C(6.1, 8.5), C(8.6, 11.0)
    times, _ = run([a, b], [(6.0, 9.0), (9.5, 11.5)])
    (sa, ea), (sb, eb) = times
    assert (sa, ea) == (5.92, 9.15)            # A ends when ITS voice stops
    assert (sb, eb) == (9.42, 11.65)           # B starts when ITS voice starts
    assert ea <= sb - snap.GAP + 1e-9


def test_overlap_clamps_the_earlier_end_never_the_later_start():
    times, _ = run([C(1.0, 3.0), C(2.8, 5.0)], [(1.0, 3.4), (3.6, 5.0)])
    (sa, ea), (sb, eb) = times
    assert sb == 3.52                          # B's start sits on B's voice onset
    assert ea == round(sb - snap.GAP, 3)       # A gives way
    assert (sa, eb) == (0.92, 5.15)


def test_infeasible_boundary_keeps_the_original_edges():
    a, b = C(1.0, 1.3), C(1.2, 4.0, locked=True)
    times, rep = run([a, b], [(1.1, 1.6)])
    assert times[0] == (1.0, 1.3) and 0 not in rep.changed


def test_locked_cues_never_move():
    times, rep = run([C(1.0, 4.0, locked=True)], [(1.0, 3.2)])
    assert times[0] == (1.0, 4.0) and rep.locked == [0] and rep.changed == []


def test_tiny_shifts_are_ignored():
    times, rep = run([C(1.0, 3.0)], [(1.1, 2.88)])
    assert times[0] == (1.0, 3.0) and rep.changed == []


def test_a_snap_never_cuts_more_than_max_cut():
    times, _ = run([C(0.0, 1.5)], [(0.0, 0.6)])
    assert times[0][1] == 1.5                  # would cut 0.75 s of 1.5 s → refused


def test_end_never_passes_the_video():
    times, _ = run([C(8.0, 9.8)], [(8.0, 9.95)], duration=10.0)
    assert times[0][1] == 10.0


def test_only_moves_the_chosen_cue():
    cues = [C(1.0, 4.0), C(5.0, 8.0)]
    rep = snap.snap_cues(cues, [(1.0, 3.2), (5.5, 7.0)], only={1})
    assert rep.changed == [1]
    assert (cues[0].start, cues[0].end) == (1.0, 4.0)
    assert (cues[1].start, cues[1].end) == (5.42, 7.15)


def test_snap_srt_rewrites_only_with_voice_data(tmp_path, monkeypatch):
    from app.pipeline import vad

    job = Job(id="s", work_dir=tmp_path / "s")
    write_srt([Cue(1, 1.0, 4.0, "一"), Cue(2, 5.0, 8.0, "二")], job.cn_srt)
    monkeypatch.setattr("app.pipeline.assemble.probe_duration", lambda p: 10.0)

    monkeypatch.setattr(vad, "speech_spans", lambda job, log=None: None)
    assert snap.snap_srt(job, job.cn_srt) is None
    assert [(c.start, c.end) for c in parse_srt(job.cn_srt)] == [(1.0, 4.0), (5.0, 8.0)]

    logs = []
    monkeypatch.setattr(vad, "speech_spans", lambda job, log=None: [(1.0, 3.2)])
    rep = snap.snap_srt(job, job.cn_srt, log=logs.append)
    assert rep.changed == [0] and rep.no_voice == [1]
    assert [(c.start, c.end) for c in parse_srt(job.cn_srt)] == [
        (pytest.approx(0.92), pytest.approx(3.35)), (5.0, 8.0)]
    assert "1/2" in logs[-1]


def test_the_next_sentences_voice_is_not_this_cues_end():
    """Regression: the offset nearest to A's end was B's voice end (2.3 s), so A's end
    reached toward B and got clamped at 1.95 instead of stopping with A's voice."""
    times, _ = run([C(0.0, 2.0), C(2.0, 2.3, locked=True)], [(0.3, 1.6), (2.0, 2.3)])
    assert times[0] == (0.22, 1.75)
