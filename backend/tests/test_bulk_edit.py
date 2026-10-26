"""Editor bulk edits found missing in the 2026-10-26 end-to-end test.

A fast talker left 102 of 756 lines "Đọc không kịp" and 57 AI-review notes; the editor
only had per-line Rút gọn / Dùng gợi ý (one dialog each). Pinned here: the batched
shorten call, the review dialog, one undo step for a bulk apply, the undo/redo buttons
for mouse users, the "Câu i / N" footer following a click, Vietnamese standard buttons,
and the cover-box default for videos with burned-in Chinese.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
editor_view = pytest.importorskip("desktop.views.editor_view")

from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from app.pipeline import translate  # noqa: E402
from desktop.cuemodel import EditorCue  # noqa: E402
from desktop.widgets.dialogs import BulkEditDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeRewriter:
    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def rewrite(self, body, instruction, *, max_tokens=300, role=None):
        self.calls += 1
        # "n| (rút còn ~X%) text" → "n| text" minus its last word
        out = []
        for line in body.splitlines():
            n, rest = line.split("| ", 1)
            words = rest.split(") ", 1)[1].split()
            out.append(f"{n}| {' '.join(words[:-1])}")
        return "\n".join(out)


# --- backend: shorten_lines ---------------------------------------------------------

def test_shorten_lines_batches_and_keeps_order(monkeypatch):
    prov = FakeRewriter()
    monkeypatch.setattr(translate, "get_translator", lambda name: prov)
    items = [(f"câu số {i} khá là dài", 70) for i in range(translate.BATCH + 3)]
    out = translate.shorten_lines(items)
    assert prov.calls == 2                                  # one call per BATCH lines
    assert out[0] == "câu số 0 khá là" and out[-1] == f"câu số {translate.BATCH + 2} khá là"


def test_shorten_lines_keeps_a_line_that_did_not_get_shorter(monkeypatch):
    class Same(FakeRewriter):
        def rewrite(self, body, instruction, **kw):
            return "1| một câu dài hơn hẳn câu gốc ban đầu"
    monkeypatch.setattr(translate, "get_translator", lambda name: Same())
    assert translate.shorten_lines([("ngắn", 70)]) == ["ngắn"]


def test_shorten_lines_raises_without_a_rewriter(monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: object())
    with pytest.raises(RuntimeError):
        translate.shorten_lines([("x", 70)])


# --- the dialog -----------------------------------------------------------------------

def test_bulk_dialog_returns_ticked_rows_with_edits(qapp):
    dlg = BulkEditDialog(None, title="t", intro="i", rows=[(0, "a b c", "a b", ""),
                                                           (4, "x y z", "x y", "note")])
    assert [i for i, _ in dlg.chosen()] == [0, 4]
    dlg._rows[0][1].setChecked(False)
    dlg._rows[1][2].setText("x")
    assert dlg.chosen() == [(4, "x")]
    dlg._set_all(False)
    assert dlg.chosen() == [] and not dlg.apply_btn.isEnabled()


def test_ai_dialog_starts_unticked(qapp):
    dlg = BulkEditDialog(None, title="t", intro="i", rows=[(0, "a", "b", "")], checked=False)
    assert dlg.chosen() == []


# --- the editor -----------------------------------------------------------------------

@pytest.fixture()
def view(qapp):
    v = editor_view.EditorView()
    v.cues = [EditorCue(start=0.0, end=1.0, vi="một câu rất là dài để đọc trong một giây thôi",
                        zh="一"),
              EditorCue(start=1.0, end=4.0, vi="hai", zh="二"),
              EditorCue(start=4.0, end=6.0, vi="ba", zh="三")]
    v._reload(dirty=False)
    v._update_cue_panel(0)
    return v


def test_bulk_targets_pick_fast_lines_only(view):
    targets = view._bulk_targets()
    assert [i for i, _p, _n in targets] == [0]
    _i, pct, note = targets[0]
    assert 50 <= pct <= 90 and "Đọc không kịp" in note


def test_bulk_apply_is_one_undo_step(view):
    view._undo_stack.clear()
    view._update_undo_buttons()
    assert not view.undo_btn.isEnabled() and not view.redo_btn.isEnabled()
    view._apply_bulk([(0, "ngắn"), (2, "ba ba")], "Đã rút gọn")
    assert [c.vi for c in view.cues] == ["ngắn", "hai", "ba ba"]
    assert len(view._undo_stack) == 1 and view.undo_btn.isEnabled()
    view.undo_btn.click()
    assert view.cues[0].vi.startswith("một câu") and view.cues[2].vi == "ba"
    assert view.redo_btn.isEnabled()
    view.redo_btn.click()
    assert view.cues[0].vi == "ngắn"


def test_ai_suggestions_lists_open_findings(view, monkeypatch):
    seen = {}

    class Dlg:
        def __init__(self, parent, *, title, intro, rows, checked, mark_rest=""):
            seen.update(rows=rows, checked=checked)

        def exec(self):
            return False

    monkeypatch.setattr("desktop.widgets.dialogs.BulkEditDialog", Dlg)
    view._qa = [{"start": 1.0, "end": 4.0, "vi": "hai", "kind": "meaning", "note": "n",
                 "fix": "hai mươi"}]
    view._ai_suggestions()
    assert seen["checked"] is False
    assert [(r[0], r[2]) for r in seen["rows"]] == [(1, "hai mươi")]


def test_footer_follows_a_clicked_row(view):
    view.cue_list.view.setCurrentIndex(view.cue_list.model.index(2))
    assert "Câu 3 / 3" in view.cue_list.pos_lbl.text()


# --- burned-in Chinese → cover box by default ---------------------------------------

def test_has_hardsubs_reads_metadata(tmp_path):
    class J:
        work_dir = tmp_path
    assert editor_view._has_hardsubs(J()) is False
    (tmp_path / "metadata.json").write_text(json.dumps({"has_hardsubs": True}), encoding="utf-8")
    assert editor_view._has_hardsubs(J()) is True


# --- Qt's own strings in Vietnamese ----------------------------------------------------

def test_standard_buttons_are_vietnamese(qapp):
    from desktop.qt_vi import ViTranslator

    t = ViTranslator(qapp)
    qapp.installTranslator(t)
    try:
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        assert sorted(b.text() for b in bb.buttons()) == ["Hủy", "Lưu"]
    finally:
        qapp.removeTranslator(t)


# --- "Gộp câu quá ngắn & gỡ chồng lấn" (pure) ----------------------------------------

def _c(a, b, vi, **kw):
    return EditorCue(start=a, end=b, vi=vi, zh="", **kw)


def test_tidy_merges_a_mid_sentence_fragment_forward():
    from desktop.cuemodel import tidy_timing

    out, merged, fixed = tidy_timing([_c(0, 2, "Câu đầu."), _c(2.1, 2.5, "iPhone Air,"),
                                      _c(2.6, 5, "rất mỏng.")])
    assert (merged, fixed) == (1, 0)
    assert [c.vi for c in out] == ["Câu đầu.", "iPhone Air, rất mỏng."]
    assert (out[1].start, out[1].end) == (2.1, 5)


def test_tidy_merges_a_sentence_tail_backward_and_respects_limits():
    from desktop.cuemodel import tidy_timing

    out, merged, _ = tidy_timing([_c(0, 2, "Nhưng mà"), _c(2.1, 2.4, "thôi."), _c(4, 6, "Sau.")])
    assert [c.vi for c in out] == ["Nhưng mà thôi.", "Sau."]
    # too far from both neighbours / a locked neighbour → left alone
    out, merged, _ = tidy_timing([_c(0, 2, "A,", locked=True), _c(2.1, 2.4, "b,"),
                                  _c(4, 6, "C.")])
    assert merged == 0 and len(out) == 3


def test_tidy_fixes_overlaps_without_touching_locked_lines():
    from desktop.cuemodel import tidy_timing

    cues = [_c(0, 3, "Một."), _c(2.5, 5, "Hai.")]
    out, merged, fixed = tidy_timing(cues)
    assert fixed == 1 and out[0].end == 2.5 and out[1].start == 2.5
    assert cues[0].end == 3                       # pure: the input is untouched
    out, _m, fixed = tidy_timing([_c(0, 3, "Một.", locked=True), _c(2.5, 5, "Hai.")])
    assert fixed == 1 and out[0].end == 3 and out[1].start == 3


def test_ai_dialog_marks_the_unticked_rows_reviewed(view):
    from desktop import cuemodel

    view._apply_bulk([(0, "ngắn")], "x", reviewed=[2])
    assert cuemodel.is_reviewed(view.cues[2]) and view.cues[0].vi == "ngắn"
    view.undo_btn.click()
    assert not cuemodel.is_reviewed(view.cues[2])


def test_mark_rest_lists_only_unticked_rows(qapp):
    dlg = BulkEditDialog(None, title="t", intro="i", rows=[(0, "a", "b", ""), (3, "c", "d", "")],
                         checked=False, mark_rest="đánh dấu")
    dlg._rows[0][1].setChecked(True)
    assert dlg.unticked() == [3]
    dlg.mark_rest.setChecked(False)
    assert dlg.unticked() == []


def test_mark_rest_alone_can_be_applied(qapp):
    dlg = BulkEditDialog(None, title="t", intro="i", rows=[(0, "a", "b", "")], checked=False,
                         mark_rest="đánh dấu")
    assert dlg.apply_btn.isEnabled() and dlg.apply_btn.text() == "Đánh dấu đã xem"
    dlg.mark_rest.setChecked(False)
    assert not dlg.apply_btn.isEnabled()
