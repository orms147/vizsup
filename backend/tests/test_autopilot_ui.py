"""Autopilot in the desktop app: the «Duyệt nhanh» and «Tự động đăng» dialogs, approval →
the render queued with the series' settings, a finished render → posts planned, and the
minute timer publishing a due post (folder publisher, no network)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline import autopilot  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    sd = tmp_path / "_series" / "末日校车"
    sd.mkdir(parents=True)
    (sd / "series.json").write_text(json.dumps({"name": "末日校车", "match": "末日校车",
                                                "render": {"voice": "vi-VN-NamMinhNeural",
                                                           "rate": "+30%", "frame": "source"}}),
                                    encoding="utf-8")
    return tmp_path


def _episode(storage: Path, jid="ep9", out=True) -> Job:
    d = storage / jid
    d.mkdir()
    (d / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nXin chào.\n", encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": "第7集 | 《末日校车》09"}), encoding="utf-8")
    (d / "job.json").write_text(json.dumps({"id": jid, "opts": {}}), encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": 9, "last": 9}),
                                        encoding="utf-8")
    if out:
        (d / "output.mp4").write_bytes(b"mp4")
    return Job(id=jid, work_dir=d)


def test_fast_approve_dialog_lists_the_flags(qapp):
    from desktop.widgets.autopilot_dialogs import FastApproveDialog

    rows = [{"n": 4, "start": 7.7, "zh": "你倒是会挑。", "vi": "Cô biết chọn ghê.",
             "flags": ["AI nghi sai"]}]
    d = FastApproveDialog(None, title="Tập 9", cues=65, flagged=rows, after="Duyệt xong …")
    assert d.table.rowCount() == 1 and d.table.item(0, 2).text() == "Cô biết chọn ghê."
    assert d.table.item(0, 0).text() == "00:07"
    d._edit()
    assert d.edit_requested
    clean = FastApproveDialog(None, title="Tập 9", cues=65, flagged=[], after="")
    assert clean.table is None


def test_autopilot_dialog_round_trip(qapp, storage):
    from desktop.widgets.autopilot_dialogs import AutopilotDialog

    d = AutopilotDialog(None, slug="末日校车")
    d.on.setChecked(True)
    d.name.setText("Xe Buýt Mạt Thế")
    d.times.setText("12:00; 19:30")
    d.per_day.setValue(2)
    on, lg, sh = d.rows["folder"]
    on.setChecked(True)
    lg.setChecked(False)
    d.save()
    plan = autopilot.load_plan("末日校车")
    assert plan["on"] and plan["series_name"] == "Xe Buýt Mạt Thế"
    assert plan["times"] == ["12:00", "19:30"] and plan["per_day"] == 2
    assert plan["platforms"]["folder"] == {"on": True, "kinds": ["short"]}
    again = AutopilotDialog(None, slug="末日校车")
    assert again.on.isChecked() and again.name.text() == "Xe Buýt Mạt Thế"


@pytest.fixture
def win(qapp, storage):
    from desktop import main as m

    w = m.MainWindow()
    yield w
    w._closing = True
    w.close()


def test_approval_queues_the_render_with_the_series_settings(win, storage):
    job = _episode(storage, out=False)
    queued = []
    win._batch_add = lambda items: queued.extend(items) or len(items)
    win._autopilot_after_approve(job)
    assert queued == []                                          # Tự động đăng is off
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True,
                                     "platforms": {"folder": {"on": True, "kinds": ["long"]}}})
    win._autopilot_after_approve(job)
    (item,) = queued
    assert item["autopilot"] == "long" and item["opts"]["voice"] == "vi-VN-NamMinhNeural"


def test_a_rendered_episode_gets_its_posts_and_a_due_post_is_published(win, storage, qapp):
    job = _episode(storage)
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True, "auto_short": False,
                                     "platforms": {"folder": {"on": True, "kinds": ["long"]}}})
    win._autopilot_rendered({"id": job.id, "title": "ep9", "autopilot": "long"})
    (post,) = autopilot.load_posts()
    assert post["platform"] == "folder" and post["status"] == "pending"
    posts = autopilot.load_posts()
    posts[0]["slot"] = time.time() - 5                           # its time has come
    autopilot.save_posts(posts)
    win._post_due()
    end = time.time() + 10
    while win._posting and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    (done,) = autopilot.load_posts()
    assert done["status"] == "draft" and Path(done["result"]["url"], "post.txt").exists()


def test_the_gate_opens_the_one_click_review_when_autopilot_is_on(win, storage, qapp):
    job = _episode(storage, out=False)
    opened = []
    win._fast_approve = opened.append
    win.job, win.opts = job, {}
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True})
    win._on_gate()
    end = time.time() + 3
    while not opened and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert opened == [job.id]
