"""v2 editor view behaviour (offscreen Qt): timing nudges respect the lock and are
one undo step each, typing is one undo step per editing session, "Đã xem" + F8 walk
the same pending set the filter shows, and a failed autosave never reports saved."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
editor_view = pytest.importorskip("desktop.views.editor_view")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop import cuemodel  # noqa: E402
from desktop.cuemodel import EditorCue  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    v = editor_view.EditorView()
    v.cues = [EditorCue(start=0.0, end=2.0, vi="một", zh="一"),
              EditorCue(start=2.0, end=2.3, vi="hai", zh="二"),        # short → flagged
              EditorCue(start=4.0, end=6.0, vi="ba", zh="三"),
              EditorCue(start=6.0, end=6.4, vi="bốn", zh="四")]        # short → flagged
    v._reload(dirty=False)
    v.cue_list.selectRow(0)
    return v


def test_nudge_is_one_undo_step(view):
    view._nudge("end", 200)
    assert view.cues[0].end == pytest.approx(2.2)
    assert view.inspector.t_fields["end"].text() == "00:02.200"
    view._undo_action()
    assert view.cues[0].end == 2.0


def test_locked_cue_refuses_nudges_without_an_undo_step(view):
    view.inspector.lock_cb.setChecked(True)          # the lock itself is undoable
    assert view.cues[0].locked
    depth = len(view._undo_stack)
    view._nudge("start", 50)
    view._set_edge("end")
    assert (view.cues[0].start, view.cues[0].end) == (0.0, 2.0)
    assert len(view._undo_stack) == depth
    view._undo_action()
    assert not view.cues[0].locked


def test_typing_is_one_undo_step_per_session(view):
    ed = view.inspector.vi_edit
    depth = len(view._undo_stack)
    for ch in "xyz":
        ed.insertPlainText(ch)
    assert view.cues[0].vi.endswith("xyz")
    assert len(view._undo_stack) == depth + 1
    view._undo_action()
    assert view.cues[0].vi == "một"


def test_enter_commits_and_moves_to_the_next_cue(view):
    view.inspector.commitNext.emit()
    assert view.cue_list.currentRow() == 1 and view._sel == 1


def test_review_filter_f8_and_reviewed_share_one_set(view):
    assert view.cue_list.pending_rows() == [1, 3]
    view._goto_pending(+1)
    assert view._sel == 1
    view._mark_reviewed()                            # → jumps to the next pending cue
    assert cuemodel.is_reviewed(view.cues[1])
    assert view._sel == 3
    assert view.cue_list.pending_rows() == [3]
    view.cue_list.set_review_only(True)
    assert view.cue_list.view.isRowHidden(0) and not view.cue_list.view.isRowHidden(3)


def test_editing_a_reviewed_cue_puts_it_back_in_review(view):
    view.cue_list.selectRow(1)
    view._mark_reviewed()
    assert 1 not in view.cue_list.pending_rows()
    view.cue_list.selectRow(1)
    view.inspector.vi_edit.insertPlainText("!")
    assert 1 in view.cue_list.pending_rows()


def test_failed_autosave_reports_and_retries(view, monkeypatch, tmp_path):
    from app.models import Job

    view.job = Job(id="x", work_dir=tmp_path / "x")
    events = []
    view.saveFailed.connect(lambda m: events.append(("failed", m)))
    view.dirtyChanged.connect(lambda d: events.append(("dirty", d)))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(cuemodel, "save_vi", boom)
    view.save_now()
    assert ("failed", "disk full") in events
    assert ("dirty", False) not in events
    assert view._save_timer.isActive()               # retry scheduled
    view._save_timer.stop()


def test_split_needs_the_playhead_inside_the_cue(view, monkeypatch):
    msgs = []
    view.message.connect(lambda t, k: msgs.append(k))
    monkeypatch.setattr(view.video, "current_time", lambda: 5.0)
    view._split()                                    # cue 0 is 0–2 s
    assert len(view.cues) == 4 and msgs == ["warn"]
    monkeypatch.setattr(view.video, "current_time", lambda: 1.0)
    view._split()
    assert len(view.cues) == 5
    assert (view.cues[0].end, view.cues[1].start) == (1.0, 1.0)


# --- tự canh lời (A / Shift+A) ----------------------------------------------------

def test_snap_buttons_wait_for_voice_data(view):
    assert not view.inspector.snap_btn.isEnabled()
    assert not view.timeline_panel.snap_all_btn.isEnabled()
    view._set_speech([(0.0, 1.5)])
    assert view.inspector.snap_btn.isEnabled() and view.timeline_panel.snap_all_btn.isEnabled()


def test_snap_selected_is_one_undo_step(view):
    view._set_speech([(0.3, 1.6), (2.0, 2.3), (4.0, 5.5), (6.0, 6.4)])
    view.cue_list.selectRow(0)
    depth = len(view._undo_stack)
    view._auto_snap_selected()
    assert (view.cues[0].start, view.cues[0].end) == (0.22, 1.75)
    assert len(view._undo_stack) == depth + 1
    view._undo_action()
    assert (view.cues[0].start, view.cues[0].end) == (0.0, 2.0)


def test_snap_selected_refuses_a_locked_cue(view):
    view._set_speech([(0.1, 1.6)])
    view.cues[0].locked = True
    msgs = []
    view.message.connect(lambda t, k: msgs.append(k))
    depth = len(view._undo_stack)
    view._auto_snap_selected()
    assert (view.cues[0].start, view.cues[0].end) == (0.0, 2.0)
    assert len(view._undo_stack) == depth and msgs == ["warn"]


def test_snap_all_is_one_undo_step_and_skips_locked(view):
    view._set_speech([(0.3, 1.6), (4.0, 5.5)])
    view.cues[2].locked = True
    msgs = []
    view.message.connect(lambda t, k: msgs.append(t))
    depth = len(view._undo_stack)
    view._auto_snap_all()
    assert len(view._undo_stack) == depth + 1
    assert (view.cues[0].start, view.cues[0].end) == (0.22, 1.75)
    assert (view.cues[2].start, view.cues[2].end) == (4.0, 6.0)   # locked
    assert "Đã canh" in msgs[-1] and "khóa" in msgs[-1]
    view._undo_action()
    assert (view.cues[0].start, view.cues[0].end) == (0.0, 2.0)


def test_worker_snap_failure_is_logged_not_raised(qapp, tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import snap
    from desktop.worker import PipelineWorker

    def boom(*_a, **_k):
        raise RuntimeError("no audio")

    monkeypatch.setattr(snap, "snap_srt", boom)
    w = PipelineWorker(Job(id="w", work_dir=tmp_path / "w"), {})
    logs = []
    w.log.connect(logs.append)
    w._snap_to_voice(w.job)
    assert logs and logs[-1].startswith("⚠ Bỏ qua bước tự canh lời")


def test_ai_finding_shows_in_the_inspector_and_its_fix_is_one_undo_step(view):
    view._qa = [{"start": 0.0, "end": 2.0, "vi": "một", "kind": "meaning",
                 "note": "sai nghĩa", "fix": "một hai"}]
    view._refresh_issues()
    view.cue_list.selectRow(0)
    view._update_cue_panel(0)
    assert not view.inspector.ai_box.isHidden() and view.inspector.ai_fix.text() == "một hai"
    assert "ai" in view.issues[0]
    view.inspector.ai_apply.click()
    assert view.cues[0].vi == "một hai" and "ai" not in view.issues[0]
    assert view.inspector.ai_box.isHidden()
    view._undo_action()
    assert view.cues[0].vi == "một"


def test_snap_all_suggests_the_word_track_when_voice_is_hidden(view, tmp_path):
    from app.models import Job

    view.job = Job(id="h", work_dir=tmp_path / "h")
    view.cues[1].start, view.cues[1].end = 20.0, 22.0       # three lines far from any voice
    view.cues[2].start, view.cues[2].end = 30.0, 32.0
    view.cues[3].start, view.cues[3].end = 40.0, 42.0
    view._set_speech([(0.3, 1.6)])
    msgs = []
    view.message.connect(lambda t, k: msgs.append(t))
    view._auto_snap_all()
    assert "Đo lại thời gian từ âm thanh" in msgs[-1]
    (view.job.work_dir / "asr_words.json").write_text('{"words": [["我", 0.3, 0.6]]}',
                                                      encoding="utf-8")
    view._auto_snap_all()                                    # word track exists → no hint
    assert "Đo lại thời gian" not in msgs[-1]
