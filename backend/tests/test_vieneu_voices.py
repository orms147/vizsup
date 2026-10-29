"""VieNeu (local voices) + the multilingual edge voices on one voice list (2026-10-29):
a voice "vieneu:Trúc Ly" names its own provider; the render page lists edge voices plus the
installed local ones; VieNeu writes an mp3 at the asked speed. No model is loaded here."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.providers import registry  # noqa: E402
from app.providers.tts import edge, vieneu  # noqa: E402


def test_split_voice_routes_by_prefix():
    assert registry.split_voice("edge", "vieneu:Trúc Ly") == ("vieneu", "Trúc Ly")
    assert registry.split_voice("edge", "vi-VN-HoaiMyNeural") == ("edge", "vi-VN-HoaiMyNeural")
    assert registry.split_voice("edge", None) == ("edge", None)
    assert registry.split_voice("edge", "nope:X") == ("edge", "nope:X")    # unknown prefix


def test_voice_choices_add_the_local_voices_when_installed(monkeypatch):
    monkeypatch.setattr(vieneu.VieNeuTTS, "available", lambda self: True)
    got = registry.voice_choices("edge")
    assert got[:2] == ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    assert "fr-FR-VivienneMultilingualNeural" in got
    assert [v for v in got if v.startswith("vieneu:")] == [f"vieneu:{v}" for v in vieneu.VOICES]
    monkeypatch.setattr(vieneu.VieNeuTTS, "available", lambda self: False)
    assert not [v for v in registry.voice_choices("edge") if v.startswith("vieneu:")]
    assert set(vieneu.VOICES) == {"Quốc Tuấn", "Trúc Ly", "Ngọc Huyền", "Adam bựa"}  # owner's picks


def test_edge_keeps_its_vi_voices_first_and_skips_ava():
    assert edge.VOICES[:2] == ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    assert not any("Ava" in v for v in edge.VOICES)


def test_gender_hints_cover_the_new_voices():
    from app.pipeline.speakers import voice_for

    assert voice_for([f"vieneu:{v}" for v in vieneu.VOICES], "high") == "vieneu:Trúc Ly"
    assert voice_for(vieneu.VOICES, "low") == "Quốc Tuấn"
    assert voice_for(edge.VOICES, "high") == "vi-VN-HoaiMyNeural"


def test_tempo_from_rate():
    assert vieneu._tempo("+10%") == pytest.approx(1.10)
    assert vieneu._tempo("-5%") == pytest.approx(0.95)
    assert vieneu._tempo(None) == 1.0 and vieneu._tempo("fast") == 1.0
    assert vieneu._tempo("+300%") == 2.0


def test_synthesize_writes_an_mp3_with_the_voice_and_speed(tmp_path, monkeypatch):
    pytest.importorskip("soundfile")
    seen = {}

    class Fake:
        sample_rate = 48_000

        def infer(self, text, voice=None):
            seen["voice"] = voice
            return np.zeros(4800, dtype=np.float32)

    monkeypatch.setattr(vieneu, "_get_engine", lambda: Fake())
    cmds = []
    real_run = vieneu.subprocess.run
    monkeypatch.setattr(vieneu.subprocess, "run", lambda cmd, **kw: (cmds.append(cmd),
                                                                      real_run(cmd, **kw))[1])
    out = vieneu.VieNeuTTS().synthesize("xin chào", tmp_path / "s.mp3", voice="Ngọc Huyền",
                                        rate="+10%")
    assert out.exists() and out.stat().st_size > 0
    assert seen["voice"] == "Ngọc Huyền" and "atempo=1.100" in cmds[0]
    vieneu.VieNeuTTS().synthesize("x", tmp_path / "t.mp3", voice="Unknown")
    assert seen["voice"] == vieneu.DEFAULT_VOICE


def test_pipeline_tts_runs_vieneu_for_a_prefixed_voice(tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import tts

    (tmp_path / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n",
                                     encoding="utf-8")
    used = []

    class P:
        supports_batch = False

        def available(self):
            return True

        def voices(self):
            return list(vieneu.VOICES)

        def synthesize(self, text, out_path, *, voice=None, rate=None):
            used.append(voice)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"mp3")
            return Path(out_path)

    monkeypatch.setattr(tts, "get_tts", lambda name: used.append(name) or P())
    tts.synthesize(Job(id="x", work_dir=tmp_path), provider="edge", voice="vieneu:Trúc Ly")
    assert used == ["vieneu", "Trúc Ly"]


def test_render_page_names(qapp=None):
    pytest.importorskip("PySide6.QtWidgets")
    from desktop.views.render_view import _voice_name

    assert _voice_name("vieneu:Trúc Ly") == "Trúc Ly · trên máy"
    assert _voice_name("fr-FR-VivienneMultilingualNeural") == "Vivienne (đa ngôn ngữ)"
    assert _voice_name("vi-VN-HoaiMyNeural") == "Hoài My"
