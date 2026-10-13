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


def test_render_view_frame_choice_is_sent_and_remembered(qapp):
    """E-16: the TikTok 9:16 frame rides in the render opts and survives a restart,
    even after the start screen saves its own options."""
    from desktop import store
    from desktop.views.render_view import RenderView

    rv = RenderView()
    got: list[dict] = []
    rv.renderRequested.connect(got.append)
    rv._emit_render()
    assert got[-1]["frame"] == "source"
    rv.frame.setCurrentIndex(rv.frame.findData("tiktok"))
    rv._emit_render(preview=True)
    assert got[-1]["frame"] == "tiktok" and got[-1]["preview"] is True
    store.save_ui_state({"tts": "edge"})                   # start screen overwrites its keys
    assert store.load_ui_state()["render_frame"] == "tiktok"
    assert RenderView().frame.currentData() == "tiktok"


def test_render_view_saves_a_copy_named_after_the_project(qapp, tmp_path, monkeypatch):
    from app.models import Job
    from desktop import store
    from desktop.views import render_view as rv_mod

    job = Job(id="abc", work_dir=tmp_path / "abc")
    job.output_video.write_bytes(b"x")
    (job.work_dir / "preview.mp4").write_bytes(b"p")
    rv = rv_mod.RenderView()
    rv.set_project("爱你没差 #翻唱", "")
    rv.set_job(job, cues=3)
    rv.show_done(str(job.work_dir / "preview.mp4"))        # quick proof: nothing to save yet
    assert not rv.save_btn.isEnabled() and not rv.mkv_action.isEnabled()
    rv.show_done(str(job.output_video))
    assert rv.save_btn.isEnabled() and rv.mkv_action.isEnabled()

    asked: list[str] = []
    dest = tmp_path / "out" / "mine.mp4"
    dest.parent.mkdir()

    def fake_dialog(_parent, _cap, default, _filt):
        asked.append(default)
        return str(dest), ""

    monkeypatch.setattr(rv_mod.QFileDialog, "getSaveFileName", fake_dialog)
    assert rv._ask_dest("Lưu video", ".mp4", "*.mp4") == dest
    assert Path(asked[0]).name == "爱你没差.mp4"
    assert store.load_ui_state()["export_dir"] == str(dest.parent)
    rv._ask_dest("Xuất .srt", ".vi.srt", "*.srt")
    assert Path(asked[1]) == dest.parent / "爱你没差.vi.srt"      # remembered folder


def test_run_render_forwards_frame(qapp, tmp_path, monkeypatch):
    job = Job(id="f", work_dir=tmp_path / "job")
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    from app.pipeline import approval

    approval.approve(job)
    seen: dict = {}

    def fake_assemble(j, segments, **kw):
        seen.update(kw)
        j.output_video.write_bytes(b"o")
        return j.output_video

    monkeypatch.setattr(tts_mod, "synthesize", lambda j, **kw: [])
    monkeypatch.setattr(assemble_mod, "assemble", fake_assemble)
    worker_mod.PipelineWorker(job, {"tts": "edge", "frame": "tiktok"}).run_render()
    assert seen["frame"] == "tiktok"
