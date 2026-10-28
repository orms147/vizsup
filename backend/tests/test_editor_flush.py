"""N-1: the editor's debounced autosave must not lose the last edit on job-switch.

Runs the real EditorView offscreen (QT_QPA_PLATFORM=offscreen). Skipped where
PySide6 / the desktop package aren't importable (e.g. base-deps CI) — the GUI
smoke still covers it there.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

pytest.importorskip("PySide6.QtWidgets")
editor_view = pytest.importorskip("desktop.views.editor_view")

from app.models import Job  # noqa: E402
from desktop import cuemodel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_flush_saves_pending_edit_on_job_switch(qapp, tmp_path, monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(cuemodel, "save_vi", lambda job, cues: saved.append(job.id))

    ev = editor_view.EditorView()
    job_a = Job(id="jobA", work_dir=tmp_path / "A")
    job_b = Job(id="jobB", work_dir=tmp_path / "B")

    ev.load(job_a)
    assert saved == []                    # loading is not an edit

    ev._mark_dirty()                      # an edit inside the 600 ms debounce window
    ev.load(job_b)                        # switch before the timer fires
    assert saved == ["jobA"]              # ...must persist job A first

    ev.flush()
    assert saved == ["jobA"]              # clean state → flush is a no-op


def test_flush_noop_when_clean(qapp, tmp_path, monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(cuemodel, "save_vi", lambda job, cues: saved.append(job.id))
    ev = editor_view.EditorView()
    ev.flush()                            # no job at all → must not raise or save
    ev.load(Job(id="j", work_dir=tmp_path / "j"))
    ev.flush()
    assert saved == []


def _hardsub_job(tmp_path, name):
    import json

    d = tmp_path / name
    d.mkdir()
    (d / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    (d / "cn.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"has_hardsubs": True}), encoding="utf-8")
    return Job(id=name, work_dir=d)


def test_opening_an_approved_project_keeps_its_approval(qapp, tmp_path):
    """2026-10-29: an approved, rendered episode without style.json lost its approval just
    by being opened — the editor picked "Hộp đặc" for the burned-in Chinese and saved it."""
    from app.pipeline import approval

    job = _hardsub_job(tmp_path, "ok")
    approval.approve(job)
    ev = editor_view.EditorView()
    ev.load(job)
    qapp.processEvents()
    ev.flush()                            # what leaving the editor does
    ev.load(_hardsub_job(tmp_path, "other"))
    assert approval.is_approved(job)
    # an unapproved one still gets the cover box
    fresh = _hardsub_job(tmp_path, "fresh")
    ev.load(fresh)
    assert ev.style_panel.box_style.currentText() == editor_view.COVER_STYLE
    qapp.processEvents()                  # its "đã chọn nền" note fires while ev lives
