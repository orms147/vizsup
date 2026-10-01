"""UX-3: undo covers every cue mutation, and redo exists.

Undo used to cover split/merge/add/delete + table cell edits only. Timeline drags,
per-line style, and gain/mute mutated self.cues with no snapshot, so a mis-drag was
unrecoverable. These pin each snapshot point and the redo semantics.

The sharp edge is COALESCING: Timeline.cueChanged fires on every mouse-move, so
snapshotting there would bury the stack under one drag. The snapshot hangs off
dragStarted / sliderPressed instead — one step per gesture.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
editor_view = pytest.importorskip("desktop.views.editor_view")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.cuemodel import EditorCue  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    v = editor_view.EditorView()
    v.cues = [EditorCue(start=0.0, end=2.0, vi="một", zh="一"),
              EditorCue(start=2.0, end=4.0, vi="hai", zh="二"),
              EditorCue(start=4.0, end=6.0, vi="ba", zh="三")]
    v.cue_list.load(v.cues)
    v.timeline.set_cues(v.cues)
    v._update_cue_panel(0)
    return v


def _press(widget, x=70.0, y=90.0):
    """Press inside cue 0's block. Blocks begin at RULER_H+WAVE_H (78 px) and cue 0
    spans x=0..80 at the default 40 px/s; x must clear the 6 px edge grab zones or
    the hit reads as an edge-resize (or a miss, which only seeks)."""
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPoint(int(x), int(y)),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    widget.mousePressEvent(ev)


# --- the gaps UX-3 was about --------------------------------------------------

def test_gain_change_is_undoable(view):
    view._update_cue_panel(0)
    view.gain.setValue(6)                      # keyboard/programmatic path
    assert view.cues[0].gain_db == 6.0

    view._undo_action()
    assert view.cues[0].gain_db == 0.0


def test_mute_toggle_is_undoable(view):
    view._update_cue_panel(1)
    view.mute_cb.setChecked(True)
    assert view.cues[1].mute is True

    view._undo_action()
    assert view.cues[1].mute is False


def test_timeline_drag_is_undoable(view):
    """A drag mutates cue times directly from the widget."""
    before = (view.cues[0].start, view.cues[0].end)
    _press(view.timeline)                      # dragStarted -> snapshot
    view.cues[0].start += 1.0                  # what mouseMoveEvent does
    view.cues[0].end += 1.0
    view._on_cue_dragged(0)

    view._undo_action()
    assert (view.cues[0].start, view.cues[0].end) == before


def test_one_drag_is_one_undo_step_not_one_per_pixel(view):
    """cueChanged fires per mouse-move; the stack must not fill up from one drag."""
    depth = len(view._undo_stack)
    _press(view.timeline)
    for _ in range(40):                        # simulate 40 move events
        view.cues[0].end += 0.01
        view._on_cue_dragged(0)
    assert len(view._undo_stack) == depth + 1


def test_slider_drag_is_one_undo_step(view):
    view._update_cue_panel(0)
    depth = len(view._undo_stack)
    view.gain.setSliderDown(True)              # emits sliderPressed itself
    for v in range(1, 8):
        view.gain.setValue(v)
    view.gain.setSliderDown(False)
    assert len(view._undo_stack) == depth + 1
    assert view.cues[0].gain_db == 7.0


# --- redo ---------------------------------------------------------------------

def test_undo_then_redo_round_trips(view):
    view._update_cue_panel(0)
    view.gain.setValue(4)
    view._undo_action()
    assert view.cues[0].gain_db == 0.0

    view._redo_action()
    assert view.cues[0].gain_db == 4.0


def test_redo_survives_several_steps(view):
    view._snapshot()
    view.cues[0].vi = "A"
    view._snapshot()
    view.cues[0].vi = "B"

    view._undo_action()
    view._undo_action()
    assert view.cues[0].vi == "một"

    view._redo_action()
    assert view.cues[0].vi == "A"
    view._redo_action()
    assert view.cues[0].vi == "B"


def test_a_new_edit_invalidates_the_redo_chain(view):
    view._snapshot()
    view.cues[0].vi = "A"
    view._undo_action()
    assert view._redo_stack                    # redo available...

    view._snapshot()                           # ...until the user diverges
    view.cues[0].vi = "C"
    assert view._redo_stack == []


def test_redo_with_an_empty_stack_is_a_no_op(view):
    before = [c.vi for c in view.cues]
    view._redo_action()
    assert [c.vi for c in view.cues] == before


def test_undo_with_an_empty_stack_is_a_no_op(view):
    view._undo_stack.clear()
    before = [c.vi for c in view.cues]
    view._undo_action()
    assert [c.vi for c in view.cues] == before


def test_undo_refreshes_the_gain_widget_not_just_the_data(view):
    """If the slider keeps the stale value, the next drag writes it straight back."""
    view._update_cue_panel(0)
    view.gain.setValue(9)
    view._undo_action()
    assert view.cues[0].gain_db == 0.0
    assert view.gain.value() == 0


# --- job switching ------------------------------------------------------------

def test_loading_a_job_clears_both_stacks(view, tmp_path):
    from app.models import Job

    view._snapshot()
    view._undo_action()
    assert view._redo_stack

    job = Job(id="j", work_dir=tmp_path)
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    view.load(job)
    assert view._undo_stack == []
    assert view._redo_stack == []              # the old job's future must not leak


# --- the stack stays bounded --------------------------------------------------

def test_undo_stack_is_capped(view):
    for i in range(140):
        view._snapshot()
        view.cues[0].vi = str(i)
    assert len(view._undo_stack) == 100
