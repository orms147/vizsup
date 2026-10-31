"""Changing the posting schedule from the phone (Telegram): /lich /gio /moingay /dangthu
/tamdung /tieptuc. Nothing here approves or publishes by itself."""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from app.config import settings
from app.models import Job
from app.pipeline import autopilot, remote


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _episode(storage, jid="ep9", n=9) -> Job:
    d = storage / jid
    d.mkdir()
    (d / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nchào\n", encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": f"《末日校车》{n:02d}"}), encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": n, "last": n}),
                                        encoding="utf-8")
    sd = storage / "_series" / "末日校车"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "series.json").write_text(json.dumps({"name": "末日校车"}), encoding="utf-8")
    (d / "output.mp4").write_bytes(b"mp4")
    return Job(id=jid, work_dir=d)


def _plan(**kw):
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True, "series_name": "Xe Tận Thế",
                                     "platforms": {"tiktok": {"on": True, "kinds": ["long"]}},
                                     "times": ["19:00"], "per_day": 1, **kw})


class Host:
    def renders(self):
        return []


def _say(text: str) -> str:
    (out,) = remote._text(remote.Event(kind="text", text=text), Host())
    return out.text


def test_parse_times():
    assert autopilot.parse_times(["19:00", "7h30", "12", "19.30", "19h"]) == \
        ["07:30", "12:00", "19:00", "19:30"]
    assert autopilot.parse_times(["25:00"]) == [] and autopilot.parse_times(["tối"]) == []


def test_new_hours_reslot_the_waiting_posts_but_not_the_uploaded_ones(storage):
    _plan()
    a, b = _episode(storage, "ep8", 8), _episode(storage, "ep9", 9)
    now = datetime(2026, 11, 1, 10, 0).timestamp()
    autopilot.plan_posts(a, "long", now=now)
    autopilot.plan_posts(b, "long", now=now)
    posts = autopilot.load_posts()
    posts[1]["status"] = "scheduled"                      # already on the platform
    up_slot = posts[1]["slot"]
    autopilot.save_posts(posts)
    names = autopilot.change_plans(times=["12:00", "21:00"], per_day=2)
    assert names == ["Xe Tận Thế"]
    plan = autopilot.load_plan("末日校车")
    assert plan["times"] == ["12:00", "21:00"] and plan["per_day"] == 2
    after = {p["job"]: p for p in autopilot.load_posts()}
    assert after["ep9"]["slot"] == up_slot                 # uploaded: kept
    assert datetime.fromtimestamp(after["ep8"]["slot"]).hour in (12, 21)


def test_pause_stops_publishing_and_resume_reslots(storage):
    _plan()
    job = _episode(storage)
    autopilot.plan_posts(job, "long", now=time.time() - 3 * 86400)   # long overdue
    assert autopilot.due()
    assert "tạm dừng" in _say("/tamdung")
    assert autopilot.paused() and autopilot.due() == []
    assert "Đăng lại" in _say("/tieptuc")
    assert not autopilot.paused()
    (p,) = autopilot.load_posts()
    assert p["slot"] > time.time()                         # the overdue post: the next slot


def test_commands_answer_in_vietnamese(storage):
    assert "Chưa bộ phim nào" in _say("/gio 19:00")
    _plan()
    _episode(storage)
    assert "21:00" in _say("/gio 21:00") and autopilot.load_plan("末日校车")["times"] == ["21:00"]
    assert "Gửi giờ" in _say("/gio tối nay")
    assert "2 bài/ngày" in _say("/moingay 2")
    assert "1–6" in _say("/moingay 9")
    assert "BẬT" in _say("/dangthu bat") and autopilot.load_plan("末日校车")["private"] is True
    assert "TẮT" in _say("/dangthu tắt") and autopilot.load_plan("末日校车")["private"] is False
    text = _say("/lich")
    assert text.startswith("📅 Lịch đăng") and "Xe Tận Thế" in text
    assert "/gio" in remote.HELP and "/tamdung" in remote.HELP
