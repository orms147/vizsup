"""⤢ / F on the render page puts that video alone on the whole screen and Esc / F bring it
back — it used to only hide the options column behind a "Toàn màn hình" tooltip (2026-11-01)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def rv(qapp, monkeypatch):
    from desktop.views import render_view

    monkeypatch.setattr(render_view, "_separation_available", lambda: False)
    v = render_view.RenderView()
    v.resize(1300, 860)
    v.show()
    yield v
    v._fs.exit()
    v.close()


def test_expand_moves_the_video_to_its_own_window_and_back(rv, qapp):
    panel = rv.result
    home = panel.parentWidget()
    panel.expandRequested.emit()
    qapp.processEvents()
    assert rv._fs.active()
    assert panel.window() is not rv.window()                # its own full-screen window
    assert rv._fs.key(Qt.Key_Escape)                        # Esc comes back
    qapp.processEvents()
    assert not rv._fs.active()
    assert panel.parentWidget() is home and panel.window() is rv.window()


def test_the_source_preview_too_and_f_toggles(rv, qapp):
    panel = rv.preview
    home = panel.parentWidget()
    panel.expandRequested.emit()
    qapp.processEvents()
    assert rv._fs.active() and panel.window() is not rv.window()
    assert rv._fs.key(Qt.Key_F)
    qapp.processEvents()
    assert panel.parentWidget() is home
    assert not rv._fs.key(Qt.Key_A)                         # other keys are not taken


def test_every_video_has_a_seek_bar_and_a_click_jumps(qapp):
    """The render result and the editor's source video had no seek bar (2026-11-01)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from desktop.widgets.video_panel import VideoPanel

    p = VideoPanel("Bản tiếng Việt")
    p.resize(900, 600)
    p.show()
    qapp.processEvents()
    bar = p._scrub
    assert bar.isVisibleTo(p)
    bar.setRange(0, 10_000)
    jumped = []
    bar.sliderMoved.connect(jumped.append)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, QPoint(int(bar.width() * 0.75), bar.height() // 2))
    assert jumped and abs(jumped[0] - 7_500) < 600            # jumps where clicked, not a page step
    p.close()
