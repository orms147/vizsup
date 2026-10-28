"""Subtitle timing post-pass (pipeline/timing_pass.py): 2-frame gaps, chaining, minimum
duration, scene cuts, locks — pure cases; plus the worker option and the editor action."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.models import Cue, Job
from app.pipeline import timing_pass as tp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class C:
    def __init__(self, start, end, locked=False):
        self.start, self.end, self.locked = start, end, locked


def times(cues, **kw):
    out, rep = tp.plan(cues, kw.pop("fps", 25), **kw)
    return out, rep


# --- gaps -----------------------------------------------------------------------------

def test_short_gap_is_chained_to_two_frames():
    out, rep = times([C(0.0, 2.0), C(2.3, 4.0)])
    assert out == [(0.0, 2.22), (2.3, 4.0)]            # 2 frames @ 25 fps = 0.08 s
    assert rep.closed == [0] and rep.changed == [0]


def test_gap_in_frames_follows_the_fps():
    out, _ = times([C(0, 2.0), C(2.3, 4.0)], fps=50)    # 2 frames @ 50 fps = 0.04 s
    assert out[0][1] == pytest.approx(2.26)
    out, _ = times([C(0, 2.0), C(2.3, 4.0)], fps=24)
    assert out[0][1] == pytest.approx(2.3 - 2 / 24, abs=1e-3)


def test_long_gap_is_left_alone():
    out, rep = times([C(0, 2.0), C(2.6, 4.0)])
    assert out == [(0, 2.0), (2.6, 4.0)] and not rep.changed


def test_exactly_two_frames_is_left_alone():
    out, rep = times([C(0, 2.0), C(2.08, 4.0)])
    assert not rep.changed


def test_touching_and_overlapping_pairs_get_two_frames_by_trimming_the_earlier_end():
    out, rep = times([C(0, 2.0), C(2.0, 4.0), C(3.5, 6.0)])
    assert out[0] == (0, 1.92)                          # touching → trimmed
    assert out[1][0] == 2.0 and out[1][1] == pytest.approx(3.42)   # overlap → earlier trimmed
    assert out[2] == (3.5, 6.0)                         # the later line never loses its start
    assert rep.min_gap == [0, 1]


def test_overlap_that_would_crush_the_earlier_line_moves_the_later_start():
    out, rep = times([C(0, 0.3), C(0.25, 2.0)])
    assert out[0] == (0, 0.3)
    assert out[1][0] == pytest.approx(0.38)             # earlier would drop below 0.2 s
    assert rep.min_gap == [1]


# --- minimum duration -----------------------------------------------------------------

def test_short_line_is_extended_to_twenty_frames():
    out, rep = times([C(0, 0.3), C(5, 6)])
    assert out[0] == (0, 0.8) and rep.lengthened == [0] and not rep.blocked


def test_min_duration_is_blocked_by_the_neighbour():
    out, rep = times([C(0, 0.3), C(0.5, 3.0)])
    assert out[0][1] == pytest.approx(0.42)             # up to the next start minus 2 frames
    assert rep.blocked == [0]


def test_last_line_is_limited_by_the_video_length():
    out, rep = times([C(0, 1.0), C(9.9, 10.0)], duration=10.2)
    assert out[1] == (9.9, 10.2)
    assert rep.blocked == [1]


def test_min_duration_never_pulls_a_start_earlier():
    out, _ = times([C(0, 1), C(3, 3.2)])
    assert out[1][0] == 3


# --- locks, order, idempotence ---------------------------------------------------------

def test_locked_cues_never_move_and_are_reported():
    cues = [C(0, 2.0, locked=True), C(2.3, 4.0), C(4.1, 4.2, locked=True)]
    out, rep = times(cues)
    assert out[0] == (0, 2.0) and out[2] == (4.1, 4.2)
    assert out[1] == (2.3, 4.02)                        # unlocked neighbour is untouched
    assert 0 in rep.locked                              # the chain over line 0 was refused
    assert rep.changed == [1]


def test_locked_overlap_moves_the_unlocked_side():
    out, rep = times([C(0, 2.0, locked=True), C(1.8, 4.0)])
    assert out[0] == (0, 2.0)
    assert out[1][0] == pytest.approx(2.08)
    both, rep2 = times([C(0, 2.0, locked=True), C(1.8, 4.0, locked=True)])
    assert both == [(0, 2.0), (1.8, 4.0)] and rep2.locked == [0]


def test_order_and_count_are_preserved_and_input_not_mutated():
    cues = [C(0, 0.3), C(0.4, 1.0), C(1.2, 1.3)]
    before = [(c.start, c.end) for c in cues]
    out, _ = times(cues)
    assert len(out) == 3 and [o[0] for o in out] == sorted(o[0] for o in out)
    assert [(c.start, c.end) for c in cues] == before


def test_second_run_changes_nothing():
    cues = [C(0, 0.3), C(0.4, 1.0), C(1.2, 1.3), C(1.3, 3.0), C(2.9, 5.0)]
    tp.apply(cues, 25)
    again = tp.apply(cues, 25)
    assert not again.changed


def test_works_on_real_cue_objects():
    cues = [Cue(index=1, start=0.0, end=2.0, text="a"), Cue(index=2, start=2.2, end=3.0, text="b")]
    rep = tp.apply(cues, 25)
    assert cues[0].end == pytest.approx(2.12) and rep.closed == [0]


# --- scene cuts -----------------------------------------------------------------------

def test_edges_near_a_cut_snap_to_it():
    cues = [C(1.1, 3.0), C(5.0, 7.0)]
    out, rep = times(cues, scenes=[1.0, 7.1])
    assert out[0][0] == 1.0                             # start onto the cut's first frame
    assert out[1][1] == pytest.approx(7.02)             # end 2 frames before the cut
    assert rep.snapped == [0, 1]


def test_edges_far_from_a_cut_are_untouched():
    cues = [C(1.5, 3.0)]
    out, rep = times(cues, scenes=[1.0, 4.0])
    assert out == [(1.5, 3.0)] and not rep.snapped


def test_snap_respects_the_neighbour_and_a_minimum_length():
    # the cut at 2.05 is inside the previous cue's tail → the start may not jump onto it
    out, _ = times([C(0, 2.0), C(2.1, 5.0)], scenes=[2.0, 2.15])
    assert out[1][0] >= out[0][1] + 0.08 - 1e-6
    out, _ = times([C(1.0, 1.3)], scenes=[1.2])         # would leave < 0.4 s → kept
    assert out[0][0] == 1.0


def test_locked_cue_does_not_snap():
    out, _ = times([C(1.1, 3.0, locked=True)], scenes=[1.0])
    assert out == [(1.1, 3.0)]


def test_chaining_does_not_run_over_a_cut():
    out, _ = times([C(0, 2.0), C(2.4, 4.0)], scenes=[2.2])
    assert out[0][1] == pytest.approx(2.2 - 0.08, abs=1e-3)    # stops 2 frames before the cut
    out, _ = times([C(0, 2.0), C(2.4, 4.0)])                    # no scenes → full chain
    assert out[0][1] == pytest.approx(2.32)


def test_parse_showinfo():
    err = ("[Parsed_showinfo_1 @ 0x1] n:   0 pts:  25 pts_time:1 pos: 1 fmt:yuv420p\n"
           "[Parsed_showinfo_1 @ 0x1] n:   1 pts: 130 pts_time:5.2 pos: 2\nnoise\n")
    assert tp.parse_showinfo(err) == [1.0, 5.2]


def _ffmpeg():
    try:
        from app.ffmpegutil import ffmpeg_bin
        b = ffmpeg_bin()
    except Exception:  # noqa: BLE001
        return None
    return b if shutil.which(b) or Path(b).exists() else None


@pytest.mark.skipif(_ffmpeg() is None, reason="ffmpeg not available")
def test_detect_scenes_finds_a_hard_cut_and_caches(tmp_path):
    vid = tmp_path / "cut.mp4"
    subprocess.run([_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=160x90:d=1:r=25",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1:r=25", "-filter_complex",
                    "concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(vid)], check=True)
    cache = tmp_path / "scenes.json"
    cuts = tp.detect_scenes(vid, cache)
    assert len(cuts) == 1 and cuts[0] == pytest.approx(1.0, abs=0.05)
    vid.rename(tmp_path / "moved.mp4")                  # cache hit needs no ffmpeg / same key
    (tmp_path / "moved.mp4").rename(vid)
    assert tp.detect_scenes(vid, cache) == cuts
    assert tp.probe_fps(vid) == pytest.approx(25, abs=0.1)


def test_run_srt_rewrites_only_when_needed(tmp_path):
    from app.pipeline.srt import parse_srt, write_srt

    job = Job(id="t", work_dir=tmp_path / "job")
    write_srt([Cue(index=1, start=0, end=2.0, text="a"), Cue(index=2, start=2.2, end=4, text="b")],
              job.cn_srt)
    logs: list[str] = []
    rep = tp.run_srt(job, job.cn_srt, log=logs.append)
    assert rep.closed == [0] and parse_srt(job.cn_srt)[0].end == pytest.approx(2.12)
    assert logs and "nối 1" in logs[-1]
    mtime = job.cn_srt.stat().st_mtime_ns
    tp.run_srt(job, job.cn_srt)
    assert job.cn_srt.stat().st_mtime_ns == mtime       # second run: nothing written
    job.cn_srt.write_text("", encoding="utf-8")
    assert tp.run_srt(job, job.cn_srt) is None


# --- worker option ----------------------------------------------------------------------

pytest.importorskip("PySide6.QtWidgets")


def _gate(tmp_path, monkeypatch, opts):
    """Run _gate_body with a provided SRT up to the first step after the timing pass."""
    from app.pipeline import split as split_mod
    from desktop import worker as worker_mod

    class Stop(Exception):
        pass

    def stop(*_a, **_k):
        raise Stop

    srt = tmp_path / "in.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n一\n\n2\n00:00:02,200 --> 00:00:04,000\n二\n",
                   encoding="utf-8")
    job = Job(id="g", work_dir=tmp_path / "job")
    job.source_video.write_bytes(b"x")
    monkeypatch.setattr(worker_mod, "_playable", lambda _p: True)
    monkeypatch.setattr(worker_mod.PipelineWorker, "_preflight_translators", lambda *_a: None)
    monkeypatch.setattr(worker_mod.PipelineWorker, "_split_if_long", lambda *_a: False)
    monkeypatch.setattr(split_mod, "inherit_brief", stop)
    w = worker_mod.PipelineWorker(job, {"subtitle_source": "srt", "srt": str(srt),
                                        "auto_snap": False, **opts})
    ran: list[bool] = []
    real = worker_mod.PipelineWorker._timing_pass
    monkeypatch.setattr(worker_mod.PipelineWorker, "_timing_pass",
                        lambda self, j, o: (ran.append(True), real(self, j, o))[1])
    with pytest.raises(Stop):
        w._gate_body()
    return job, ran


def test_worker_runs_the_timing_pass_by_default(tmp_path, monkeypatch):
    from app.pipeline.srt import parse_srt

    job, ran = _gate(tmp_path, monkeypatch, {})
    assert ran == [True]
    assert parse_srt(job.cn_srt)[0].end == pytest.approx(2.12)       # default fps, gap chained


def test_worker_skips_it_when_the_option_is_off(tmp_path, monkeypatch):
    from app.pipeline.srt import parse_srt

    job, ran = _gate(tmp_path, monkeypatch, {"timing_pass": False})
    assert ran == []
    assert parse_srt(job.cn_srt)[0].end == pytest.approx(2.0)


def test_worker_failure_is_logged_not_raised(tmp_path, monkeypatch):
    from desktop.worker import PipelineWorker

    def boom(*_a, **_k):
        raise RuntimeError("ffprobe missing")

    monkeypatch.setattr(tp, "run_srt", boom)
    w = PipelineWorker(Job(id="w", work_dir=tmp_path / "w"), {})
    logs: list[str] = []
    w.log.connect(logs.append)
    w._timing_pass(w.job, {})
    assert logs and logs[-1].startswith("⚠ Bỏ qua bước chỉnh thời gian chuẩn")


def test_store_remembers_the_checkbox():
    from desktop import store

    assert "timing_pass" in store._UI_KEYS


# --- editor action ------------------------------------------------------------------------

editor_view = pytest.importorskip("desktop.views.editor_view")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    from desktop.cuemodel import EditorCue

    v = editor_view.EditorView()
    v.cues = [EditorCue(start=0.0, end=2.0, vi="một", zh="一"),
              EditorCue(start=2.3, end=4.0, vi="hai", zh="二", locked=True),
              EditorCue(start=4.2, end=6.0, vi="ba", zh="三")]
    v._reload(dirty=False)
    return v


def test_editor_action_is_one_undo_step_and_skips_locked(view):
    msgs: list[str] = []
    view.message.connect(lambda m, _k: msgs.append(m))
    depth = len(view._undo_stack)
    view._timing_pass()
    assert len(view._undo_stack) == depth + 1
    assert view.cues[0].end == pytest.approx(2.22)                      # chained
    assert (view.cues[1].start, view.cues[1].end) == (2.3, 4.0)         # locked: untouched
    assert view.cues[2].start == pytest.approx(4.2)
    assert "Đã chỉnh thời gian" in msgs[-1]
    view._undo_action()
    assert [(c.start, c.end) for c in view.cues] == [(0.0, 2.0), (2.3, 4.0), (4.2, 6.0)]


def test_editor_action_with_nothing_to_do_leaves_undo_alone(view):
    view.cues[0].end = 2.22
    view.cues[1].end = 4.12
    view._reload(dirty=False)
    depth = len(view._undo_stack)
    view._timing_pass()
    assert len(view._undo_stack) == depth


def test_cue_list_menu_has_the_action(view):
    got: list[bool] = []
    view.cue_list.timingPassRequested.connect(lambda: got.append(True))
    acts = [a for a in view.cue_list.more_btn.menu().actions() if "Chỉnh thời gian chuẩn" in a.text()]
    assert acts
    acts[0].trigger()
    assert got == [True]
