"""N-5: table-driven regression tests for assemble._plan_segments — the dub-sync
policy core (overlap-free absolute timeline, 1.30x cap, trim-vs-push, carry cap).

probe_duration is monkeypatched with a path→seconds dict, so no media is needed.
Cases are argued from the documented policy, not from observed float output —
invariants over goldens wherever possible.
"""
from __future__ import annotations

import random

import pytest

from app.models import Cue
from app.pipeline import assemble
from app.pipeline.assemble import MAX_CARRY, SPEED_ACCEPT, SPEED_MAX, TRIM_BUDGET, _plan_segments


def _fake_probe(durations: dict[str, float]):
    return lambda p: durations[str(p)]


def _segs(monkeypatch, rows: list[tuple[float, float, float]]):
    """rows = [(cue_start, cue_end, seg_duration)] → segments + patched probe."""
    segments, durations = [], {}
    for i, (s, e, d) in enumerate(rows, 1):
        path = f"seg_{i}.mp3"
        segments.append((Cue(index=i, start=s, end=e, text=f"c{i}"), path))
        durations[path] = d
    monkeypatch.setattr(assemble, "probe_duration", _fake_probe(durations))
    return segments


def test_fits_in_own_slot_untouched(monkeypatch):
    segs = _segs(monkeypatch, [(0.0, 2.0, 1.5), (3.0, 5.0, 1.0)])
    placements, sped, flagged = _plan_segments(segs)
    assert [p["tempo"] for p in placements] == [1.0, 1.0]
    assert [p["start"] for p in placements] == [0.0, 3.0]
    assert sped == 0 and flagged == []


def test_span_reclaims_gap_to_next_cue(monkeypatch):
    # cue is 1s long but the next cue starts at 5s → a 3s segment fits unsped
    segs = _segs(monkeypatch, [(0.0, 1.0, 3.0), (5.0, 6.0, 0.5)])
    placements, sped, flagged = _plan_segments(segs)
    assert placements[0]["tempo"] == 1.0 and sped == 0 and flagged == []
    assert placements[1]["start"] == 5.0                       # neighbour untouched


def test_tempo_hard_capped_at_speed_max(monkeypatch):
    segs = _segs(monkeypatch, [(0.0, 1.0, 10.0), (2.0, 3.0, 0.5)])
    placements, sped, flagged = _plan_segments(segs)
    assert placements[0]["tempo"] == pytest.approx(SPEED_MAX)
    assert sped == 1                                           # above SPEED_ACCEPT
    assert [c.index for c, _, _ in flagged] == [1]             # can't fit even at cap


def test_sped_counter_ignores_gentle_speedup(monkeypatch):
    # tempo needed = 2.3/2 = 1.15 == SPEED_ACCEPT → inaudible, not counted
    segs = _segs(monkeypatch, [(0.0, 1.0, 2.3), (2.0, 3.0, 0.5)])
    _, sped, flagged = _plan_segments(segs)
    assert sped == 0 and flagged == []


def test_small_overrun_trimmed_not_pushed(monkeypatch):
    # eff = 2.9/1.3 ≈ 2.231 → over ≈ 0.231 ≤ TRIM_BUDGET → fade-trim, no flag,
    # and the next line starts exactly on its cue
    segs = _segs(monkeypatch, [(0.0, 1.0, 2.9), (2.0, 3.0, 0.5)])
    placements, _, flagged = _plan_segments(segs)
    assert placements[0]["trim"] is True
    assert placements[0]["start"] + placements[0]["eff"] == pytest.approx(2.0)
    assert placements[1]["start"] == pytest.approx(2.0)
    assert flagged == []


def test_big_overrun_pushes_next_and_flags(monkeypatch):
    # eff = 5/1.3 ≈ 3.846 → over ≈ 1.846 > TRIM_BUDGET, carry 0 < MAX_CARRY → push
    segs = _segs(monkeypatch, [(0.0, 1.0, 5.0), (2.0, 3.0, 0.5)])
    placements, _, flagged = _plan_segments(segs)
    assert placements[0]["trim"] is False
    assert placements[1]["start"] == pytest.approx(5.0 / SPEED_MAX)   # pushed late
    assert placements[1]["carry_s"] == pytest.approx(5.0 / SPEED_MAX - 2.0, abs=1e-3)
    assert [c.index for c, _, _ in flagged] == [1]


def test_carry_cap_switches_push_to_trim(monkeypatch):
    # c1 pushes c2 ~1.85s late (> MAX_CARRY); c2's own big overrun must then be
    # TRIMMED (to the 0.25s floor) instead of pushing c3 further
    segs = _segs(monkeypatch, [(0.0, 1.0, 5.0), (2.0, 3.0, 8.0), (4.0, 5.0, 0.5)])
    placements, _, flagged = _plan_segments(segs)
    p2 = placements[1]
    assert p2["carry_s"] >= MAX_CARRY
    assert p2["trim"] is True
    assert p2["eff"] == pytest.approx(0.25)                    # trim floor
    assert {c.index for c, _, _ in flagged} == {1, 2}
    # c3 starts at c2's trimmed end, not after 6s of pushed audio
    assert placements[2]["start"] == pytest.approx(p2["start"] + 0.25)


def test_zero_duration_segment_skipped(monkeypatch):
    segs = _segs(monkeypatch, [(0.0, 1.0, 0.0), (2.0, 3.0, 1.0)])
    placements, _, _ = _plan_segments(segs)
    assert [p["inp"] for p in placements] == [2]               # first seg dropped


def test_out_of_order_input_planned_in_time_order_inp_preserved(monkeypatch):
    # segments list order = ffmpeg input order; planning order = cue time order
    segs = _segs(monkeypatch, [(5.0, 6.0, 1.0), (0.0, 1.0, 1.0)])
    placements, _, _ = _plan_segments(segs)
    assert [p["inp"] for p in placements] == [2, 1]
    assert [p["start"] for p in placements] == [0.0, 5.0]


def test_no_overlap_invariant_randomized(monkeypatch):
    rng = random.Random(20260702)
    rows = []
    t = 0.0
    for _ in range(60):
        t += rng.uniform(0.2, 3.0)
        dur = rng.uniform(0.3, 2.0)
        rows.append((round(t, 2), round(t + dur, 2), round(rng.uniform(0.2, 6.0), 2)))
    placements, _, _ = _plan_segments(_segs(monkeypatch, rows))
    prev_end = 0.0
    for p in placements:
        assert p["start"] >= prev_end - 1e-9                   # NEVER overlaps
        assert p["tempo"] <= SPEED_MAX + 1e-9                  # cap always holds
        assert p["eff"] > 0
        prev_end = p["start"] + p["eff"]


def test_gap_reset_constant_removed():
    # v1's GAP_RESET was dead code promising a rule the planner never ran;
    # re-anchoring actually happens via start = max(cue.start, prev_end)
    assert not hasattr(assemble, "GAP_RESET")
    assert SPEED_ACCEPT < SPEED_MAX and TRIM_BUDGET < MAX_CARRY  # sanity of the real knobs
