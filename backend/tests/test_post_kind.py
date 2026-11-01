"""A video that is already vertical and ≤ 3 min (a Douyin short) goes out as a Short and no Short
is cut from it (autopilot.post_kind) — in the headless after-approve path (remote.CliSession) and
the desktop's (MainWindow._autopilot_rendered); its post title comes from its own lines."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.models import Job
from app.pipeline import approval, autopilot, remote

SRT = ("1\n00:00:00,000 --> 00:00:02,000\nTôi về rồi.\n\n"
       "2\n00:00:02,000 --> 00:00:04,000\nAi cho phép các cô xuống?\n")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    sd = tmp_path / "_series" / "hai"
    sd.mkdir(parents=True)
    (sd / "series.json").write_text(json.dumps({
        "name": "Hài Douyin", "match": "Hài Douyin",
        "render": {"voice": "vi-VN-NamMinhNeural", "frame": "source"}}), encoding="utf-8")
    return tmp_path


def _plan(auto_short=True, kinds=("long", "short")):
    autopilot.save_plan("hai", {**autopilot.DEFAULT_PLAN, "on": True, "auto_short": auto_short,
                                "platforms": {"folder": {"on": True, "kinds": list(kinds)}}})


def _job(storage: Path, jid="clip1", *, size=(1080, 1920), dur=58.0, title="搞笑日常",
         inserts=()) -> Job:
    d = storage / jid
    d.mkdir()
    (d / "vi.srt").write_text(SRT, encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": title}), encoding="utf-8")
    (d / "job.json").write_text(json.dumps({"id": jid, "opts": {}}), encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "hai", "name": "Hài Douyin"}),
                                        encoding="utf-8")
    if size is not None:
        (d / "output.mp4").write_bytes(b"mp4")
        (d / "render.json").write_text(json.dumps({"size": list(size), "duration": dur,
                                                   "inserts": [list(i) for i in inserts]}),
                                       encoding="utf-8")
    return Job(id=jid, work_dir=d)


def test_kind_of():
    assert autopilot.kind_of((1080, 1920), 58) == "short"
    assert autopilot.kind_of([720, 1280], 180) == "short"
    assert autopilot.kind_of((1080, 1920), 180.5) == "long"          # over YouTube Shorts' 3 min
    assert autopilot.kind_of((1920, 1080), 30) == "long"             # landscape
    assert autopilot.kind_of((1080, 1080), 30) == "long"             # square
    assert autopilot.kind_of(None, 30) == "long" and autopilot.kind_of((1080, 1920), 0) == "long"
    assert autopilot.kind_of(("x", 1), 30) == "long" and autopilot.kind_of((1,), 30) == "long"


def test_post_kind_reads_the_render(storage):
    assert autopilot.post_kind(_job(storage, "a")) == "short"
    assert autopilot.post_kind(_job(storage, "b", size=(1920, 1080))) == "long"
    assert autopilot.post_kind(_job(storage, "c", dur=200.0)) == "long"
    # a commentary pause makes the rendered video longer than the source
    assert autopilot.post_kind(_job(storage, "d", dur=170.0, inserts=[(5.0, 12.0)])) == "long"
    # a Short cut from an episode is a Short whatever its file says
    cut = _job(storage, "e", size=None)
    (cut.work_dir / "metadata.json").write_text(json.dumps(
        {"title": "x", "short": {"parent": "ep9", "start": 0, "end": 40, "hook": "h"}}),
        encoding="utf-8")
    assert autopilot.post_kind(Job(id="e", work_dir=cut.work_dir)) == "short"
    # nothing rendered, no source → long (as before)
    assert autopilot.post_kind(_job(storage, "f", size=None)) == "long"


def test_post_kind_probes_the_source_before_a_render(storage):
    from app.ffmpegutil import ffmpeg_bin

    job = _job(storage, "g", size=None)
    try:
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=blue:s=180x320:r=5:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(job.source_video)], check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg not available")
    assert autopilot.post_kind(job) == "short"


def test_a_native_short_is_planned_as_a_short_with_its_own_title(storage):
    _plan(kinds=("short",))
    job = _job(storage)
    text = autopilot.after_approve_text(job)
    assert "tự cắt 1 Short" not in text and "dạng Short" in text
    (post,) = autopilot.plan_posts(job, autopilot.post_kind(job))
    assert post["kind"] == "short"
    meta = autopilot.post_meta(job, "short", autopilot.load_plan("hai"))
    # no episode number: no dangling "tập"; the hook is its punchiest line
    assert meta.title == "Ai cho phép các cô xuống? | Hài Douyin" and "#shorts" in meta.tags
    long_job = _job(storage, "ep", size=(1920, 1080), dur=600.0)
    assert "tự cắt 1 Short" in autopilot.after_approve_text(long_job)


class _Off:
    def configured(self):
        return False


def _cli(monkeypatch, out_size):
    cuts = []

    def fake_render(job, kind="long", log=None):
        assert approval.is_approved(job)
        (job.work_dir / "output.mp4").write_bytes(b"mp4")
        (job.work_dir / "render.json").write_text(json.dumps(
            {"size": list(out_size), "duration": 58.0}), encoding="utf-8")
        return job.work_dir / "output.mp4"

    monkeypatch.setattr(autopilot, "auto_short", lambda job, log=None: cuts.append(job.id))
    return remote.CliSession(ch=_Off(), render=fake_render), cuts


def test_headless_after_approve_posts_a_native_short_whole(storage, monkeypatch):
    _plan()
    job = _job(storage, size=None)
    approval.approve(job, cues=2)
    session, cuts = _cli(monkeypatch, (1080, 1920))
    session._render(job.id)
    assert [(p["kind"], p["platform"]) for p in autopilot.load_posts()] == [("short", "folder")]
    assert cuts == []                                                # nothing cut from a Short


def test_headless_after_approve_still_cuts_a_short_from_a_long_episode(storage, monkeypatch):
    _plan()
    job = _job(storage, size=None)
    approval.approve(job, cues=2)
    session, cuts = _cli(monkeypatch, (1920, 1080))
    session._render(job.id)
    assert [p["kind"] for p in autopilot.load_posts()] == ["long"] and cuts == [job.id]


# --- the desktop's after-render step ---------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def win(storage):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from desktop import main as m

    w = m.MainWindow()
    yield w
    w._closing = True
    w.close()


def test_desktop_posts_a_rendered_native_short_as_a_short_and_cuts_nothing(win, storage, monkeypatch):
    _plan()
    cuts = []
    monkeypatch.setattr(autopilot, "auto_short", lambda job, log=None: cuts.append(job.id))
    job = _job(storage)
    win._autopilot_rendered({"id": job.id, "title": "clip", "autopilot": "long"})
    assert [p["kind"] for p in autopilot.load_posts()] == ["short"]
    assert cuts == []
