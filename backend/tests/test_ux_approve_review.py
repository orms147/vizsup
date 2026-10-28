"""Approve dialog (UX audit E11): with lines still to look at it offers "Xem N câu" — back to the
editor with only those lines shown — and never approves on that path."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402


@pytest.fixture()
def qapp(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return QApplication.instance() or QApplication([])


def test_dialog_has_the_review_button_only_when_lines_are_pending(qapp):
    from desktop.widgets.dialogs import ApproveDialog

    d = ApproveDialog(None, cues=300, voice="HoaiMy", duration="15:45", pending=83)
    assert d.review_btn.text() == "Xem 83 câu" and not d.review_requested
    d.review_btn.click()
    assert d.review_requested and d.result() == 0          # closed without approving
    clean = ApproveDialog(None, cues=300, voice="HoaiMy", duration="15:45", pending=0)
    assert not hasattr(clean, "review_btn")


def test_xem_cau_returns_to_the_editor_filtered_and_does_not_approve(qapp, tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import approval
    from desktop import main as main_mod
    from desktop.cuemodel import EditorCue
    from desktop.widgets.dialogs import ApproveDialog

    class _Tts:
        def voices(self):
            return []

    monkeypatch.setattr(main_mod, "get_tts", lambda _name: _Tts())
    monkeypatch.setattr(ApproveDialog, "exec", lambda self: (self.review_btn.click(), 0)[1])

    win = main_mod.MainWindow()
    win.job = Job(id="ux1", work_dir=tmp_path / "ux1")
    win.opts = {}
    ed = win.editor
    ed.job = win.job
    ed.cues = [EditorCue(start=i * 4.0, end=i * 4.0 + 3, vi=f"câu {i}", zh="一") for i in range(6)]
    ed._reload(dirty=False)
    ed.cue_list.set_issues([["Quá dài"] if i in (2, 4) else [] for i in range(6)])
    win.stack.setCurrentIndex(main_mod.RENDER)               # approving from the render page

    win._approve()

    assert not approval.is_approved(win.job)
    assert win.stack.currentIndex() == main_mod.EDITOR
    assert ed.cue_list.review_only() and ed.cue_list.currentRow() == 2
