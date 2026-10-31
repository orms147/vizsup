"""2026-11-01: "Dựng lại" on a project whose output.mp4 was shown in the render page's result
pane failed at the very end — PermissionError [WinError 5] output.rendering.mp4 → output.mp4:
the pane's player held output.mp4 (Windows does not let a file a player has open be replaced)
and the render only paused it. Starting a render now unloads the pane."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_starting_a_render_lets_go_of_the_shown_output(qapp, monkeypatch):
    from desktop.views.render_view import RenderView

    v = RenderView()
    calls: list[str] = []
    monkeypatch.setattr(v.result, "unload", lambda: calls.append("unload"))
    monkeypatch.setattr(v.result, "pause", lambda: calls.append("pause"))
    v._start_progress({"audio_mode": "replace"})
    assert calls == ["unload"]                   # released, not merely paused
    v._tick.stop()
