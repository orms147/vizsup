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
