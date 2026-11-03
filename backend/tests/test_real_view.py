"""Render page «Xem thật»: the preview shows the exact frame of the render, refreshed on a change."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ffmpegutil import ffmpeg_bin  # noqa: E402
from app.models import Cue, Job  # noqa: E402
from app.pipeline.srt import write_srt  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait(cond, qapp, secs=30):
    end = time.time() + secs
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.05)
    return cond()


def test_the_render_page_shows_the_real_frame_and_cuts_it_again_on_a_change(qapp, tmp_path, monkeypatch):
    from desktop import store
    from desktop.views.render_view import RenderView

    monkeypatch.setattr(store, "update_ui_state", lambda **k: None)
    monkeypatch.setattr(store, "load_ui_state", lambda: {"render_real_view": False})
    j = Job(id="rv", work_dir=tmp_path / "job")
    j.work_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x336699:s=640x360:d=4:r=25", "-pix_fmt", "yuv420p",
                    str(j.work_dir / "source.mp4")], check=True, capture_output=True)
    write_srt([Cue(index=1, start=0.0, end=4.0, text="Xin chào")], j.vi_srt)

    rv = RenderView()
    rv.show()
    rv._job = j
    rv.set_style({}, 20, "Be Vietnam Pro")
    from types import SimpleNamespace as NS

    rv.set_preview(j.source_video, [NS(start=0.0, end=4.0, vi="Xin chào")])
    assert rv.preview._real_btn.isVisibleTo(rv.preview)
    rv.preview.set_real_view(True)
    assert _wait(lambda: rv.preview._canvas.has_still() and not rv._real_busy, qapp)
    first = rv._real_key
    rv.frame.setCurrentIndex(rv.frame.findData("tiktok"))            # another option → a new cut
    assert _wait(lambda: rv._real_key != first and not rv._real_busy, qapp)
    img = rv.preview._canvas._still
    assert img is not None and img.height() > img.width()            # the 9:16 composition
    rv.preview.set_real_view(False)
    assert not rv.preview._canvas.has_still()
