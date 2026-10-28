"""Editor navigation (2026-10-28 requests): ↑/↓ move between lines, ←/→ jump 10 s
like a video player, F / ⤢ shows the video full screen (Esc back, same keys there),
and pressing on the timeline outside a line and dragging scrubs the playhead."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, monkeypatch):
    from desktop.cuemodel import EditorCue
    from desktop.views.editor_view import EditorView

    v = EditorView()
    v.cues = [EditorCue(start=float(i * 5), end=float(i * 5 + 3), vi=f"câu {i}", zh="一")
              for i in range(6)]
    v._reload(dirty=False)
    t = {"now": 0.0}
    monkeypatch.setattr(v.video, "current_time", lambda: t["now"])
    monkeypatch.setattr(v.video, "duration", lambda: 30.0)
    monkeypatch.setattr(v.video, "seek_seconds", lambda s: t.__setitem__("now", s))
    v.show()
    qapp.processEvents()
    yield v, t
    if v.is_fullscreen():
        v.toggle_fullscreen()
    v.close()


def test_arrows_move_between_lines_and_jump_ten_seconds(view):
    v, t = view
    t["now"] = 11.0                       # inside line 2 (10–13 s), nothing selected
    v._sel = -1
    v.step_cue(+1)
    assert v._sel == 3 and t["now"] == 15.0
    v.step_cue(-1)
    assert v._sel == 2 and t["now"] == 10.0
    for _ in range(10):
        v.step_cue(+1)
    assert v._sel == 5                    # stops at the last line
    t["now"] = 12.0
    v.seek_by(+10)
    assert t["now"] == 22.0
    v.seek_by(+10)
    assert t["now"] == 30.0               # clamped to the video
    v.seek_by(-100)
    assert t["now"] == 0.0


def test_full_screen_and_back_keeps_the_layout(view, qapp):
    v, t = view
    sizes = v.top.sizes()
    v.toggle_fullscreen()
    assert v.is_fullscreen() and v.video.window() is v._fs and v.top.indexOf(v.video) == -1
    host = v._fs
    host.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
    assert t["now"] == 10.0                # the keys work in full screen too
    host.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    qapp.processEvents()
    assert not v.is_fullscreen() and v.top.indexOf(v.video) == 0
    assert v.video.window() is v.window()
    assert abs(sum(v.top.sizes()) - sum(sizes)) <= 2


def test_dragging_on_the_timeline_scrubs(view, qapp):
    v, _t = view
    tl = v.timeline
    seen = []
    tl.seeked.connect(seen.append)
    y = 5.0                                # the ruler: no line block there

    def ev(kind, x, buttons):
        return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.LeftButton, buttons,
                           Qt.NoModifier)

    tl.mousePressEvent(ev(QEvent.MouseButtonPress, 100, Qt.LeftButton))
    tl.mouseMoveEvent(ev(QEvent.MouseMove, 200, Qt.LeftButton))
    tl.mouseMoveEvent(ev(QEvent.MouseMove, 300, Qt.LeftButton))
    tl.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, 300, Qt.NoButton))
    tl.mouseMoveEvent(ev(QEvent.MouseMove, 400, Qt.NoButton))      # released: no more
    assert len(seen) == 3 and seen == sorted(seen) and seen[0] > 0
