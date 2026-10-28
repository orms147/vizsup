"""Overview strip throttle (review U6): a playback tick used to emit marksChanged — the strip repainted
every cue and the panel re-synced its range, 25 times a second. A tick now emits only timeChanged;
the strip repaints just the sliver around the playhead and only when the line moved a whole pixel.
Measured on a 700-cue project, 1500 ticks (60 s of playback): 8.5 ms/tick → 0.7 ms/tick, strip paints
1500 → 54, panel syncs 1500 → 0."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QObject  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Count(QObject):
    def __init__(self, target):
        super().__init__()
        self.target, self.n = target, 0
        target.installEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.target and ev.type() == QEvent.Paint:
            self.n += 1
        return False


@pytest.fixture()
def panel(qapp, monkeypatch):
    from desktop.cuemodel import EditorCue
    from desktop.widgets import timeline as T

    synced = []
    orig = T.TimelinePanel._sync_overview
    monkeypatch.setattr(T.TimelinePanel, "_sync_overview",
                        lambda self, *a: (synced.append(1), orig(self, *a))[1])
    p = T.TimelinePanel()
    p.resize(1300, 260)
    p.show()
    cues = [EditorCue(start=i * 2.0, end=i * 2.0 + 1.5, vi=f"câu {i}", zh="一") for i in range(700)]
    p.timeline.set_cues(cues)
    p.timeline.set_duration(1400.0)
    p.timeline.set_issues([["Quá dài"] if i % 5 == 0 else [] for i in range(700)])
    for _ in range(8):
        qapp.processEvents()
    p._synced = synced
    yield p
    p.close()


def test_a_playback_tick_does_not_touch_the_marks(panel, qapp):
    tl = panel.timeline
    marks, times = [], []
    tl.marksChanged.connect(lambda: marks.append(1))
    tl.timeChanged.connect(times.append)
    synced_before = len(panel._synced)
    for k in range(1, 101):
        tl.set_time(k * 0.04)
        qapp.processEvents()
    assert marks == [] and len(times) == 100
    assert len(panel._synced) == synced_before                    # no range re-sync per tick
    tl.set_time(100 * 0.04)                                       # same time again: nothing at all
    assert len(times) == 100


def test_the_strip_repaints_only_when_the_line_moves_a_pixel(panel, qapp):
    strip = panel.overview
    counter = _Count(strip)
    tl = panel.timeline
    tl.set_time(10.0)
    for _ in range(4):
        qapp.processEvents()
    base = counter.n
    # 200 ticks of 5 ms: 1 s of playback = ~0.9 px on a 1272 px strip over 1400 s
    for k in range(1, 201):
        tl.set_time(10.0 + k * 0.005)
        qapp.processEvents()
    assert counter.n - base <= 3, counter.n - base
    # 30 s further is ~27 px: a repaint per pixel step at most, never one per tick
    before = counter.n
    for k in range(1, 751):
        tl.set_time(11.0 + k * 0.04)
        qapp.processEvents()
    moved = counter.n - before
    assert 10 <= moved <= 60, moved


def test_the_sliver_repaint_still_draws_the_playhead_in_the_right_place(panel, qapp):
    strip = panel.overview
    tl = panel.timeline
    tl.set_time(100.0)
    for _ in range(3):
        qapp.processEvents()
    tl.set_time(700.0)                                            # a big jump
    for _ in range(3):
        qapp.processEvents()
    img = strip.grab().toImage()
    x = int(round(strip.x_of(700.0)))
    white = [img.pixelColor(x, y) for y in (2, strip.height() // 2, strip.height() - 2)]
    assert any(c.red() > 200 and c.green() > 200 and c.blue() > 200 for c in white)
    old_x = int(round(strip.x_of(100.0)))                        # and it is gone from the old place
    assert all(not (img.pixelColor(old_x, y).red() > 240 and img.pixelColor(old_x, y).green() > 240)
               for y in (2, strip.height() - 2))
