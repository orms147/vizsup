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
