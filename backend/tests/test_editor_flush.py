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
