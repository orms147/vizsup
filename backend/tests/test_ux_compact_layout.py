"""Compact cue list + 9:16-aware editor layout (UX audit E1, E5/R3): the default "Gọn" rows show
about twice the lines at 768 px high without dropping the Chinese, the flag or the times, the
density is remembered, and a vertical video no longer keeps a landscape-sized column."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    saved = (app.font(), {k: app.property(k) for k in ("vz_sans", "vz_mono", "vz_cjk")})
    load_fonts(app)
    yield app
    app.setFont(saved[0])
    for k, val in saved[1].items():
        app.setProperty(k, val)


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _cues(n=40):
    from desktop.cuemodel import EditorCue

    return [EditorCue(start=i * 4.0, end=i * 4.0 + 3.0, vi=f"Anh có biết vì sao cô ấy bỏ đi không {i}?",
                      zh="你知道她为什么离开吗？") for i in range(n)]


# --- split_sizes ----------------------------------------------------------------------

def test_vertical_video_gives_its_spare_width_to_the_list():
    from desktop.views.editor_view import INSPECTOR_MAX, LIST_MAX, VIDEO_MIN, split_sizes

    base = split_sizes(1330, "balanced", 390)
    v, lst, ins = split_sizes(1330, "balanced", 390, aspect=9 / 16, pic_h=310)
    assert v + lst + ins == 1330
    assert VIDEO_MIN <= v < base[0] and lst > base[1]
    assert ins >= 390
    # a wide screen: the list stops at its cap, the inspector grows to its cap, the rest is video
    v, lst, ins = split_sizes(1880, "balanced", 390, aspect=9 / 16, pic_h=620)
    assert v + lst + ins == 1880 and lst <= LIST_MAX and ins <= INSPECTOR_MAX


def test_landscape_unknown_aspect_and_video_priority_are_unchanged():
    from desktop.views.editor_view import split_sizes

    base = split_sizes(1330, "balanced", 390)
    assert split_sizes(1330, "balanced", 390, aspect=16 / 9, pic_h=310) == base
    assert split_sizes(1330, "balanced", 390, aspect=0.0, pic_h=310) == base
    assert split_sizes(1330, "balanced", 390, aspect=9 / 16, pic_h=0) == base
    assert split_sizes(1400, "video", 385, aspect=9 / 16, pic_h=310) == \
        split_sizes(1400, "video", 385)                        # "ưu tiên video" keeps its promise


def test_editor_narrows_the_video_column_when_it_learns_the_video_is_vertical(qapp, storage):
    from desktop.theme import qss
    from desktop.views.editor_view import EditorView

    prev = qapp.styleSheet()
    qapp.setStyleSheet(qss())              # the inspector's minimum width depends on the styling
    v = EditorView()                       # (it must be in place before the widgets are built)
    v.cues = _cues()
    v._reload(dirty=False)
    v.resize(1366, 700)
    v.show()
    for _ in range(4):
        qapp.processEvents()
    before = v.top.sizes()
    v._on_dims(540, 960)
    after = v.top.sizes()
    assert after[0] < before[0] and after[1] > before[1]
    v._on_dims(1920, 1080)                                     # a landscape clip: back to the ratio
    assert v.top.sizes()[0] == before[0]
    v._user_split = True                                       # the user dragged a handle: hands off
    v._on_dims(540, 960)
    assert v.top.sizes()[0] == before[0]
    v.close()
    qapp.setStyleSheet(prev)


# --- compact rows ---------------------------------------------------------------------

def _list(qapp, storage, compact):
    from desktop.widgets.cue_list import CueList

    w = CueList()
    w.resize(475, 400)
    w.load(_cues(), [["Quá dài"] if i % 5 == 0 else [] for i in range(40)])
    w.set_compact(compact)
    w.show()
    for _ in range(3):
        qapp.processEvents()
    return w


def _rows_in_view(w) -> float:
    d, h, n = w._delegate, 0, 0
    for i in range(w.model.rowCount()):
        rh = d.sizeHint(None, w.model.index(i)).height()
        if h + rh > w.view.viewport().height():
            return n + (w.view.viewport().height() - h) / rh
        h += rh
        n += 1
    return float(n)


def test_compact_rows_fit_about_twice_the_lines(qapp, storage):
    wide = _list(qapp, storage, False)
    compact = _list(qapp, storage, True)
    ratio = _rows_in_view(compact) / _rows_in_view(wide)
    assert ratio >= 1.6, ratio
    ix = compact.model.index(1)
    assert compact._delegate.sizeHint(None, ix).height() <= 48
    wide.close()
    compact.close()


def test_compact_row_still_paints_zh_vi_flag_and_time(qapp, storage):
    from PySide6.QtGui import QColor, QImage

    from desktop.theme import V2

    w = _list(qapp, storage, True)
    w.view.setCurrentIndex(w.model.index(3))             # row 0 is flagged and not selected
    for _ in range(2):
        qapp.processEvents()
    img: QImage = w.view.viewport().grab().toImage()
    amber = QColor(V2["amber"])
    edge = img.pixelColor(1, 10)                         # first row, left edge: the pending bar
    assert (edge.red(), edge.green(), edge.blue()) == (amber.red(), amber.green(), amber.blue())
    # text lands in every region: the VI, the flag under it, the time column (the Chinese needs a
    # CJK font, which the offscreen platform may not have: its line is checked through the height)
    def ink(x0, y0, x1, y1):
        bg = img.pixelColor(x0, y0)
        return any(img.pixelColor(x, y) != bg for x in range(x0, x1) for y in range(y0, y1))
    wv = img.width()
    assert ink(40, 5, 200, 22)                           # VI
    assert ink(wv - 160, 24, wv - 80, 38)                # the flag label ("Quá dài"), amber
    assert ink(wv - 70, 5, wv - 12, 20)                  # start time
    assert ink(wv - 70, 21, wv - 12, 36)                 # duration
    d, ix = w._delegate, w.model.index(1)
    with_zh = d.sizeHint(None, ix).height()
    w.model.cues[1].zh = ""
    assert d.sizeHint(None, ix).height() < with_zh               # the Chinese line is really there
    w.close()


def test_density_toggle_is_remembered(qapp, storage):
    from desktop import store
    from desktop.widgets.cue_list import CueList

    w = CueList()
    assert w.compact()                                   # the default
    w.density_btn.click()
    assert not w.compact()
    assert store.load_ui_state()["cue_density"] == "wide"
    again = CueList()
    assert not again.compact()                           # remembered by the next editor
    again.density_btn.click()
    assert store.load_ui_state()["cue_density"] == "compact"
