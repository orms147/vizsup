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


def test_voice_label_is_shared():
    assert registry.voice_label("vieneu:Ngọc Huyền") == "Ngọc Huyền · trên máy"
    assert registry.voice_label("pt-BR-ThalitaMultilingualNeural") == "Thalita (đa ngôn ngữ)"
    assert registry.voice_label("vi-VN-NamMinhNeural") == "Nam Minh"
    pytest.importorskip("PySide6.QtWidgets")
    from desktop.widgets import commentary_panel

    assert commentary_panel._voice_name("vieneu:Trúc Ly") == "Trúc Ly · trên máy"


def test_install_vieneu_never_pulls_the_cpu_onnxruntime(monkeypatch):
    from app import tools

    cmds = []

    class R:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(tools.subprocess, "run", lambda cmd, **kw: (cmds.append(cmd), R())[1])
    monkeypatch.setattr(tools, "_fresh_version", lambda d: "3.8.3")
    ok, msg = tools.install_vieneu()
    assert ok and "VieNeu" in msg
    assert cmds[0][-2:] == ["--no-deps", tools.VIENEU]                 # vieneu without its pins
    assert not any("onnxruntime" in a for c in cmds[:2] for a in c)    # directml stays
    assert cmds[2][1] == "-c" and "backend='onnx'" in cmds[2][2]       # the model is fetched

    class Bad(R):
        returncode, stderr = 1, "ERROR: no matching distribution"

    monkeypatch.setattr(tools.subprocess, "run", lambda cmd, **kw: Bad())
    ok, msg = tools.install_vieneu()
    assert not ok and "cài thư viện thất bại" in msg


def test_commentary_note_in_a_vieneu_voice_runs_vieneu(tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import commentary as cm
    from app.providers import registry as reg

    made = []

    class P:
        def __init__(self, name):
            self.name = name

        def voices(self):
            return ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]

        def synthesize(self, text, out_path, *, voice=None, rate=None):
            made.append((self.name, voice))
            Path(out_path).write_bytes(b"mp3")
            return Path(out_path)

    monkeypatch.setattr(reg, "get_tts", lambda name: P(name))
    monkeypatch.setattr("app.pipeline.assemble.probe_duration", lambda p: 1.0)
    note = cm.Note(at=1.0, text="Ý kiến của tôi", voice="vieneu:Trúc Ly", mode="over")
    out = cm.synthesize(Job(id="c", work_dir=tmp_path), [note], provider="edge",
                        dub_voice="vi-VN-NamMinhNeural")
    assert made == [("vieneu", "Trúc Ly")] and len(out) == 1


def test_settings_card_shows_the_state(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    from app.config import settings
    from desktop.settings_dialog import SettingsView

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    monkeypatch.setattr(vieneu.VieNeuTTS, "available", lambda self: True)
    monkeypatch.setattr(vieneu, "model_cached", lambda: False)
    page = SettingsView()
    assert "chưa tải model" in page.vieneu_ver.text()
    page._install_vieneu_done((True, "Đã cài giọng VieNeu 3.8.3 — model đã tải sẵn."))
    assert page.vieneu_status.text().startswith("✓")
