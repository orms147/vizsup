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


# --- E-10: clipboard pickup ----------------------------------------------------------

def test_clipboard_link_is_offered_once_and_starts_only_on_click(view):
    sent = []
    view.startRequested.connect(sent.append)
    view.url.setText("")
    text = "xem nè https://v.douyin.com/AbC123/ hay lắm"
    assert view.offer_clipboard(text, known={"https://v.douyin.com/old/"})
    assert not view.clip_chip.isHidden() and sent == []            # never auto-starts
    view._use_clipboard()
    assert view.url.text() == "https://v.douyin.com/AbC123/" and len(sent) == 1
    assert not view.offer_clipboard(text)                          # used → not offered again


def test_clipboard_ignores_other_links_known_projects_and_dismissed(view):
    assert not view.offer_clipboard("https://youtube.com/watch?v=x")
    assert not view.offer_clipboard("https://b23.tv/k", known={"https://b23.tv/k"})
    assert view.offer_clipboard("https://www.bilibili.com/video/BV1x")
    view._dismiss_clipboard()
    assert not view.offer_clipboard("https://www.bilibili.com/video/BV1x")


# --- E-11: unfinished projects are listed for resuming ---------------------------------

def test_unfinished_jobs_are_listed(tmp_path, monkeypatch):
    import json

    from app.config import settings
    from desktop import store

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "source.mp4").write_bytes(b"x")
    (tmp_path / "a" / "job.json").write_text(json.dumps({"url": "https://v.douyin.com/a/",
                                                         "opts": {"tts": "edge"}}), encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "source.mp4").write_bytes(b"x")          # no job.json → not resumable
    jobs = store.list_jobs()
    assert [j["id"] for j in jobs] == ["a"] and jobs[0]["unfinished"]
    assert "https://v.douyin.com/a/" in store.known_urls()
