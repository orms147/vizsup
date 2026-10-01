"""v2 start screen (offscreen Qt): the stage list follows the worker, context is
detected from progress text, and errors pick the right inline card."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
input_view = pytest.importorskip("desktop.views.input_view")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from app.providers.registry import list_providers

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)   # empty recent list
    v = input_view.InputView(list_providers())
    v.url.setText("https://www.douyin.com/video/1")
    return v


def states(v):
    return {k: r.state for k, r in v.stage_rows.items()}


def test_stages_follow_the_worker_and_context_comes_from_progress_text(view):
    view.set_busy(True)
    assert not view.start.isEnabled()
    view.set_stage("download")
    view.set_stage("subtitle")
    view.set_progress(0.7, "Đọc nhanh ngữ cảnh video…")
    assert states(view) == {"download": "done", "subtitle": "done", "context": "active",
                            "translate": "wait"}
    view.set_stage("translate")
    assert states(view)["context"] == "done"
    view.set_done()
    assert set(states(view).values()) == {"done"}
    assert view.start.isEnabled()


def test_skipped_context_reads_as_skipped(view):
    view.set_busy(True)
    view.set_stage("download")
    view.set_stage("subtitle")
    view.set_stage("translate")
    assert view.stage_rows["context"].detail.text() == "Bỏ qua"


def test_cookie_error_shows_the_three_step_card_and_retries_with_the_same_opts(view):
    sent = []
    view.startRequested.connect(sent.append)
    view._emit_start()
    view.set_busy(True)
    view.set_stage("download")
    view.show_error("RuntimeError: Douyin cần cookie mới để tải. Chưa cấu hình cookie.")
    assert not view.alert.isHidden() and view.progress.isHidden()
    assert not view.retry_btn.isEnabled()          # needs a cookies.txt first
    view._retry()
    assert len(sent) == 2 and sent[0] == sent[1]


def test_cancel_is_not_an_error_card(view):
    view.set_busy(True)
    view.set_stage("download")
    view.show_error("Đã hủy.")
    assert view.alert.isHidden()
    assert view.stage_rows["download"].state == "paused"
