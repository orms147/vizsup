"""CSV formula guard (review U1): a Vietnamese line starting with - = + @ ("- Anh đi đâu?") is a
formula to Excel / Google Sheets on open. Export guards such cells with a leading apostrophe;
import strips exactly that guard (only an apostrophe followed by one of those four characters)."""
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


LINES = ["- Anh đi đâu?", "= tổng", "+84 912 345 678", "@kenh", "Bình thường", "'đã có dấu nháy",
         "'- chính nó bắt đầu bằng nháy", "-", "  - có khoảng trắng", "", "a - b"]


def cues():
    return [C(i * 2.0, i * 2.0 + 1.5, "你好", vi) for i, vi in enumerate(LINES)]


def test_export_guards_formula_starts_and_only_those():
    text = subcsv.export_text(cues())
    rows = list(csv.reader(io.StringIO(text.lstrip(subcsv.BOM), newline="")))[1:]
    vi = [r[4] for r in rows]
    assert vi[0] == "'- Anh đi đâu?" and vi[1] == "'= tổng" and vi[2] == "'+84 912 345 678"
    assert vi[3] == "'@kenh" and vi[7] == "'-"
    assert vi[4] == "Bình thường" and vi[5] == "'đã có dấu nháy"     # an apostrophe alone: untouched
    assert vi[6] == "''- chính nó bắt đầu bằng nháy"                 # already looks guarded → guarded
    assert vi[8] == "  - có khoảng trắng" and vi[10] == "a - b"      # not at the start: untouched
    assert vi[9] == ""


def test_round_trip_gives_back_every_line_exactly():
    parsed = subcsv.read_rows(subcsv.export_text(cues()))
    assert [r["vi"] for r in parsed] == LINES
    assert [r["zh"] for r in parsed] == ["你好"] * len(LINES)
    assert subcsv.match(cues(), parsed)                              # and it is still this project's


def test_import_strips_only_the_guard():
    assert subcsv.unguard("'- x") == "- x" and subcsv.unguard("'=1") == "=1"
    assert subcsv.unguard("'+1") == "+1" and subcsv.unguard("'@a") == "@a"
    assert subcsv.unguard("'hello") == "'hello" and subcsv.unguard("'") == "'"
    assert subcsv.unguard("- x") == "- x" and subcsv.unguard("''- x") == "'- x"
    # a sheet that dropped the guard (Sheets exports the visible text) still imports fine
    rows = subcsv.read_rows("index,start,end,zh,vi\n1,00:00:00.000,00:00:01.000,你好,- Anh đi đâu?\n")
    assert rows[0]["vi"] == "- Anh đi đâu?"


def test_a_chinese_column_starting_with_a_dash_still_matches():
    cs = [C(0.0, 1.0, "- 你好", "Xin chào"), C(2.0, 3.0, "=好", "Ừ")]
    parsed = subcsv.read_rows(subcsv.export_text(cs))
    assert subcsv.match(cs, parsed)
    assert parsed[0]["zh"] == "- 你好" and parsed[1]["zh"] == "=好"


@pytest.mark.parametrize("text", LINES)
def test_guard_unguard_are_inverse(text):
    assert subcsv.unguard(subcsv.guard(text)) == text
