"""Local pitch check: an objective second opinion on the speakers' gender (E-4)."""
from __future__ import annotations

import numpy as np
import pytest

from app.pipeline import pitch, speakers


def _voice(f0: float, seconds: float = 2.0) -> np.ndarray:
    t = np.arange(0, seconds, 1 / pitch.SR)
    return (0.2 * sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 6))).astype(np.float32)


@pytest.mark.parametrize("f0", [95.0, 130.0, 210.0, 320.0])
def test_f0_of_a_harmonic_voice(f0):
    got = pitch.f0_frames(_voice(f0))
    assert len(got) > 50 and abs(float(np.median(got)) - f0) < 1.5


def test_verdict_low_high_mixed_and_too_little():
    assert pitch.verdict(pitch.f0_frames(_voice(125)))["voice"] == "low"
    assert pitch.verdict(pitch.f0_frames(_voice(240)))["voice"] == "high"
    both = np.concatenate([_voice(125), _voice(240)])
    assert pitch.verdict(pitch.f0_frames(both))["voice"] is None          # two speakers
    assert pitch.verdict(pitch.f0_frames(_voice(125, 0.4))) is None       # < 1 s of voice
    assert len(pitch.f0_frames(np.zeros(pitch.SR, dtype=np.float32))) == 0  # silence


def test_listener_genders_ignore_place_names():
    assert speakers.genders("Một người nam, thanh niên. HÁT: không") == {"low"}
    assert speakers.genders("Hai người: nam trung niên và nữ trẻ") == {"low", "high"}
    assert speakers.genders("Cô gái nói về du lịch Việt Nam và miền Nam") == set()


TALK_F = "Một người nói, giới tính nữ, thanh niên. HÁT: không"


def test_pitch_note_flags_a_contradiction_only_for_talking():
    low = {"median": 128, "voice": "low"}
    note = speakers.pitch_note(TALK_F, low)
    assert "CHƯA CHẮC" in note and "~128 Hz" in note
    assert speakers.pitch_note(TALK_F, {"median": 230, "voice": "high"}) is None    # agrees
    assert speakers.pitch_note("Một nữ ca sĩ. HÁT: có", low) is None                # singing
    assert speakers.pitch_note("Một bé gái nữ nói. HÁT: không", low) is None        # child
    assert speakers.pitch_note(TALK_F, {"median": 200, "voice": None}) is None      # mixed
    filled = speakers.pitch_note("Một người, giới tính không rõ. HÁT: không", low)
    assert filled.endswith("giọng trầm (thường là nam).")


def test_observe_adds_the_pitch_line_to_the_brief_notes(tmp_path, monkeypatch):
    from app.models import Job

    job = Job(id="p", work_dir=tmp_path / "p")
    monkeypatch.setattr(speakers, "_listen", lambda j, say: TALK_F)
    monkeypatch.setattr(speakers, "_measure", lambda j, say: {"median": 120, "voice": "low"})
    notes = speakers.observe(job)
    assert TALK_F in notes and "CHƯA CHẮC" in notes
    monkeypatch.setattr(speakers, "_measure", lambda j, say: 1 / 0)    # cached: not re-measured
    assert speakers.observe(job) == notes


def test_brief_rules_cover_the_tā_trap_and_song_personas():
    from app.pipeline import brief

    rules = brief.BRIEF_INSTRUCTION
    assert "他 / 她 / 它 are the same spoken word" in rules
    assert "lyrics' own persona" in rules and "CHƯA CHẮC" in rules
