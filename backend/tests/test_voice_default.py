"""The dub voice follows the speaker (e2e test 2026-10-26: a male tech reviewer — the
audio listener said "Một người nam" — was dubbed with HoaiMy by default), and a voice
the user picked on the render page sticks to that project."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models import Job  # noqa: E402
from app.pipeline import speakers  # noqa: E402

EDGE = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]


def test_voice_for_matches_the_speaker():
    assert speakers.voice_for(EDGE, "low") == "vi-VN-NamMinhNeural"
    assert speakers.voice_for(EDGE, "high") == "vi-VN-HoaiMyNeural"
    assert speakers.voice_for(EDGE, None) is None
    assert speakers.voice_for(["en-US-JennyFemale", "en-US-GuyMale"], "low") == "en-US-GuyMale"
    assert speakers.voice_for(["unknown-voice"], "low") is None


def _job(tmp_path, audio: str) -> Job:
    job = Job(id="j", work_dir=tmp_path)
    (tmp_path / "observed.json").write_text(
        json.dumps({"audio": audio, "key": speakers._key(job)}, ensure_ascii=False),
        encoding="utf-8")
    return job


def test_speaker_gender_needs_one_gender(tmp_path):
    assert speakers.speaker_gender(_job(tmp_path, "Một người nam, thanh niên.")) == "low"
    assert speakers.speaker_gender(_job(tmp_path, "Một nam và một nữ nói chuyện.")) is None


def test_default_voice_priority(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from desktop import main
    from desktop.views import render_view

    job = _job(tmp_path, "Một người nam, review công nghệ.")
    assert main._default_voice(job, EDGE, {}) == "vi-VN-NamMinhNeural"
    assert main._default_voice(job, EDGE, {"voice": "vi-VN-HoaiMyNeural"}) == "vi-VN-HoaiMyNeural"
    (tmp_path / render_view.VOICE_FILE).write_text(json.dumps({"voice": "vi-VN-HoaiMyNeural"}),
                                                   encoding="utf-8")
    assert render_view.saved_voice(job) == "vi-VN-HoaiMyNeural"
    assert main._default_voice(job, EDGE, {"voice": "x"}) == "vi-VN-HoaiMyNeural"
    assert main._default_voice(job, [], {}) is None


def test_main_module_entry_point_is_last():
    """`python -m desktop.main` runs main() at the `if __name__` line: a helper defined
    below it (as _default_voice once was) is a NameError in the real app only."""
    import ast

    src = (Path(__file__).resolve().parents[2] / "desktop" / "main.py").read_text(encoding="utf-8")
    body = ast.parse(src).body
    last = body[-1]
    assert isinstance(last, ast.If) and "__name__" in ast.unparse(last.test)


def test_render_page_warns_a_fast_talker_and_offers_a_speed(tmp_path):
    """e2e 2026-10-27: 522/717 lines came out late at 1.0× with no warning beforehand."""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from desktop.cuemodel import EditorCue
    from desktop.views import render_view

    QApplication.instance() or QApplication([])
    rv = render_view.RenderView()
    job = Job(id="j", work_dir=tmp_path)
    rv.set_job(job, cues=10)
    fast = [EditorCue(start=i * 1.6, end=i * 1.6 + 1.5, vi="một hai ba bốn năm sáu bảy tám chín mười",
                      zh="") for i in range(10)]   # speech-only model: 2.5 s at 1.0×, 1.96 s at 1.3×
    rv.set_preview(tmp_path / "missing.mp4", fast)
    assert not rv.fit_hint.isHidden() and "trễ" in rv.fit_hint.text()
    assert not rv.fit_btn.isHidden() and "1.30×" in rv.fit_btn.text()
    rv.fit_btn.click()
    assert rv.speed.value() == 130
    rv._remember_voice()
    assert render_view.saved_rate(job) is None or render_view.saved_rate(job) == 1.3
    slow = [EditorCue(start=i * 3.0, end=i * 3.0 + 3.0, vi="một hai", zh="") for i in range(5)]
    rv.set_preview(tmp_path / "missing.mp4", slow)
    assert rv.fit_hint.isHidden()


def test_speed_is_restored_per_project(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from desktop.views import render_view

    QApplication.instance() or QApplication([])
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (b / render_view.VOICE_FILE).write_text(json.dumps({"voice": EDGE[1], "rate": 1.2}),
                                            encoding="utf-8")
    rv = render_view.RenderView()
    rv.set_voices(EDGE)
    rv.set_job(Job(id="b", work_dir=b), cues=1)
    rv.select_voice(EDGE[1])
    assert rv.speed.value() == 120 and rv.voice.currentData() == EDGE[1]
    rv.set_job(Job(id="a", work_dir=a), cues=1)
    rv.select_voice(EDGE[0])
    assert rv.speed.value() == 100                       # a has no saved speed
    assert render_view.saved_rate(Job(id="b", work_dir=b)) == 1.2   # b untouched
    rv.speed.setValue(130)                               # e.g. the "Dùng 1.30×" button
    assert render_view.saved_rate(Job(id="a", work_dir=a)) == 1.3
