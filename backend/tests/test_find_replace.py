"""UX-4: find/replace over the VI column.

The search policy is pure (cuemodel) so it is testable without a display; the
widget test then checks the bar is wired to it, snapshots for undo, and — the
part that actually protects the user — never touches the zh reference column.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from desktop.cuemodel import (  # noqa: E402
    EditorCue,
    find_matches,
    find_next,
    replace_all,
    replace_in_cue,
)


def cues(*texts: str) -> list[EditorCue]:
    return [EditorCue(start=i, end=i + 1, vi=t, zh=f"中文{i}") for i, t in enumerate(texts)]


# --- find_matches -------------------------------------------------------------

def test_find_is_case_insensitive_by_default():
    cs = cues("Hà Nội mùa thu", "về hà nội", "Sài Gòn")
    assert find_matches(cs, "hà nội") == [0, 1]


def test_find_case_sensitive_mode():
    cs = cues("Hà Nội mùa thu", "về hà nội", "Sài Gòn")
    assert find_matches(cs, "Hà Nội", case_sensitive=True) == [0]


def test_empty_needle_matches_nothing():
    """An empty box must not 'match everything' and yank the selection to row 0."""
    assert find_matches(cues("a", "b"), "") == []


def test_find_ignores_the_zh_reference_column():
    cs = cues("xin chào")
    cs[0].zh = "你好世界"
    assert find_matches(cs, "你好") == []


def test_diacritics_are_distinct():
    cs = cues("chào bạn", "chao bạn")
    assert find_matches(cs, "chào") == [0]


# --- find_next ----------------------------------------------------------------

def test_find_next_advances_then_wraps():
    cs = cues("một", "hai", "một nữa", "ba")
    assert find_next(cs, "một", 0) == 0
    assert find_next(cs, "một", 1) == 2
    assert find_next(cs, "một", 3) == 0          # wrap


def test_find_next_from_no_selection_lands_on_the_first_hit():
    """_cur() is -1 with nothing selected, so start arrives as 0 after the +1 —
    but a bare -1 must be clamped too, not treated as 'past the end'."""
    cs = cues("một", "hai")
    assert find_next(cs, "hai", -1) == 1


def test_find_next_without_matches_is_minus_one():
    assert find_next(cues("một", "hai"), "ba", 0) == -1


# --- replace_in_cue -----------------------------------------------------------

def test_replace_in_cue_replaces_only_the_first_occurrence():
    cs = cues("ta ta ta")
    assert replace_in_cue(cs, 0, "ta", "X") is True
    assert cs[0].vi == "X ta ta"


def test_replace_in_cue_case_insensitive_keeps_the_rest_intact():
    cs = cues("Hà nội đẹp")
    assert replace_in_cue(cs, 0, "hà nội", "Hà Nội") is True
    assert cs[0].vi == "Hà Nội đẹp"


def test_replace_in_cue_out_of_range_is_a_no_op():
    cs = cues("một")
    assert replace_in_cue(cs, 9, "một", "x") is False
    assert replace_in_cue(cs, -1, "một", "x") is False
    assert cs[0].vi == "một"


# --- replace_all --------------------------------------------------------------

def test_replace_all_counts_occurrences_and_lines():
    cs = cues("ta ta", "ta", "không")
    assert replace_all(cs, "ta", "X") == (3, 2)
    assert [c.vi for c in cs] == ["X X", "X", "không"]


def test_replace_all_is_literal_not_a_regex():
    """A needle with regex metacharacters must match itself."""
    cs = cues("giá 1.000đ (giảm)", "a+b")
    assert replace_all(cs, "(giảm)", "") == (1, 1)
    assert cs[0].vi == "giá 1.000đ "
    assert replace_all(cs, "a+b", "c") == (1, 1)
    assert cs[1].vi == "c"


def test_replace_all_case_insensitive_normalizes_spelling():
    cs = cues("hà nội", "Hà nội", "HÀ NỘI")
    assert replace_all(cs, "hà nội", "Hà Nội") == (3, 3)
    assert [c.vi for c in cs] == ["Hà Nội"] * 3


def test_replace_all_case_sensitive_leaves_other_spellings_alone():
    cs = cues("hà nội", "Hà nội")
    assert replace_all(cs, "Hà nội", "Hà Nội", case_sensitive=True) == (1, 1)
    assert [c.vi for c in cs] == ["hà nội", "Hà Nội"]


def test_replace_all_never_touches_zh():
    cs = cues("xin chào")
    cs[0].zh = "xin chào"                      # contrived, but pins the intent
    replace_all(cs, "xin chào", "hello")
    assert cs[0].vi == "hello"
    assert cs[0].zh == "xin chào"


def test_replace_all_empty_needle_is_a_no_op():
    cs = cues("một", "hai")
    assert replace_all(cs, "", "X") == (0, 0)
    assert [c.vi for c in cs] == ["một", "hai"]


# --- the widget wiring --------------------------------------------------------

pytest.importorskip("PySide6.QtWidgets")
editor_view = pytest.importorskip("desktop.views.editor_view")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    v = editor_view.EditorView()
    v.cues = cues("Hà Nội mùa thu", "về hà nội", "Sài Gòn nắng")
    v.table.load(v.cues)
    v.timeline.set_cues(v.cues)
    return v


def test_find_bar_starts_hidden_and_toggles(view):
    # isHidden(), not isVisible(): the view itself is never shown offscreen, so
    # isVisible() is False for every child regardless of the bar's own state.
    assert view.find_bar.isHidden()
    view._toggle_find(True)
    assert not view.find_bar.isHidden()
    view._toggle_find()              # no arg → toggle
    assert view.find_bar.isHidden()
    view._toggle_find()
    assert not view.find_bar.isHidden()


def test_count_label_reports_matches(view):
    view.find_in.setText("hà nội")
    assert "2" in view.find_lbl.text()
    view.find_case.setChecked(True)
    assert "1" in view.find_lbl.text()
    view.find_in.setText("không có đâu")
    assert view.find_lbl.text() == "không thấy"


def test_find_next_moves_the_table_selection(view):
    view.find_in.setText("hà nội")
    view.table.setCurrentCell(0, 0)
    view._find_next()
    assert view.table.currentRow() == 1


def test_replace_all_edits_every_line_and_is_undoable(view):
    view.find_in.setText("hà nội")
    view.repl_in.setText("Hà Nội")
    view._replace_all()

    assert [c.vi for c in view.cues] == ["Hà Nội mùa thu", "về Hà Nội", "Sài Gòn nắng"]
    assert "2 chỗ" in view.find_lbl.text()

    view._undo_action()
    assert [c.vi for c in view.cues] == ["Hà Nội mùa thu", "về hà nội", "Sài Gòn nắng"]


def test_replace_all_with_no_match_leaves_no_undo_step(view):
    """A no-op must not cost the user a Ctrl+Z press to get past."""
    before = len(view._undo_stack)
    view.find_in.setText("không có chuỗi này")
    view.repl_in.setText("x")
    view._replace_all()
    assert len(view._undo_stack) == before
    assert view.find_lbl.text() == "không thấy"


def test_replace_one_jumps_before_it_edits(view):
    """With row 2 (no match) selected, the first 'Thay' should move to a match
    rather than editing the unrelated row under the cursor."""
    view.find_in.setText("hà nội")
    view.repl_in.setText("HN")
    view.table.setCurrentCell(2, 0)
    view._replace_one()
    assert [c.vi for c in view.cues] == ["Hà Nội mùa thu", "về hà nội", "Sài Gòn nắng"]
    assert view.table.currentRow() == 0          # moved onto a match

    view._replace_one()                          # now it edits
    assert view.cues[0].vi == "HN mùa thu"
