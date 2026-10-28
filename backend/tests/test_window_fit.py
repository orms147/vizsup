"""The window must fit the screen (2026-10-28 report: "phóng to" pushed the editor and the
render page wider than the display) and the preview must follow the default audio device
(headphones plugged in still played on the laptop speakers)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture()
def qapp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return QApplication.instance() or QApplication([])


def test_editor_can_shrink_below_its_rows(qapp):
    from desktop.views import editor_view

    v = editor_view.EditorView()
    # its rows need ~1310 px; inside a scroll area the page asks for almost nothing
    assert v.minimumSizeHint().width() < 900


def test_render_page_can_shrink_below_its_panes(qapp):
    from desktop.views import render_view

    v = render_view.RenderView()
    assert v.minimumSizeHint().width() < 900


def test_audio_output_follows_the_default_device(qapp):
    pytest.importorskip("PySide6.QtMultimedia")
    from PySide6.QtMultimedia import QAudioOutput, QMediaDevices

    from desktop.audio import follow_default_output

    out = QAudioOutput()
    watcher = follow_default_output(out)
    assert watcher is not None
    default = QMediaDevices.defaultAudioOutput()
    if not default.isNull():                  # a machine without audio hardware: nothing to pin
        assert out.device().id() == default.id()
    watcher.audioOutputsChanged.emit()        # plugging headphones in/out re-points the output
    if not default.isNull():
        assert out.device().id() == QMediaDevices.defaultAudioOutput().id()


def test_main_window_can_shrink_to_a_small_screen(qapp):
    """The window's minimum used to be the largest page's (1346 px wide, taller once scaled by
    the display DPI): on a smaller screen it could not shrink and ran off the display."""
    from desktop import main as desktop_main

    win = desktop_main.MainWindow()
    hint = win.minimumSizeHint()
    assert hint.width() < 1000 and hint.height() < 300      # the nav bar (~952 px) is all that is left


def test_video_priority_layout_gives_the_picture_the_room(qapp):
    """"Phóng to" could not enlarge the video: the editing panel keeps ~650 px, so the
    video pane stayed ~400 px. The video-priority layout now hides that panel."""
    from desktop.views import editor_view

    v = editor_view.EditorView()
    v.resize(1500, 800)
    v.show()
    QApplication.processEvents()
    v._apply_layout("balanced")
    QApplication.processEvents()
    balanced = v.top.sizes()[0]
    assert not v.inspector.isHidden()
    v._apply_layout("video")
    QApplication.processEvents()
    assert v.inspector.isHidden()
    assert v.top.sizes()[0] > balanced * 1.4              # the video pane really grew
    v._apply_layout("balanced")
    QApplication.processEvents()
    assert not v.inspector.isHidden()                     # and the panel comes back
    assert editor_view.split_sizes(1400, "video", 385)[2] == 0


def test_render_page_expand_hides_and_restores_the_options(qapp):
    from desktop.views import render_view

    v = render_view.RenderView()
    v.resize(1500, 800)
    v.show()
    QApplication.processEvents()
    assert not v._split.widget(0).isHidden()
    v.result.expandRequested.emit()                 # ⤢ on the result video
    assert v._split.widget(0).isHidden()
    v.preview.expandRequested.emit()                # ⤢ again (either video) brings them back
    assert not v._split.widget(0).isHidden()


def test_video_canvas_scales_smoothly(qapp):
    """The in-app preview drew frames nearest-neighbour: subtitle text looked jagged."""
    from PySide6.QtGui import QImage, QPainter

    from desktop.widgets.video_canvas import VideoCanvas

    seen = {}
    real = QPainter.setRenderHint

    def spy(self, hint, on=True):
        seen[hint] = on
        return real(self, hint, on)

    c = VideoCanvas()
    c.resize(400, 300)
    c._frame = QImage(1280, 720, QImage.Format_RGB32)
    c._frame.fill(0xFFFFFF)
    QPainter.setRenderHint = spy
    try:
        c.grab()
    finally:
        QPainter.setRenderHint = real
    assert seen.get(QPainter.SmoothPixmapTransform) is True
