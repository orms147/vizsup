"""CSV export / import of subtitles (wave 3, item 2): index,start,end,zh,vi in UTF-8 WITH a BOM; an
import matches rows by index, changes only the Vietnamese (times only on request, never on a
locked cue), as ONE undo step, and refuses a file of another video with the reason."""
from __future__ import annotations

import csv
import io
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.pipeline import subcsv  # noqa: E402


class C:
    def __init__(self, start, end, zh, vi, locked=False):
        self.start, self.end, self.zh, self.vi, self.locked = start, end, zh, vi, locked


def cues():
    return [C(1.0, 2.5, "你好", "Xin chào"), C(3.0, 4.25, "再见", "Tạm biệt,\nbạn nhé"),
            C(65.5, 67.0, "好的", "Được rồi — “ok”, \"vâng\"; nhé")]


# --- the file format ------------------------------------------------------------------

def test_export_is_utf8_with_bom_and_round_trips(tmp_path):
    p = subcsv.write_file(tmp_path / "x.csv", cues())
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                           # the BOM Excel needs
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[0] == ["index", "start", "end", "zh", "vi"]
    assert rows[1] == ["1", "00:00:01.000", "00:00:02.500", "你好", "Xin chào"]
    assert rows[3][1] == "00:01:05.500" and "“ok”" in rows[3][4] and '"vâng"' in rows[3][4]
    assert rows[2][4] == "Tạm biệt,\nbạn nhé"                         # a line break survives quoting
    parsed = subcsv.read_rows(subcsv.read_text(p))
    assert [r["vi"] for r in parsed] == [c.vi for c in cues()]
    assert subcsv.match(cues(), parsed) == parsed


def test_timestamps_parse_in_every_form_a_spreadsheet_may_leave():
    p = subcsv.parse_ts
    assert p("00:01:05.500") == 65.5 and p("01:05,5") == 65.5 and p("1:02:03.004") == 3723.004
    assert p("12.5") == 12.5 and p("12,5") == 12.5 and p("7") == 7.0
    assert p("") is None and p("abc") is None
    assert subcsv.fmt_ts(3723.004) == "01:02:03.004" and subcsv.fmt_ts(-1) == "00:00:00.000"


def test_semicolon_files_and_a_missing_bom_are_read(tmp_path):
    body = "index;start;end;zh;vi\r\n1;00:00:01.000;00:00:02.500;你好;Xin chào\r\n"
    f = tmp_path / "e.csv"
    f.write_bytes(body.encode("utf-8"))                               # no BOM
    rows = subcsv.read_rows(subcsv.read_text(f))
    assert rows[0]["vi"] == "Xin chào" and rows[0]["zh"] == "你好"
    f.write_bytes(("﻿" + body).encode("utf-8"))                  # BOM
    assert subcsv.read_rows(subcsv.read_text(f))[0]["index"] == 1


# --- refusing a file that is not this project's -----------------------------------------

def test_wrong_row_count_is_refused_with_the_numbers():
    rows = subcsv.read_rows(subcsv.export_text(cues()))
    with pytest.raises(subcsv.CsvError, match=r"File có 2 dòng nhưng dự án có 3 câu"):
        subcsv.match(cues(), rows[:2])


def test_changed_zh_column_is_refused_and_names_the_lines():
    other = cues()
    other[1].zh = "别的话"
    rows = subcsv.read_rows(subcsv.export_text(other))
    with pytest.raises(subcsv.CsvError, match=r"Cột zh khác dự án ở 1 câu \(câu 2\)"):
        subcsv.match(cues(), rows)
    # whitespace differences are not a mismatch
    spaced = cues()
    spaced[0].zh = "  你好 "
    assert subcsv.match(cues(), subcsv.read_rows(subcsv.export_text(spaced)))


def test_duplicate_unknown_index_and_missing_columns_are_refused():
    rows = subcsv.read_rows(subcsv.export_text(cues()))
    rows[1]["index"] = 1
    with pytest.raises(subcsv.CsvError, match="xuất hiện hai lần"):
        subcsv.match(cues(), rows)
    rows = subcsv.read_rows(subcsv.export_text(cues()))
    rows[2]["index"] = 9
    with pytest.raises(subcsv.CsvError, match="ngoài 1–3"):
        subcsv.match(cues(), rows)
    with pytest.raises(subcsv.CsvError, match="Thiếu cột: zh, vi"):
        subcsv.read_rows("index,start,end\n1,0,1\n")
    with pytest.raises(subcsv.CsvError, match="File trống"):
        subcsv.read_rows("﻿ \n")
    with pytest.raises(subcsv.CsvError, match="không phải số"):
        subcsv.read_rows("index,start,end,zh,vi\nx,0,1,a,b\n")


def test_rows_may_be_reordered_and_matched_by_index():
    rows = subcsv.read_rows(subcsv.export_text(cues()))
    rows.reverse()                                                    # a sort in the spreadsheet
    ordered = subcsv.match(cues(), rows)
    assert [r["index"] for r in ordered] == [1, 2, 3]


