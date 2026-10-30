"""Shorts S1: the "Cài sẵn Short" button on the render page and the series' remembered Short
settings. One click = 9:16 + blurred bars + the episode chip + subtitles above the platform's UI;
a real Short render is saved on the series and the next episode starts from it."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline import batch, series, shorts  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


def _job(base: Path, jid: str, title: str) -> Job:
    j = Job(id=jid, work_dir=base / jid)
    j.metadata = {"title": title, "platform": "douyin"}
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                              encoding="utf-8")
    (j.work_dir / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n",
                                       encoding="utf-8")
    return j


# --- the series keeps the Short settings -----------------------------------------------------

def test_defaults_without_a_series(storage):
    j = _job(storage, "a1", "no series here")
    cfg = shorts.settings_for(j.work_dir)
    assert cfg["platform"] == "tiktok" and cfg["frame"] == "tiktok" and cfg["tiktok_fill"] == "blur"
    assert cfg["episode_card"]["on"] is True


def test_a_short_render_is_saved_on_the_series_and_the_next_episode_gets_it(storage):
    ep1 = _job(storage, "e1", "第1集 | 《末日校车》01 开头")
    series.resolve(ep1)
    opts = {"short": True, "safe_caption": "reels", "frame": "tiktok", "tiktok_fill": "black",
            "res": "720", "preset": "balanced", "episode_card": {"on": True, "chip": "Ep {n}"},
            "burn_cues": [object()], "voice": "vi-VN-NamMinhNeural"}
    batch.save_opts(ep1.work_dir, opts)
    p = series.profile_of(ep1)
    assert p.short["platform"] == "reels" and p.short["res"] == "720"
    assert "burn_cues" not in p.short and p.short["episode_card"]["chip"] == "Ep {n}"
    ep2 = _job(storage, "e2", "第2集 | 《末日校车》02 然后")
    series.resolve(ep2)
    cfg = shorts.settings_for(ep2.work_dir)
    assert cfg["platform"] == "reels" and cfg["tiktok_fill"] == "black" and cfg["res"] == "720"
    # the latest Short render wins
    batch.save_opts(ep2.work_dir, {**opts, "safe_caption": "shorts", "res": "1080"})
    assert shorts.settings_for(ep1.work_dir)["platform"] == "shorts"


def test_only_real_short_renders_are_learned(storage):
    ep = _job(storage, "e1", "第1集 | 《末日校车》01 开头")
    series.resolve(ep)
    batch.save_opts(ep.work_dir, {"frame": "tiktok", "res": "720"})                 # not a Short
    batch.save_opts(ep.work_dir, {"short": True, "preview": True, "res": "480"})    # a quick proof
    assert series.profile_of(ep).short == {}
    assert shorts.learn(ep.work_dir, {"short": True, "frame": "tiktok"}) is True
    lone = _job(storage, "lone", "no series")
    assert shorts.learn(lone.work_dir, {"short": True}) is False


# --- the render page ---------------------------------------------------------------------------

def _view(qapp):
    from desktop.theme import qss
    from desktop.views.render_view import RenderView

    qapp.setStyleSheet(qss())
    v = RenderView()
    v.resize(1000, 900)
    v.show()
    for _ in range(4):
        qapp.processEvents()
    return v


def test_one_click_sets_the_9_16_frame_fill_card_and_safe_captions(qapp, storage):
    v = _view(qapp)
    assert v.frame.currentData() == "source" and not v.safe_cb.isChecked()
    o = v.collect_opts()
    assert o["safe_caption"] is None and o["short"] is False
    v.short_btn.click()
    assert v.frame.currentData() == "tiktok" and v.fill.currentData() == "blur"
    assert v.card_cb.isChecked() and v.safe_cb.isChecked()
    o = v.collect_opts()
    assert (o["frame"], o["tiktok_fill"], o["safe_caption"], o["short"]) == (
        "tiktok", "blur", "tiktok", True)
    assert o["episode_card"] and o["episode_card"]["on"]
    assert v.preview.safe_zone() == "tiktok"                  # and the overlay shows the same strips
    v.close()


def test_the_preset_uses_the_series_short_settings(qapp, storage):
    ep1 = _job(storage, "e1", "第1集 | 《末日校车》01 开头")
    series.resolve(ep1)
    batch.save_opts(ep1.work_dir, {"short": True, "safe_caption": "reels", "frame": "tiktok",
                                   "tiktok_fill": "black", "res": "720", "preset": "light",
                                   "episode_card": {"on": True, "chip": "Phần {n}",
                                                    "end": "Xem tiếp Phần {next}"}})
    ep2 = _job(storage, "e2", "第2集 | 《末日校车》02 然后")
    series.resolve(ep2)
    v = _view(qapp)
    v.set_job(ep2, cues=1, tts="edge")
    v.short_btn.click()
    assert v.fill.currentData() == "black" and v.res.currentData() == "720"
    assert v.preset.currentData() == "light"
    assert v.card_chip.text() == "Phần {n}" and v.card_end.text() == "Xem tiếp Phần {next}"
    assert v.collect_opts()["safe_caption"] == "reels" and v.preview.safe_zone() == "reels"
    v.close()


def test_switching_project_resets_the_preset(qapp, storage):
    v = _view(qapp)
    a = _job(storage, "a", "one")
    v.set_job(a, cues=1, tts="edge")
    v.short_btn.click()
    assert v.collect_opts()["short"] is True
    v.set_job(_job(storage, "b", "two"), cues=1, tts="edge")
    o = v.collect_opts()
    assert o["short"] is False and o["safe_caption"] is None
    v.close()


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@needs_ffmpeg
def test_a_native_vertical_source_gets_safe_captions_by_itself(qapp, storage):
    j = _job(storage, "vert", "no series")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=540x960:r=5:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(j.source_video)], check=True)
    v = _view(qapp)
    v.set_job(j, cues=1, tts="edge")
    for _ in range(200):
        qapp.processEvents()
        if v._src_size:
            break
        time.sleep(0.02)
    assert v._src_size == (540, 960)
    assert v.safe_cb.isChecked() and v.collect_opts()["safe_caption"] == "tiktok"
    v.safe_cb.setChecked(False)                               # the user's choice sticks
    assert v.collect_opts()["safe_caption"] is None
    land = _job(storage, "land", "no series")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=960x540:r=5:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(land.source_video)], check=True)
    v.set_job(land, cues=1, tts="edge")
    for _ in range(200):
        qapp.processEvents()
        if v._src_size == (960, 540):
            break
        time.sleep(0.02)
    assert not v.safe_cb.isChecked()                          # landscape: nothing automatic
    v.close()


def test_short_preset_turns_on_the_bar_text_and_it_only_shows_in_the_9_16_frame(qapp, storage):
    v = _view(qapp)
    assert not v.canvas_cb.isVisibleTo(v) and v.collect_opts()["canvas_text"] is False
    v.short_btn.click()
    assert v.canvas_cb.isVisibleTo(v) and v.bars_cb.isVisibleTo(v)
    o = v.collect_opts()
    assert o["canvas_text"] is True and o["bar_subs"] is True
    v.bars_cb.setChecked(False)
    assert v.collect_opts()["bar_subs"] is False and v.collect_opts()["canvas_text"] is True
    v.canvas_cb.setChecked(False)                          # no canvas layer → no bar dialogue either
    assert not v.bars_cb.isEnabled() and v.collect_opts()["bar_subs"] is False
    v.frame.setCurrentIndex(v.frame.findData("source"))     # back to the source frame: both off
    o = v.collect_opts()
    assert not v.canvas_cb.isVisibleTo(v) and o["canvas_text"] is False and o["bar_subs"] is False
    v.close()


def test_the_series_remembers_the_bar_text_choice(storage):
    ep = _job(storage, "e1", "第1集 | 《末日校车》01 开头")
    series.resolve(ep)
    batch.save_opts(ep.work_dir, {"short": True, "frame": "tiktok", "canvas_text": True,
                                  "bar_subs": False, "safe_caption": "tiktok"})
    cfg = shorts.settings_for(ep.work_dir)
    assert cfg["canvas_text"] is True and cfg["bar_subs"] is False
