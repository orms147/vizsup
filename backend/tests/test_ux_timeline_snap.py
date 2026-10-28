"""Timeline dragging (UX audit E7): an edge sticks to the playhead and to a neighbour's edge, a cue
cannot be dragged across its neighbours, Alt drags freely — and a neighbour it already overlapped
(old ASR data) does not freeze it."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from desktop.widgets.timeline import drag_cue  # noqa: E402

KW = dict(prev_end=5.0, next_start=12.0, playhead=8.0, video_dur=60.0, snap=0.2)


# --- the pure function -------------------------------------------------------------------

def test_move_sticks_to_a_neighbour_edge():
    # cue 6.0–8.0 dragged left by 0.9: its start would be 5.1, 0.1 from the previous cue's end 5.0
    s, e = drag_cue("move", 6.0, 8.0, -0.9, **KW)
    assert (s, e) == (5.0, 7.0)
    # dragged right: its END comes within 0.1 of the next cue's start 12.0
    s, e = drag_cue("move", 6.0, 8.0, +3.9, **KW)
    assert (s, e) == (10.0, 12.0)


def test_move_sticks_to_the_playhead_by_either_edge():
    s, e = drag_cue("move", 6.0, 8.0, +0.1, **dict(KW, playhead=6.2, next_start=30.0))
    assert s == 6.2 and e == pytest.approx(8.2)                         # start → playhead
    s, e = drag_cue("move", 6.0, 8.0, -0.1, **dict(KW, playhead=7.85, prev_end=0.0))
    assert e == 7.85 and s == pytest.approx(5.85)                       # end → playhead


def test_nothing_sticks_beyond_the_snap_distance():
    s, e = drag_cue("move", 6.0, 8.0, +0.5, **dict(KW, playhead=20.0))
    assert (s, e) == (6.5, 8.5)


def test_a_cue_cannot_cross_its_neighbours():
    s, e = drag_cue("move", 6.0, 8.0, -3.0, **KW)                      # far past the previous cue
    assert (s, e) == (5.0, 7.0)
    s, e = drag_cue("move", 6.0, 8.0, +9.0, **KW)                      # far past the next cue
    assert (s, e) == (10.0, 12.0)
    assert drag_cue("l", 6.0, 8.0, -4.0, **KW) == (5.0, 8.0)           # start edge stops at prev end
    assert drag_cue("r", 6.0, 8.0, +9.0, **KW) == (6.0, 12.0)          # end edge stops at next start


def test_resize_edges_snap_and_keep_a_minimum_length():
    assert drag_cue("l", 6.0, 8.0, -0.9, **KW) == (5.0, 8.0)
    assert drag_cue("r", 6.0, 8.0, +3.9, **KW) == (6.0, 12.0)
    s, e = drag_cue("l", 6.0, 8.0, +5.0, **dict(KW, playhead=30.0))     # dragged past its own end
    assert e == 8.0 and e - s >= 0.2 - 1e-9


def test_alt_drags_freely():
    assert drag_cue("move", 6.0, 8.0, -0.9, free=True, **{k: v for k, v in KW.items()}) == (5.1, 7.1)
    s, e = drag_cue("move", 6.0, 8.0, -3.0, free=True, **KW)            # may cross the neighbour
    assert s == pytest.approx(3.0)


def test_an_already_overlapping_neighbour_does_not_freeze_the_cue():
    # the previous cue ends at 7.0 although this one starts at 6.0 (old ASR overlap)
    s, e = drag_cue("move", 6.0, 8.0, -2.0, **dict(KW, prev_end=7.0))
    assert s == pytest.approx(4.0)
    s, e = drag_cue("move", 6.0, 8.0, +1.0, **dict(KW, next_start=7.0))
    assert s == pytest.approx(7.0)


def test_the_video_end_and_zero_still_bound_a_cue():
    assert drag_cue("move", 6.0, 8.0, -50.0, prev_end=None, next_start=None, playhead=99.0,
                    video_dur=60.0, snap=0.2) == (0.0, 2.0)
    s, e = drag_cue("move", 6.0, 8.0, +99.0, prev_end=None, next_start=None, playhead=99.0,
                    video_dur=60.0, snap=0.2)
    assert e == 60.0 and s == 58.0


# --- through the widget ------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _tl(qapp):
    from desktop.cuemodel import EditorCue
    from desktop.widgets.timeline import Timeline

    t = Timeline()
    t.set_cues([EditorCue(start=1.0, end=4.0, vi="a", zh=""), EditorCue(start=6.0, end=8.0, vi="b", zh=""),
                EditorCue(start=12.0, end=14.0, vi="c", zh="")])
    t.set_duration(30.0)
    t.set_zoom(40.0)
    t.resize(1400, 140)
    t.set_time(9.0)
    return t


def _mouse(kind, t, x, y, mods=0):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    pos = QPointF(x, y)
    typ = QEvent.MouseButtonPress if kind == "press" else QEvent.MouseMove
    btn = Qt.LeftButton if kind == "press" else Qt.NoButton
    return QMouseEvent(typ, pos, t.mapToGlobal(pos), btn, Qt.LeftButton, Qt.KeyboardModifier(mods))


def test_dragging_a_block_snaps_to_the_playhead_clamps_and_alt_frees(qapp):
    from PySide6.QtCore import Qt

    t = _tl(qapp)
    r = t._block_rect(t._cues[1])
    x0, y = r.center().x(), r.center().y()
    t.mousePressEvent(_mouse("press", t, x0, y))
    # +2.5 s (100 px): 8.5–10.5 is not near anything → follows the mouse, no guide
    t.mouseMoveEvent(_mouse("move", t, x0 + 2.5 * 40, y))
    assert t._cues[1].end == pytest.approx(10.5, abs=0.01) and t._snap_t is None
    # end near the playhead (9.0): 8 + 1.03 = 9.03 → sticks to 9.0
    t.mouseMoveEvent(_mouse("move", t, x0 + 1.03 * 40, y))
    assert t._cues[1].end == 9.0 and t._snap_t == 9.0
    # far to the right: stops where the next cue starts
    t.mouseMoveEvent(_mouse("move", t, x0 + 20 * 40, y))
    assert t._cues[1].end == 12.0 and t._cues[1].start == 10.0
    # Alt: crosses it
    t.mouseMoveEvent(_mouse("move", t, x0 + 20 * 40, y, Qt.AltModifier))
    assert t._cues[1].start > 12.0
    t.mouseReleaseEvent(_mouse("move", t, x0, y))
    assert t._snap_t is None
