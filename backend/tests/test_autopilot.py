"""Autopilot core (app/pipeline/autopilot.py): per-series posting plans, slots, the post queue,
post texts, the folder publisher end to end, and carrying the approval to an automatically cut
Short only when its lines are exactly approved lines of the episode."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from app.config import settings
from app.models import Job
from app.pipeline import approval, autopilot
from app.providers.base import PostResult

SRT = ("1\n00:00:00,000 --> 00:00:02,000\nAi cho phép các cô xuống?\n\n"
       "2\n00:00:02,000 --> 00:00:04,000\nTôi được ưu tiên tìm ở đây,\n\n"
       "3\n00:00:04,000 --> 00:00:06,000\nvì tôi đã giết một con xác sống.\n")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _episode(storage: Path, jid="ep9", title="第7集 | 《末日校车》09 我一个普通大巴司机", out=True) -> Job:
    d = storage / jid
    d.mkdir()
    (d / "vi.srt").write_text(SRT, encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": title, "platform": "douyin",
                                                 "uploader": "阿熊动漫"}), encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": 9, "last": 9}),
                                        encoding="utf-8")
    sd = storage / "_series" / "末日校车"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "series.json").write_text(json.dumps({"name": "末日校车", "match": "末日校车"}),
                                    encoding="utf-8")
    if out:
        (d / "output.mp4").write_bytes(b"mp4")
    return Job(id=jid, work_dir=d)


def _plan_on(platforms):
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True, "platforms": platforms,
                                     "times": ["12:00", "19:00"], "per_day": 1})


def test_plan_defaults_and_targets(storage):
    assert autopilot.load_plan("x")["on"] is False and autopilot.targets(autopilot.load_plan("x"), "long") == []
    _plan_on({"youtube": {"on": True, "kinds": ["long", "short"]},
              "facebook": {"on": True, "kinds": ["short"]}, "tiktok": {"on": False}})
    p = autopilot.load_plan("末日校车")
    assert autopilot.targets(p, "long") == ["youtube"]
    assert autopilot.targets(p, "short") == ["youtube", "facebook"]
    assert "YouTube (dài+Short)" in autopilot.summary("末日校车")


def test_next_slot_respects_times_and_per_day():
    now = datetime(2026, 10, 31, 10, 0).timestamp()
    s1 = autopilot.next_slot([], ["12:00", "19:00"], 1, now)
    assert datetime.fromtimestamp(s1).hour == 12
    s2 = autopilot.next_slot([s1], ["12:00", "19:00"], 1, now)          # one a day → tomorrow
    assert datetime.fromtimestamp(s2).date() > datetime.fromtimestamp(s1).date()
    s3 = autopilot.next_slot([s1], ["12:00", "19:00"], 2, now)          # two a day → 19:00 today
    assert datetime.fromtimestamp(s3).hour == 19
    late = datetime(2026, 10, 31, 20, 0).timestamp()
    assert datetime.fromtimestamp(autopilot.next_slot([], ["19:00"], 1, late)).day == 1


def test_plan_posts_queues_each_platform_once(storage):
    job = _episode(storage)
    _plan_on({"folder": {"on": True, "kinds": ["long"]}, "youtube": {"on": True, "kinds": ["long"]}})
    now = datetime(2026, 10, 31, 10, 0).timestamp()
    new = autopilot.plan_posts(job, "long", now=now)
    assert {p["platform"] for p in new} == {"folder", "youtube"}
    assert autopilot.plan_posts(job, "long", now=now) == []             # already queued
    # YouTube holds a scheduled post itself → it goes up now; the folder package waits
    assert [p["platform"] for p in autopilot.due(now)] == ["youtube"]
    assert len(autopilot.due(now + 3 * 86400)) == 2
    # no series plan / no rendered video → nothing
    other = _episode(storage, "ep10", out=False)
    assert autopilot.plan_posts(other, "long", now=now) == []


def test_record_retries_then_fails(storage):
    job = _episode(storage)
    _plan_on({"folder": {"on": True, "kinds": ["long"]}})
    (post,) = autopilot.plan_posts(job, "long", now=time.time() - 10 * 86400)
    for _ in range(2):
        p = autopilot.record(post["id"], PostResult(False, "failed", message="mạng"))
        assert p["status"] == "pending" and p["retry_at"] > time.time()
        assert autopilot.due() == []                                    # waits for its retry
    assert [x["id"] for x in autopilot.due(time.time() + 3601)] == [post["id"]]
    p = autopilot.record(post["id"], PostResult(False, "failed", message="mạng"))
    assert p["status"] == "failed" and p["result"]["message"] == "mạng"


def test_publish_with_the_folder_publisher(storage):
    job = _episode(storage)
    _plan_on({"folder": {"on": True, "kinds": ["long"]}})
    (post,) = autopilot.plan_posts(job, "long", now=time.time() - 10 * 86400)
    res = autopilot.publish(post)
    assert res.ok and res.status == "draft"
    folder = Path(res.url)
    text = (folder / "post.txt").read_text(encoding="utf-8")
    assert text.startswith("末日校车 – Tập 9 | Vietsub + Lồng tiếng") and "#vietsub" in text
    assert autopilot.record(post["id"], res)["status"] == "draft"


def test_publish_refuses_an_unknown_platform_and_packages_an_unconnected_one(storage):
    job = _episode(storage)
    post = {"id": "x", "job": job.id, "kind": "long", "platform": "nowhere", "series": "末日校车"}
    assert not autopilot.publish(post).ok
    res = autopilot.publish({**post, "platform": "youtube"})          # not linked on this machine
    assert res.ok and res.status == "draft" and "chưa kết nối" in res.message
    assert (Path(res.url) / "post.txt").exists()


def test_short_title_and_tags(storage):
    job = _episode(storage)
    md = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
    md["short"] = {"parent": "p", "start": 0, "end": 40, "hook": "Ai cho phép các cô xuống?"}
    (job.work_dir / "metadata.json").write_text(json.dumps(md), encoding="utf-8")
    meta = autopilot.post_meta(Job(id=job.id, work_dir=job.work_dir), "short",
                               autopilot.load_plan("末日校车"))
    assert meta.title == "Ai cho phép các cô xuống? | 末日校车 tập 9" and "#shorts" in meta.tags


def _child(storage: Path, parent: Job, texts: list[str], start=0.0, end=6.0) -> Job:
    d = storage / "short1"
    d.mkdir()
    body = "".join(f"{k}\n00:00:0{2 * (k - 1)},000 --> 00:00:0{2 * k},000\n{t}\n\n"
                   for k, t in enumerate(texts, 1))
    (d / "vi.srt").write_text(body, encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps(
        {"title": "x · Short 1", "short": {"parent": parent.id, "start": start, "end": end,
                                            "hook": texts[0]}}), encoding="utf-8")
    return Job(id="short1", work_dir=d)


def test_carry_approval_only_for_identical_approved_lines(storage):
    parent = _episode(storage)
    child = _child(storage, parent, ["Ai cho phép các cô xuống?", "Tôi được ưu tiên tìm ở đây,",
                                     "vì tôi đã giết một con xác sống."])
    assert not autopilot.carry_approval(parent, child)                  # parent not approved
    approval.approve(parent)
    assert autopilot.carry_approval(parent, child)
    assert approval.is_approved(child) and (child.work_dir / "approved_from.json").exists()


def test_no_carry_when_a_line_differs(storage):
    parent = _episode(storage)
    approval.approve(parent)
    child = _child(storage, parent, ["Ai cho phép các cô xuống?", "Tôi ĐÃ SỬA câu này,",
                                     "vì tôi đã giết một con xác sống."])
    assert not autopilot.carry_approval(parent, child) and not approval.is_approved(child)


def test_render_opts_from_the_series_or_its_latest_render(storage):
    from app.pipeline import series

    job = _episode(storage, out=False)
    assert autopilot.render_opts(job) is None                           # nothing ever rendered
    prev = _episode(storage, "ep8", title="第6集 | 《末日校车》08")
    (prev.work_dir / "render_opts.json").write_text(json.dumps(
        {"voice": "vi-VN-NamMinhNeural", "rate": "+30%", "frame": "source", "res": "720"}),
        encoding="utf-8")
    opts = autopilot.render_opts(job)
    assert opts["voice"] == "vi-VN-NamMinhNeural" and opts["frame"] == "source"
    p = series.load("末日校车")
    p.render = {"voice": "vieneu:Trúc Ly", "rate": "+20%", "frame": "source"}
    series.save(p)
    assert autopilot.render_opts(job)["voice"] == "vieneu:Trúc Ly"         # the series' own wins
    short = autopilot.render_opts(job, "short")
    assert short["frame"] == "tiktok" and short["short"] is True and short["safe_caption"]


def test_claim_lets_one_process_publish_a_post(storage):
    job = _episode(storage)
    _plan_on({"folder": {"on": True, "kinds": ["long"]}})
    (post,) = autopilot.plan_posts(job, "long", now=time.time() - 10 * 86400)
    assert autopilot.claim(post["id"]) is True
    assert autopilot.claim(post["id"]) is False                     # the other process
    assert autopilot.due() == []                                    # running is not due
    posts = autopilot.load_posts()
    posts[0]["claimed_at"] = time.time() - autopilot.RUN_STALE_S - 1  # a publisher that died
    autopilot.save_posts(posts)
    assert [p["id"] for p in autopilot.due()] == [post["id"]] and autopilot.claim(post["id"])


def test_run_due_publishes_and_records(storage):
    job = _episode(storage)
    _plan_on({"folder": {"on": True, "kinds": ["long"]}})
    autopilot.plan_posts(job, "long", now=time.time() - 10 * 86400)
    logs = []
    (p,) = autopilot.run_due(log=logs.append)
    assert p["status"] == "draft" and logs and logs[0].startswith("✓ folder")
    assert autopilot.run_due() == []                                # nothing left


def test_youtube_and_facebook_go_up_early_as_scheduled_posts(storage, monkeypatch):
    job = _episode(storage)
    _plan_on({"youtube": {"on": True, "kinds": ["long"]}, "facebook": {"on": True, "kinds": ["long"]},
              "tiktok": {"on": True, "kinds": ["long"]}})
    now = time.time()
    autopilot.plan_posts(job, "long", now=now)
    posts = autopilot.load_posts()
    slot = now + 6 * 3600                                       # tonight, whatever the clock says
    for p in posts:
        p["slot"] = slot
    autopilot.save_posts(posts)
    assert {p["platform"] for p in autopilot.due(now)} == {"youtube", "facebook"}  # TikTok: at the slot

    seen = {}

    class Pub:
        display_name = "YouTube"

        def connected(self):
            return True

        def publish(self, video, meta, progress=None):
            seen["at"] = meta.publish_at
            return PostResult(True, "scheduled", url="https://youtu.be/x", message="hẹn giờ")
    monkeypatch.setattr("app.providers.registry.get_publisher", lambda _name: Pub())
    yt = next(p for p in posts if p["platform"] == "youtube")
    assert autopilot.publish(yt).status == "scheduled" and seen["at"] == slot
    p = autopilot.record(yt["id"], PostResult(True, "scheduled", url="https://youtu.be/x"))
    assert p["status"] == "scheduled" and p["slot"] == slot          # its hour is kept

    # a failed early upload keeps the slot and is retried an hour later, still scheduled
    fb = next(x for x in posts if x["platform"] == "facebook")
    p = autopilot.record(fb["id"], PostResult(False, "failed", message="mạng"))
    assert p["slot"] == slot and p["retry_at"] > now
    # too near (or already past) → published at the slot, not scheduled
    near = {**yt, "slot": time.time() + 60}
    assert autopilot.early(near) is False
    autopilot.publish(near)
    assert seen["at"] is None


def test_scheduled_posts_are_reported_as_such(storage):
    from app.pipeline import remote

    outs = remote.posted_outs({"platform": "youtube", "kind": "long", "job": "nope",
                               "status": "scheduled", "slot": time.time() + 7200,
                               "result": {"url": "https://youtu.be/x"}})
    assert outs[0].text.startswith("⏰ YouTube: đã tải lên") and "tự công khai lúc" in outs[0].text


def test_a_new_series_renders_like_the_owners_latest_episode(storage):
    """No render in this series yet → the owner's newest long render of another series;
    a Short's 9:16 settings never serve as the template."""
    job = _episode(storage, out=False)
    other = storage / "solo"
    other.mkdir()
    (other / "metadata.json").write_text(json.dumps({"title": "《别的》01"}), encoding="utf-8")
    (other / "output.mp4").write_bytes(b"mp4")
    (other / "render_opts.json").write_text(json.dumps({"voice": "vieneu:Ngọc Huyền", "frame": "source"}),
                                            encoding="utf-8")
    short = storage / "short1"
    short.mkdir()
    (short / "metadata.json").write_text(json.dumps({"title": "x", "short": {"parent": "solo"}}),
                                         encoding="utf-8")
    (short / "output.mp4").write_bytes(b"mp4")
    (short / "render_opts.json").write_text(json.dumps({"voice": "edge", "frame": "tiktok"}),
                                            encoding="utf-8")
    import os
    os.utime(short / "output.mp4", (time.time() + 60, time.time() + 60))  # the Short is newer
    opts = autopilot.render_opts(job)
    assert opts["voice"] == "vieneu:Ngọc Huyền" and opts["frame"] == "source"


