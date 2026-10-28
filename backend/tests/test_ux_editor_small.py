"""Editor on a DPI-scaled laptop (logical ≈ 1133×667), real-window report 2026-10-29:
 3. the cue list's filter chip was squeezed to "ần xem lạ" (chip + "Theo video" + the density
    button did not fit the 320 px column) → the chip is the short "Xem lại" and the row fits;
 4. the timeline panel showed only its header: the video picture insisted on 300 px of height, so
    the editor page scrolled and the waveform and cue lane fell below the window → the picture may
    shrink, the timeline keeps its waveform + cue lane in view."""
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
    saved = (app.font(), app.styleSheet(),
             {k: app.property(k) for k in ("vz_sans", "vz_mono", "vz_cjk")})
    load_fonts(app)
    yield app
    app.setFont(saved[0])
    app.setStyleSheet(saved[1])
    for k, val in saved[2].items():
        app.setProperty(k, val)


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _cues(n=40):
    from desktop.cuemodel import EditorCue

    return [EditorCue(start=i * 4.0, end=i * 4.0 + 3.0, vi=f"câu {i}", zh="一") for i in range(n)]


def test_filter_row_fits_the_narrowest_cue_list(qapp, storage):
    from desktop.theme import qss
    from desktop.widgets.cue_list import CueList

    qapp.setStyleSheet(qss())
    w = CueList()
    w.load(_cues(), [["Quá dài"] if i % 2 == 0 else [] for i in range(40)])
    w.resize(320, 500)                                           # the column's minimum (editor_view)
    w.show()
    for _ in range(4):
        qapp.processEvents()
    chip, seg = w.f_review, w.f_review.parentWidget()
    assert chip.width() >= chip.sizeHint().width(), (chip.width(), chip.sizeHint().width())
    assert seg.width() >= seg.sizeHint().width(), (seg.width(), seg.sizeHint().width())
    assert w.pending_badge.isVisible() and w.pending_badge.text() == "20"
    # and nothing of the row spills past the column
    for b in (w.follow, w.density_btn):
        assert b.mapTo(w, b.rect().bottomRight()).x() <= w.width(), b.toolTip()
    assert chip.text() == "Xem lại"
    w.close()


@pytest.mark.parametrize("height", [600, 667])
def test_the_timeline_keeps_its_wave_and_cue_lane_on_a_short_window(qapp, tmp_path, monkeypatch,
                                                                    height):
    from desktop import main as m
    from desktop.theme import qss
    from desktop.widgets.timeline import TOTAL_H

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    qapp.setStyleSheet(qss())
    win = m.MainWindow()
    win.resize(1133, height)
    win.show()
    win.stack.setCurrentIndex(m.EDITOR)
    ed = win.editor
    ed.cues = _cues(60)
    ed._reload(dirty=False)
    ed.timeline.set_duration(240.0)
    ed._on_dims(1922, 1080)
    for _ in range(10):
        qapp.processEvents()
    assert ed._scroll.verticalScrollBar().maximum() == 0, "the editor page scrolls vertically"
    assert win.stack.currentWidget().verticalScrollBar().maximum() == 0
    tl = ed.timeline_panel
    vp = tl.scroll.viewport()
    assert vp.height() >= TOTAL_H - 4, (vp.height(), TOTAL_H)       # ruler + waveform + cue lane
    lane = ed.timeline._block_rect(ed.cues[0])
    bottom = ed.timeline.mapTo(win, lane.bottomLeft().toPoint()).y()
    assert bottom <= win.height() - 29, (bottom, win.height())      # above the status bar
    win.close()
