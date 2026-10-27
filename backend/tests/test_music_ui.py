"""Editor tab "Nhạc nền" + the render page's audio choice (keep the original sound by
default; remembered per project; honest about the separation engine)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

pytest.importorskip("PySide6.QtWidgets")

from app.models import Job  # noqa: E402
from app.pipeline import music as mu  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# --- editor tab -------------------------------------------------------------------------

@pytest.fixture()
def view(qapp, tmp_path, monkeypatch):
    from desktop.views import editor_view

    v = editor_view.EditorView()
    v.job = Job(id="u", work_dir=tmp_path / "u")
    monkeypatch.setattr(v, "_duration", lambda: 90.0)
    v.music.set_duration(90.0)
    song = tmp_path / "Nhạc vui.mp3"
    song.write_bytes(b"\x00song" * 50)
    monkeypatch.setattr(v, "_pick_music_file", lambda: song)
    return v


def test_tab_sits_next_to_commentary(view):
    bar = view.inspector.tabbar
    assert bar.tabText(view.music_tab) == "Nhạc nền"
    assert view.music_tab == view.commentary_tab + 1


def test_add_whole_video_copies_the_file_and_saves(view):
    view._add_music("all")
    (c,) = mu.load(view.job)
    assert (c.start, c.end, c.name) == (0.0, None, "Nhạc vui.mp3")
    assert (view.job.work_dir / c.file).is_file() and c.file.startswith("music/")
    assert view.timeline._music == [(0.0, None)]
    assert "1 đoạn" in view.music.count_lbl.text() and view.music.to_end.isChecked()


def test_add_from_the_playhead_then_edit_and_remove(view, monkeypatch):
    monkeypatch.setattr(view.video, "current_time", lambda: 12.0)
    view._add_music("here")
    mp = view.music
    assert mp.selected().start == 12.0
    mp.to_end.setChecked(False)                            # a concrete end: +30 s
    assert mu.load(view.job)[0].end == 42.0
    mp.end_edit.setText("0:20")
    mp._on_end()
    mp.volume.setValue(55)
    mp.fade_in.setValue(3.0)
    mp.loop_cb.setChecked(False)
    mp.duck_cb.setChecked(False)
    (c,) = mu.load(view.job)
    assert (c.end, c.volume, c.fade_in, c.loop, c.duck) == (20.0, 0.55, 3.0, False, False)
    monkeypatch.setattr(view.video, "current_time", lambda: 15.5)
    mp.startHereRequested.emit()
    assert mu.load(view.job)[0].start == 15.5
    assert "00:15" in mp.list.item(0).text() and "55%" in mp.list.item(0).text()
    mp.del_btn.click()
    assert not (view.job.work_dir / mu.FILE).exists() and view.timeline._music == []


def test_reopening_the_project_loads_its_music(view):
    mu.save(view.job, [mu.Clip(file="music/a.mp3", start=5, end=9, name="a.mp3")])
    view._load_music(view.job)
    assert view.music.list.count() == 1 and view.timeline._music == [(5.0, 9.0)]
    assert "không tìm thấy" in view.music.warn_lbl.text()   # the file is not there


# --- render page ------------------------------------------------------------------------

@pytest.fixture()
def rv(qapp, monkeypatch):
    from desktop.views import render_view

    state = {"sep": False}
    monkeypatch.setattr(render_view, "_separation_available", lambda: state["sep"])
    v = render_view.RenderView()
    v._sep_state = state
    return v


def _job(tmp_path, name="r"):
    return Job(id=name, work_dir=tmp_path / name)


def test_default_mode_pure():
    from desktop.views.render_view import default_audio_mode

    assert default_audio_mode(True) == "keep_music"
    assert default_audio_mode(False) == "duck"


def test_without_separation_defaults_to_keeping_the_original_and_says_why(rv, tmp_path):
    rv.set_job(_job(tmp_path), cues=3)
    assert rv._audio_mode() == "duck"
    assert not rv.sep_box.isHidden() and "chưa cài" in rv.sep_note.text()
    assert "chưa cài" in rv.mode_cards["keep_music"].desc()
    fired = []
    rv.installSeparationRequested.connect(lambda: fired.append(1))
    rv.sep_btn.click()
    assert fired == [1]
    rv.mode_cards["keep_music"].click()                    # still choosable: explains fallback
    assert "khi dựng sẽ tự dùng" in rv.sep_note.text()


def test_with_separation_defaults_to_keep_music(rv, tmp_path):
    rv._sep_state["sep"] = True
    rv.set_job(_job(tmp_path), cues=3)
    assert rv._audio_mode() == "keep_music" and rv.sep_box.isHidden()
    assert "Bỏ giọng Trung" in rv.mode_cards["keep_music"].desc()


def test_install_later_upgrades_an_unchosen_default_only(rv, tmp_path):
    rv.set_job(_job(tmp_path, "a"), cues=3)
    rv.set_separation_available(True)
    assert rv._audio_mode() == "keep_music" and rv.sep_box.isHidden()
    rv.mode_cards["replace"].click()                       # the user's own choice
    rv.set_separation_available(True)
    assert rv._audio_mode() == "replace"


def test_choice_is_remembered_per_project(rv, tmp_path):
    from desktop.views import render_view

    a, b = _job(tmp_path, "a"), _job(tmp_path, "b")
    (a.work_dir / render_view.VOICE_FILE).write_text(json.dumps({"voice": "v", "rate": 1.2}),
                                                     encoding="utf-8")
    rv.set_job(a, cues=1)
    rv.mode_cards["replace"].click()
    saved = json.loads((a.work_dir / render_view.VOICE_FILE).read_text(encoding="utf-8"))
    assert saved == {"voice": "v", "rate": 1.2, "audio_mode": "replace"}   # merged, not lost
    rv.set_job(b, cues=1)
    assert rv._audio_mode() == "duck"                      # b never chose
    rv.set_job(a, cues=1)
    assert rv._audio_mode() == "replace"
    rv.suggest_original("yes")                             # a saved choice beats E-8
    assert rv._audio_mode() == "replace"


def test_retired_mix_choice_maps_to_duck(rv, tmp_path):
    from desktop.views import render_view

    j = _job(tmp_path)
    (j.work_dir / render_view.VOICE_FILE).write_text(json.dumps({"audio_mode": "mix"}),
                                                     encoding="utf-8")
    rv.set_job(j, cues=1)
    assert rv._audio_mode() == "duck" and "mix" not in rv.mode_cards


def test_cards_say_what_they_keep(rv):
    titles = {k: c._title.text() for k, c in rv.mode_cards.items()}
    assert titles["replace"].startswith("Chỉ giọng Việt")
    assert "bỏ hết âm gốc" in rv.mode_cards["replace"].desc().lower()
    assert "âm gốc" in titles["duck"] and "lồng tiếng" in titles["duck"]
    assert "hiệu ứng" in titles["keep_music"]


def test_render_page_summarizes_the_music(rv, tmp_path):
    j = _job(tmp_path)
    rv.set_job(j, cues=1)
    assert "Chưa thêm nhạc nền" in rv.music_lbl.text()
    mu.save(j, [mu.Clip(file="music/a.mp3", end=10), mu.Clip(file="music/b.mp3", start=30)])
    rv.set_job(j, cues=1)
    assert rv.music_lbl.text().startswith("Nhạc nền: 2 đoạn")


def test_render_options_carry_the_mode(rv, tmp_path):
    rv.set_job(_job(tmp_path), cues=1)
    seen = []
    rv.renderRequested.connect(seen.append)
    rv._emit_render()
    assert seen[0]["audio_mode"] == "duck"
    assert rv.stage_rows["separate"].isHidden()            # nothing to separate without engine