def test_a_test_plan_keeps_every_post_private(storage, monkeypatch):
    job = _episode(storage)
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True, "private": True,
                                     "platforms": {"youtube": {"on": True, "kinds": ["long"]},
                                                   "facebook": {"on": True, "kinds": ["long"]}}})
    assert "ĐĂNG THỬ" in autopilot.summary("末日校车")
    meta = autopilot.post_meta(job, "long", autopilot.load_plan("末日校车"), publish_at=time.time() + 7200)
    assert meta.private is True and meta.publish_at is None

    seen = []

    class Pub:
        display_name = "Facebook (Trang)"

        def connected(self):
            return True

        def publish(self, video, meta, progress=None):
            seen.append(meta)
            return PostResult(True, "private", url="https://youtu.be/x")

    class Folder:
        def publish(self, video, meta, progress=None):
            return PostResult(True, "draft", url="C:/posts/x")

    monkeypatch.setattr("app.providers.registry.get_publisher",
                        lambda name: Folder() if name == "folder" else Pub())
    autopilot.plan_posts(job, "long")
    posts = {p["platform"]: p for p in autopilot.load_posts()}
    res = autopilot.publish(posts["youtube"])
    assert res.status == "private" and seen[-1].private and seen[-1].publish_at is None
    res = autopilot.publish(posts["facebook"])                 # no private Page video → package
    assert res.status == "draft" and "đăng thử" in res.message and len(seen) == 1