def test_changes_text_always_times_only_on_request_never_when_locked():
    cs = cues()
    rows = subcsv.read_rows(subcsv.export_text(cs))
    rows[0]["vi"] = "Xin chào bạn"
    rows[1]["start"] = "00:00:03.500"
    rows[2]["end"] = "00:01:09.000"
    ordered = subcsv.match(cs, rows)
    assert subcsv.changes(cs, ordered) == ([0], [])                  # times ignored
    assert subcsv.changes(cs, ordered, with_times=True) == ([0], [1, 2])
    cs[2].locked = True
    assert subcsv.changes(cs, ordered, with_times=True) == ([0], [1])
    ordered[1]["end"] = "00:00:03.100"                               # ends before it starts
    with pytest.raises(subcsv.CsvError, match="Câu 2: thời gian kết thúc"):
        subcsv.changes(cs, ordered, with_times=True)
    ordered[1]["end"] = "bad"
    with pytest.raises(subcsv.CsvError, match="Câu 2: thời gian"):
        subcsv.changes(cs, ordered, with_times=True)


# --- the editor ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from desktop.cuemodel import EditorCue
    from desktop.views.editor_view import EditorView

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = EditorView()
    v.cues = [EditorCue(start=1.0, end=2.5, vi="Xin chào", zh="你好"),
              EditorCue(start=3.0, end=4.25, vi="Tạm biệt", zh="再见"),
              EditorCue(start=5.0, end=6.0, vi="Được rồi", zh="好的")]
    v._reload(dirty=False)
    yield v
    v.close()


def test_menu_offers_export_and_import(view):
    texts = [a.text() for a in view.cue_list.more_btn.menu().actions()]
    assert any(t.startswith("Xuất bảng CSV") for t in texts)
    assert any(t.startswith("Nhập lại bảng CSV") for t in texts)


def test_export_then_import_a_proof_read_file_is_one_undo_step(view, tmp_path):
    out = view.export_csv_to(tmp_path / "ep.csv")
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    text = out.read_bytes().decode("utf-8-sig")
    text = text.replace("Xin chào", "Chào bạn").replace("Được rồi", "Được thôi")   # "proof-read"
    ordered, texts, times = view.plan_csv_import(text)
    assert texts == [0, 2] and times == []
    before_stack = len(view._undo_stack)
    n = view.apply_csv_import(ordered, texts, times, with_times=False)
    assert n == 2 and [c.vi for c in view.cues] == ["Chào bạn", "Tạm biệt", "Được thôi"]
    assert len(view._undo_stack) == before_stack + 1                   # ONE step
    assert [(c.start, c.end) for c in view.cues] == [(1.0, 2.5), (3.0, 4.25), (5.0, 6.0)]
    view._undo_action()
    assert [c.vi for c in view.cues] == ["Xin chào", "Tạm biệt", "Được rồi"]


def test_times_only_when_asked_and_locked_cues_keep_theirs(view, tmp_path):
    text = view.export_csv_to(tmp_path / "ep.csv").read_bytes().decode("utf-8-sig")
    text = text.replace("00:00:03.000", "00:00:03.200").replace("00:00:05.000", "00:00:05.500")
    view.cues[2].locked = True
    ordered, texts, times = view.plan_csv_import(text, with_times=True)
    assert texts == [] and times == [1]                                # row 3 is locked
    assert view.apply_csv_import(ordered, texts, times, with_times=False) == 0   # unticked: nothing
    assert view.cues[1].start == 3.0
    assert view.apply_csv_import(ordered, texts, times, with_times=True) == 1
    assert view.cues[1].start == 3.2 and view.cues[2].start == 5.0


def test_a_file_of_another_video_changes_nothing_and_says_why(view, tmp_path):
    text = view.export_csv_to(tmp_path / "ep.csv").read_bytes().decode("utf-8-sig")
    stack = len(view._undo_stack)
    with pytest.raises(subcsv.CsvError, match="Cột zh khác"):
        view.plan_csv_import(text.replace("你好", "别的"))
    view.cues.pop()
    with pytest.raises(subcsv.CsvError, match="File có 3 dòng nhưng dự án có 2 câu"):
        view.plan_csv_import(text)
    assert len(view._undo_stack) == stack and view.cues[0].vi == "Xin chào"


def test_import_dialog_summarises_and_gates_the_button(qapp):
    from desktop.widgets.dialogs import CsvImportDialog

    d = CsvImportDialog(None, texts=4, times=2, total=10)
    assert d.with_times.isEnabled() and not d.with_times.isChecked() and d.ok_btn.isEnabled()
    none = CsvImportDialog(None, texts=0, times=0, total=10)
    assert not none.ok_btn.isEnabled() and not none.with_times.isEnabled()
    only_times = CsvImportDialog(None, texts=0, times=3, total=10, time_problem="")
    assert only_times.ok_btn.isEnabled()
