"""Selecting several lines at once (2026-11-02): Ctrl+A, Shift / Ctrl + click on the timeline,
Ctrl+C / Ctrl+V, one menu for the group, "Nghe lại", and the batched re-translate / listening
backends. Offscreen Qt; nothing reaches the network."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.pipeline import listen, translate  # noqa: E402
from desktop.cuemodel import EditorCue  # noqa: E402
from desktop.selection_actions import paste_plan, plain_lines  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, monkeypatch):
    from desktop.views.editor_view import EditorView

    v = EditorView()
    v.cues = [EditorCue(start=float(i * 5), end=float(i * 5 + 3), vi=f"câu {i}", zh=f"中{i}")
              for i in range(8)]
    v._reload(dirty=False)
    t = {"now": 0.0}
    monkeypatch.setattr(v.video, "current_time", lambda: t["now"])
    monkeypatch.setattr(v.video, "seek_seconds", lambda s: t.__setitem__("now", s))
    v.show()
    qapp.processEvents()
    QApplication.clipboard().clear()
    yield v
    v.close()


# --- pure rules -------------------------------------------------------------------------

def test_plain_lines_drops_blanks():
    assert plain_lines("a\r\n\n  b  \n") == ["a", "b"]


def test_paste_plan():
    assert paste_plan(["x"], [2, 4], 8) == [(2, "x"), (4, "x")]            # one text → every row
    assert paste_plan(["a", "b", "c"], [5], 8) == [(5, "a"), (6, "b"), (7, "c")]   # on down the list
    assert paste_plan(["a", "b", "c", "d"], [1, 3], 8) == [(1, "a"), (3, "b")]      # never past the selection
    assert paste_plan(["a", "b", "c"], [7], 8) == [(7, "a")]                        # the list ends
    assert paste_plan([], [1], 8) == [] and paste_plan(["a"], [], 8) == []


def test_same_text_ignores_punctuation_and_catches_other_words():
    assert listen.same_text("我是谁？", "我是谁")
    assert not listen.same_text("徭役", "什么玩意")
    assert listen.same_text("", "")


# --- backends -----------------------------------------------------------------------------

class Rewriter:
    def available(self):
        return True

    def rewrite(self, body, instruction, *, max_tokens=300, role=None):
        self.body, self.role = body, role
        return "\n".join(f"{ln.split('|', 1)[0]}| mới {ln.split('|', 1)[0]}"
                         for ln in body.splitlines() if "| ZH:" in ln)


def test_retranslate_lines_sends_zh_and_neighbours_and_keeps_order(monkeypatch):
    prov = Rewriter()
    monkeypatch.setattr(translate, "get_translator", lambda name: prov)
    items = [{"zh": f"中{i}", "vi": f"cũ {i}", "before": "trước", "after": "sau"} for i in range(3)]
    out = translate.retranslate_lines(items, brief="phim cổ trang", request="xưng tôi")
    assert out == ["mới 1", "mới 2", "mới 3"]
    assert "ZH: 中0" in prov.body and "trước: trước" in prov.body and "xưng tôi" in prov.body
    assert prov.role == "shorten"


# --- the list and the timeline ----------------------------------------------------------------

def _click(tl, block: int, mods) -> None:
    r = tl._block_rect(tl._cues[block])
    pos = QPointF(r.center().x(), r.center().y())
    ev = QMouseEvent(QEvent.MouseButtonPress, pos, tl.mapToGlobal(pos), Qt.LeftButton,
                     Qt.LeftButton, mods)
    tl.mousePressEvent(ev)
    tl.mouseReleaseEvent(ev)


def test_ctrl_and_shift_click_on_the_timeline_select_many_and_the_list_follows(view, qapp):
    tl = view.timeline
    _click(tl, 1, Qt.NoModifier)
    _click(tl, 3, Qt.ControlModifier)
    qapp.processEvents()
    assert tl.multi() == [1, 3] and view.cue_list.selected_rows() == [1, 3]
    _click(tl, 6, Qt.ShiftModifier)                       # the range from the current one (3)
    qapp.processEvents()
    assert tl.multi() == [1, 3, 4, 5, 6] and view.cue_list.selected_rows() == [1, 3, 4, 5, 6]
    _click(tl, 4, Qt.ControlModifier)                     # Ctrl on a selected block removes it
    assert 4 not in tl.multi() and 4 not in view.cue_list.selected_rows()
    _click(tl, 0, Qt.NoModifier)                          # a plain click starts over
    qapp.processEvents()
    assert tl.multi() == [0] and view.cue_list.selected_rows() == [0]


def test_selecting_in_the_list_marks_the_timeline_blocks(view, qapp):
    view.cue_list.set_selected_rows([2, 3, 5], 3)
    qapp.processEvents()
    assert view.timeline.multi() == [2, 3, 5]


def test_ctrl_a_selects_every_line(view, qapp):
    view._select_all_cues()
    assert view.cue_list.selected_rows() == list(range(8)) and view.timeline.multi() == list(range(8))


def test_copy_paste_between_lines_is_one_undo_step(view, qapp):
    view.cue_list.set_selected_rows([1, 2], 1)
    view._clip_copy()
    assert QApplication.clipboard().text() == "câu 1\ncâu 2"
    view.cue_list.set_selected_rows([5, 6], 5)
    n = len(view._undo_stack)
    view._clip_paste()
    assert [c.vi for c in view.cues[5:7]] == ["câu 1", "câu 2"] and len(view._undo_stack) == n + 1
    view._undo_action()
    assert [c.vi for c in view.cues[5:7]] == ["câu 5", "câu 6"]
    view.cue_list.selectRow(0)                            # plain text from outside lands on the line
    QApplication.clipboard().setText("lời mới")
    view._clip_paste()
    assert view.cues[0].vi == "lời mới"


def test_group_menu_replaces_the_single_line_menu_for_a_selection(view, qapp):
    view.cue_list.set_selected_rows([1, 2, 3], 2)
    texts = [a.text() for a in view._group_menu(view._sel_rows()).actions()]
    assert any(t.startswith("Dịch lại 3 câu") for t in texts)
    assert any(t.startswith("AI nghe lại 3 câu") for t in texts)
    assert any(t.startswith("Xóa 3 câu") for t in texts)
    merge = next(a for a in view._group_menu([1, 3]).actions() if a.text().startswith("Gộp"))
    assert merge.isEnabled() is False                     # not neighbours


def test_group_delete_merge_and_lock_are_undoable(view, qapp):
    view.cue_list.set_selected_rows([1, 2], 1)
    view._merge_rows()
    assert len(view.cues) == 7 and "câu 1" in view.cues[1].vi and "câu 2" in view.cues[1].vi
    view._undo_action()
    assert len(view.cues) == 8
    view.cue_list.set_selected_rows([3, 4, 5], 3)
    view._lock_rows(True)
    assert all(view.cues[i].locked for i in (3, 4, 5))
    view._delete_rows()
    assert len(view.cues) == 5
    view._undo_action()
    assert len(view.cues) == 8


def test_listen_plays_the_selection_and_stops_at_its_end(view, qapp, monkeypatch):
    played = []
    monkeypatch.setattr(view.video, "play", lambda: played.append("play"))
    monkeypatch.setattr(view.video, "pause", lambda: played.append("pause"))
    view.cue_list.set_selected_rows([1, 2], 1)
    view._listen_rows()
    assert played == ["play"] and view._listen_until == pytest.approx(13.15)   # cue 2 ends at 13 s
    view._listen_tick(8.0)
    assert played == ["play"]                             # still inside
    view._listen_tick(13.2)
    assert played == ["play", "pause"] and view._listen_until is None


def test_listen_gives_up_when_the_user_seeks_away(view, qapp):
    view._listen_from, view._listen_until = 4.3, 13.15
    view._listen_tick(30.0)
    assert view._listen_until is None


def test_ai_listen_refuses_a_big_selection(view, qapp):
    msgs = []
    view.message.connect(lambda m, k: msgs.append(m))
    view.cues.extend(EditorCue(start=100 + i, end=100.5 + i, vi="x", zh="y") for i in range(40))
    view._reload(dirty=False)
    view._select_all_cues()
    view._ai_listen_rows()
    assert any("tối đa 40" in m for m in msgs)
