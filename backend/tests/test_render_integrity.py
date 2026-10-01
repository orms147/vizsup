"""N-13: render integrity — the burned subtitles must match the snapshot the
user approved (even if vi.srt changes mid-render), and the render screen must
never send its "(mặc định)" placeholder as a real TTS voice id.

Offscreen Qt tests; skipped where PySide6 is absent (base-deps CI).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

pytest.importorskip("PySide6.QtWidgets")
worker_mod = pytest.importorskip("desktop.worker")

from app.models import Job  # noqa: E402
from app.pipeline import assemble as assemble_mod  # noqa: E402
from app.pipeline import tts as tts_mod  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_run_render_forwards_burn_cues_snapshot(qapp, tmp_path, monkeypatch):
    job = Job(id="t", work_dir=tmp_path / "job")
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nphiên bản ĐÃ DUYỆT\n",
                          encoding="utf-8")
    from app.pipeline import approval

    approval.approve(job)                                  # the gate: only approved revisions render
    snapshot = object()                                    # sentinel travels untouched
    seen: dict = {}

    def fake_assemble(j, segments, **kw):
        seen.update(kw)
        j.output_video.write_bytes(b"o")
        return j.output_video

    monkeypatch.setattr(tts_mod, "synthesize", lambda j, **kw: [])
    monkeypatch.setattr(assemble_mod, "assemble", fake_assemble)

    failures: list[str] = []
    w = worker_mod.PipelineWorker(job, {"burn_cues": snapshot, "tts": "edge"})
    w.failed.connect(failures.append)
    w.run_render()
    assert failures == []
    assert seen["burn_cues"] is snapshot                   # exact object, not re-parsed


def test_render_view_voice_placeholder_maps_to_none(qapp):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    got: list[dict] = []
    rv.renderRequested.connect(got.append)

    rv.set_voices([])                                      # provider lists no voices
    rv._emit_render()
    assert got[-1]["voice"] is None                        # NOT the literal "(mặc định)"

    rv.set_voices(["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"])
    rv._emit_render()
    assert got[-1]["voice"] == "vi-VN-HoaiMyNeural"        # real voices pass through


def test_render_view_lock_toggles_controls(qapp):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    rv.set_rendering(True)
    assert not rv.render_btn.isEnabled()
    assert not rv.back_btn.isEnabled()
    assert rv.cancel_btn.isVisibleTo(rv) and rv.cancel_btn.isEnabled()
    rv.set_rendering(False)                                # every exit path unlocks
    assert rv.render_btn.isEnabled()
    assert rv.back_btn.isEnabled()
    assert not rv.cancel_btn.isVisibleTo(rv)


def test_render_view_locked_until_the_current_revision_is_approved(qapp):
    """v2: an edit after approval re-locks Dựng video — the primary button asks for
    approval instead of rendering (the worker would refuse anyway)."""
    from desktop.views.render_view import RenderView

    rv = RenderView()
    renders, approvals = [], []
    rv.renderRequested.connect(renders.append)
    rv.approveRequested.connect(lambda: approvals.append(1))
    rv.set_approved(False)
    rv.render_btn.click()
    assert approvals == [1] and renders == []
    rv.set_approved(True)
    rv.render_btn.click()
    assert len(renders) == 1
    assert renders[0]["audio_mode"] == "replace"


def test_render_view_reopens_a_finished_project_on_its_result(qapp, tmp_path):
    from app.models import Job
    from desktop.views.render_view import RenderView

    job = Job(id="done", work_dir=tmp_path / "done")
    job.output_video.write_bytes(b"x")
    rv = RenderView()
    rv.set_job(job, cues=3)
    assert rv._right_stack.currentIndex() == 2 and rv.render_btn.text() == "Dựng lại"
