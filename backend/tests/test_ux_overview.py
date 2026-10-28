"""Whole-episode overview strip (UX audit E6): one thin bar above the zoomed timeline with a tick
per cue — amber = flagged and still to look at, green = flagged and marked "Đã xem" — the
viewport rectangle and the playhead; a press jumps there; it hides when the zoomed view already
shows the whole clip."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, tmp_path, monkeypatch):
    from desktop.cuemodel import EditorCue
    from desktop.views.editor_view import EditorView

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = EditorView()
    v.cues = [EditorCue(start=i * 10.0, end=i * 10.0 + 4.0, vi=f"câu {i}", zh="一")
              for i in range(60)]                                  # a 10-minute episode
    v._reload(dirty=False)
    v.timeline.set_duration(600.0)
    v.cue_list.set_issues([["Quá dài"] if i in (3, 20, 40) else [] for i in range(60)])
    v.timeline.set_issues([["Quá dài"] if i in (3, 20, 40) else [] for i in range(60)])
    v.resize(1300, 800)
    v.show()
    for _ in range(4):
        qapp.processEvents()
    yield v
    v.close()


def _px(strip, t, frac=0.1):
    img = strip.grab().toImage()
    x = int(strip.x_of(t)) + 1
    y = int(strip.height() - 4 - 2)
    return img.pixelColor(x, y)


def _same(c: QColor, hex_: str, tol=3) -> bool:
    o = QColor(hex_)
    return abs(c.red() - o.red()) <= tol and abs(c.green() - o.green()) <= tol \
        and abs(c.blue() - o.blue()) <= tol


def test_strip_is_shown_for_a_long_episode_and_hidden_for_a_short_one(view, qapp):
    strip = view.timeline_panel.overview
    assert not strip.isHidden()
    view.timeline.set_duration(20.0)                       # the zoomed view shows it all
    view.cues = view.cues[:2]
    view.timeline.set_cues(view.cues)
    qapp.processEvents()
    assert strip.isHidden()


def test_ticks_say_pending_seen_and_nothing_to_check(view, qapp):
    from desktop import cuemodel
    from desktop.theme import V2

    strip = view.timeline_panel.overview
    assert _same(_px(strip, 3 * 10.0 + 1), V2["amber"])               # flagged, pending
    assert _same(_px(strip, 7 * 10.0 + 1), V2["purple_line"])         # nothing to check
    view.cue_list.selectRow(3)
    view._mark_reviewed()                                             # → "Đã xem"
    qapp.processEvents()
    assert cuemodel.is_reviewed(view.cues[3])
    assert _same(_px(strip, 3 * 10.0 + 1), V2["green"], tol=60)       # redrawn green (alpha)
    assert not _same(_px(strip, 3 * 10.0 + 1), V2["amber"])
    assert _same(_px(strip, 40 * 10.0 + 1), V2["amber"])              # another still pending
    assert _same(_px(strip, 20 * 10.0 + 1), V2["purple_bright"])      # and the one jumped to


def test_press_jumps_the_video_and_recentres_the_zoomed_view(view, qapp):
    strip = view.timeline_panel.overview
    seen = []
    view.timeline.seeked.connect(seen.append)
    x = strip.x_of(300.0)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, 10), strip.mapToGlobal(QPointF(x, 10)),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    strip.mousePressEvent(ev)
    assert len(seen) == 1 and abs(seen[0] - 300.0) < 1.0
    a, b = strip.view()
    assert a <= 300.0 <= b                                 # the viewport rectangle moved there
    # dragging keeps following; a right press does nothing
    seen.clear()
    mv = QMouseEvent(QEvent.MouseMove, QPointF(strip.x_of(400.0), 10),
                     strip.mapToGlobal(QPointF(strip.x_of(400.0), 10)), Qt.NoButton,
                     Qt.LeftButton, Qt.NoModifier)
    strip.mouseMoveEvent(mv)
    assert seen and abs(seen[-1] - 400.0) < 1.0
    strip.mouseReleaseEvent(mv)
    seen.clear()
    rp = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, 10), strip.mapToGlobal(QPointF(x, 10)),
                     Qt.RightButton, Qt.RightButton, Qt.NoModifier)
    strip.mousePressEvent(rp)
    assert not seen


def test_viewport_rectangle_follows_scroll_and_zoom(view, qapp):
    strip = view.timeline_panel.overview
    panel = view.timeline_panel
    a0, b0 = strip.view()
    panel.ensure_visible(450.0, center=True)
    qapp.processEvents()
    a1, b1 = strip.view()
    assert a1 > a0 and a1 <= 450.0 <= b1
    panel.timeline.set_zoom(panel.timeline.pps() * 2)
    qapp.processEvents()
    a2, b2 = strip.view()
    assert (b2 - a2) < (b1 - a1) * 0.7                      # zoomed in: a narrower rectangle
