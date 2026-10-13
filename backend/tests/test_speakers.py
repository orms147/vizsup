"""E-4: who is speaking → the brief's xưng hô. The excerpt is picked where the voice is,
observations are cached per source, and the brief rebuilds when one is added."""
from __future__ import annotations

import pytest

from app.models import Cue, Job
from app.pipeline import brief, speakers
from app.pipeline.srt import write_srt


def test_pick_excerpt_takes_the_window_with_the_most_voice():
    spans = [(0.0, 3.0), (100.0, 150.0), (155.0, 158.0)]
    a, b = speakers.pick_excerpt(spans, 300.0, 60.0)
    assert b - a == 60.0 and a <= 100.0 and b >= 150.0


def test_pick_excerpt_short_video_or_no_voice():
    assert speakers.pick_excerpt([(1.0, 2.0)], 30.0) == (0.0, 30.0)
    assert speakers.pick_excerpt(None, 300.0) == (0.0, 60.0)


@pytest.fixture()
def job(tmp_path):
    j = Job(id="s", work_dir=tmp_path / "s")
    j.source_audio.write_bytes(b"x")
    write_srt([Cue(1, 0.0, 2.0, "我说再喝一碗我熬的茶汤")], j.cn_srt)
    return j


def test_observe_caches_and_frames_are_opt_in(job, monkeypatch):
    calls = []
    monkeypatch.setattr(speakers, "_listen", lambda j, say: calls.append("audio") or "Một cô gái trẻ hát")
    monkeypatch.setattr(speakers, "_look", lambda j, say: calls.append("frames") or "Livestream, 1 nữ")
    first = speakers.observe(job)
    assert first.startswith("Nghe âm thanh") and first.endswith("Một cô gái trẻ hát")
    assert speakers.observe(job) == first
    assert calls == ["audio"]                                  # cached, frames not asked
    out = speakers.observe(job, frames=True)
    assert "Livestream, 1 nữ" in out and "chỉ bối cảnh" in out and calls == ["audio", "frames"]


def test_a_failed_observation_never_breaks_the_brief(job, monkeypatch):
    def boom(j, say):
        raise RuntimeError("no key")

    monkeypatch.setattr(speakers, "_listen", boom)
    assert speakers.observe(job) is None


def test_the_brief_request_carries_observations_and_rebuilds_on_them(job):
    req = brief.build_request(job, ["我说再喝一碗"], None, "Nghe âm thanh: cô gái hát")
    assert "OBSERVED" in req and "cô gái hát" in req
    assert brief._inputs_key(["a"], None) != brief._inputs_key(["a"], None, "Nghe: nữ")


# --- E-8: songs ---------------------------------------------------------------------

def test_singing_verdict_is_parsed_from_the_last_line():
    assert speakers.parse_singing("Một nam hát…\nHÁT: có") == "yes"
    assert speakers.parse_singing("…\nHÁT: xen kẽ") == "mixed"
    assert speakers.parse_singing("…\nhát: không") == "no"
    assert speakers.parse_singing("không có dòng kết") is None


def test_a_sung_video_gets_the_lyrics_rule():
    from app.providers.translation._common import LYRICS_HINT, system_prompt_for

    assert LYRICS_HINT in system_prompt_for("BASE", {"_lyrics": True, "_brief": "b"})
    assert LYRICS_HINT not in system_prompt_for("BASE", {"_brief": "b"})


def test_render_original_mode_is_preselected_for_songs(qt_render_view):
    rv = qt_render_view
    rv.suggest_original("yes")
    assert rv._audio_mode() == "original" and not rv.voice.isEnabled()
    rv.mode_cards["duck"].setChecked(True)
    rv.suggest_original("yes")                     # the user's own choice is kept
    assert rv._audio_mode() == "duck"


@pytest.fixture()
def qt_render_view():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from desktop.views.render_view import RenderView

    return RenderView()
