"""The silence every TTS segment carries is cut off (pipeline/tts.py trim_*).

2026-10-30: edge-tts segments hold ~0.21 s of leading and ~0.65 s of trailing silence (3–5 word
lines are padded to 1.87 s). assemble._plan_segments plans on FILE durations, so that silence was
sped up, pushed the next line and was flagged "QUÁ DÀI": replayed on 5 real episodes it gave
167 / 25 / 68 / 40 / 27 flagged lines and 57 / 5 / 33 / 12 / 10 s of trimmed speech — and 0–2
flagged lines once the silence was cut. Real audio is generated with ffmpeg here (no network)."""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from app.models import Cue, Job
from app.pipeline import approval, assemble
from app.pipeline import tts as tts_mod
from app.pipeline.srt import write_srt

try:
    from app.ffmpegutil import ffmpeg_bin
    FFMPEG = ffmpeg_bin()
    subprocess.run([FFMPEG, "-version"], capture_output=True, check=True)
except Exception:  # noqa: BLE001
    FFMPEG = None

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


def make_audio(path, *, lead=0.3, speech=0.5, tail=0.7, amp=0.5, freq=220):
    """mp3 = ``lead`` s silence + ``speech`` s tone + ``tail`` s silence."""
    graph = (f"sine=f={freq}:d={speech}:r=24000,volume={amp},"
             f"adelay={int(lead * 1000)}:all=1,apad=pad_dur={tail}")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", graph, "-ac", "1",
                    "-c:a", "libmp3lame", "-b:a", "48k", str(path)], check=True)
    return path


def pcm(path):
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768


def dur(path) -> float:
    return len(pcm(path)) / 16000


# --- the pure part ----------------------------------------------------------------------------

def test_speech_window_keeps_the_margins():
    env = np.zeros(200)                                   # 2.0 s at 10 ms
    env[30:80] = 0.3                                      # speech 0.30–0.80 s
    start, end = tts_mod.speech_window(env, 2.0)
    assert start == pytest.approx(0.25) and end == pytest.approx(0.92)


def test_speech_window_is_relative_to_the_segments_own_level():
    quiet = np.zeros(200)
    quiet[30:80] = 0.02                                   # a quiet voice: 20× under the loud one
    quiet[80:90] = 0.0004                                 # the fading tail, ~−34 dB under its peak
    start, end = tts_mod.speech_window(quiet, 2.0)
    assert start == pytest.approx(0.25) and end == pytest.approx(0.92)   # the tail is not "speech"
    assert tts_mod.speech_window(np.full(200, 0.001), 2.0) is None        # below the absolute floor


def test_speech_window_never_cuts_below_the_minimum_or_an_empty_signal():
    blip = np.zeros(100)
    blip[50] = 0.5                                        # one 10 ms click
    assert tts_mod.speech_window(blip, 1.0) is None or \
        tts_mod.speech_window(blip, 1.0)[1] - tts_mod.speech_window(blip, 1.0)[0] >= tts_mod.TRIM_MIN
    assert tts_mod.speech_window(np.zeros(0), 1.0) is None
    assert tts_mod.speech_window(np.zeros(100), 1.0) is None


# --- real audio ---------------------------------------------------------------------------------

@needs_ffmpeg
def test_trim_cuts_the_padding_and_keeps_the_speech(tmp_path):
    p = make_audio(tmp_path / "seg.mp3", lead=0.3, speech=0.5, tail=0.7)
    before = pcm(p)
    assert tts_mod.trim_silence(p) is True
    after = pcm(p)
    assert 0.55 <= len(after) / 16000 <= 0.80            # 0.5 speech + 0.05 + 0.12 margins (+ mp3 frames)
    loud = lambda x: float((x ** 2).sum())               # noqa: E731
    assert loud(after) >= 0.97 * loud(before)             # no speech energy lost
    assert not list(tmp_path.glob(".*trim*"))             # no temp file left


@needs_ffmpeg
def test_a_short_interjection_survives(tmp_path):
    """"Ừ." — 0.18 s of voice inside 1.87 s: keeps the voice plus margins, never < TRIM_MIN."""
    p = make_audio(tmp_path / "u.mp3", lead=0.25, speech=0.18, tail=1.4)
    energy = float((pcm(p) ** 2).sum())
    assert tts_mod.trim_silence(p) is True
    d = dur(p)
    assert tts_mod.TRIM_MIN <= d <= 0.5
    # a synthetic tone starts abruptly (the mp3 re-encode softens its first ~10 ms); real
    # speech measured 99.3-100 % kept over 269 segments
    assert float((pcm(p) ** 2).sum()) >= 0.90 * energy


@needs_ffmpeg
def test_a_quiet_voice_is_trimmed_by_its_own_level(tmp_path):
    p = make_audio(tmp_path / "q.mp3", lead=0.3, speech=0.5, tail=0.7, amp=0.2)       # a peak of ~0.025: −32 dBFS
    assert tts_mod.trim_silence(p) is True
    assert dur(p) < 1.0


@needs_ffmpeg
def test_nothing_to_cut_or_all_silence_leaves_the_file_alone(tmp_path):
    tight = make_audio(tmp_path / "t.mp3", lead=0.03, speech=0.6, tail=0.05)
    size = tight.stat().st_size
    assert tts_mod.trim_silence(tight) is True            # already tight: counted done, not rewritten
    assert tight.stat().st_size == size
    silent = tmp_path / "s.mp3"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", "1", "-c:a", "libmp3lame", str(silent)], check=True)
    before = silent.read_bytes()
    assert tts_mod.trim_silence(silent) is False
    assert silent.read_bytes() == before


def test_garbage_files_never_raise(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"mp3 but not audio")
    assert tts_mod.trim_silence(bad) is False
    assert bad.read_bytes() == b"mp3 but not audio"
    assert tts_mod.trim_silence(tmp_path / "missing.mp3") is False


@needs_ffmpeg
def test_registry_trims_each_file_once_and_again_when_it_changes(tmp_path):
    a, b = make_audio(tmp_path / "a.mp3"), make_audio(tmp_path / "b.mp3")
    reg = tmp_path / "trimmed.json"
    assert tts_mod.trim_files([a, b], reg) == 2
    assert set(json.loads(reg.read_text(encoding="utf-8"))) == {"a.mp3", "b.mp3"}
    stamp = a.stat().st_mtime_ns
    assert tts_mod.trim_files([a, b], reg) == 0           # nothing to do the second time
    assert a.stat().st_mtime_ns == stamp
    make_audio(a, lead=0.4, speech=0.4, tail=0.9)         # the line was re-synthesized
    assert tts_mod.trim_files([a, b], reg) == 1
    assert dur(a) < 0.9


@needs_ffmpeg
def test_env_switch_turns_trimming_off(tmp_path, monkeypatch):
    p = make_audio(tmp_path / "a.mp3")
    monkeypatch.setenv("VIZSUP_TRIM_TTS", "0")
    assert tts_mod.trim_files([p], tmp_path / "trimmed.json") == 0
    assert dur(p) > 1.3


# --- through synthesize() ---------------------------------------------------------------------------

VI = ("1\n00:00:00,000 --> 00:00:01,000\ncâu một\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\ncâu hai\n")


class PaddedTTS:
    """Writes real audio with the padding edge-tts adds."""

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        self.calls += 1
        make_audio(out_path, lead=0.21, speech=0.45, tail=0.9)


@pytest.fixture()
def job(tmp_path):
    j = Job(id="t", work_dir=tmp_path / "job")
    j.vi_srt.write_text(VI, encoding="utf-8")
    return j


@needs_ffmpeg
def test_synthesize_trims_new_and_cached_segments(job, monkeypatch):
    fake = PaddedTTS()
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: fake)
    logs: list[str] = []
    segs = tts_mod.synthesize(job, provider="fake", log=logs.append)
    assert len(segs) == 2 and all(dur(p) < 0.9 for _c, p in segs)
    assert any("Cắt khoảng lặng" in m for m in logs)
    # a project rendered BEFORE this change: padded segments are in the cache already
    for _c, p in segs:
        make_audio(p, lead=0.21, speech=0.45, tail=0.9)
    (job.tts_dir / tts_mod.TRIM_FILE).unlink()
    calls = fake.calls
    segs = tts_mod.synthesize(job, provider="fake")
    assert fake.calls == calls                              # no new synthesis…
    assert all(dur(p) < 0.9 for _c, p in segs)              # …but trimmed all the same


@needs_ffmpeg
def test_trim_false_keeps_the_raw_segments(job, monkeypatch):
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: PaddedTTS())
    segs = tts_mod.synthesize(job, provider="fake", trim=False)
    assert all(dur(p) > 1.4 for _c, p in segs)
    assert not (job.tts_dir / tts_mod.TRIM_FILE).exists()


def test_fake_non_audio_segments_pass_through(job, monkeypatch):
    class Junk:
        def available(self):
            return True

        def synthesize(self, text, out_path, *, voice=None, rate=None):
            out_path.write_bytes(b"mp3" + text.encode())

    monkeypatch.setattr(tts_mod, "get_tts", lambda n: Junk())
    segs = tts_mod.synthesize(job, provider="fake")
    assert len(segs) == 2 and all(p.read_bytes().startswith(b"mp3") for _c, p in segs)


@needs_ffmpeg
def test_the_dub_window_is_rebuilt_when_a_segment_is_trimmed(job, monkeypatch):
    """Same text / voice, only the audio changed: the window cache key must change."""
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: PaddedTTS())
    segs = tts_mod.synthesize(job, provider="fake", trim=False)
    plan, _sped, _flag = assemble._plan_segments(segs)
    win = {"lo": 0.0, "hi": 3.0, "items": plan}
    files = [p for _c, p in segs]
    before = assemble._window_key(win, files)
    tts_mod.trim_files(files, job.tts_dir / tts_mod.TRIM_FILE)
    plan2, *_ = assemble._plan_segments(segs)
    after = assemble._window_key({"lo": 0.0, "hi": 3.0, "items": plan2}, files)
    assert before != after
    assert assemble._window_key({"lo": 0.0, "hi": 3.0, "items": plan}, files) != before  # the files alone differ


@needs_ffmpeg
def test_trimming_does_not_change_the_approved_revision(job, monkeypatch):
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: PaddedTTS())
    approval.approve(job)
    tts_mod.synthesize(job, provider="fake")
    assert approval.is_approved(job)


@needs_ffmpeg
def test_the_planner_no_longer_flags_lines_that_only_the_padding_made_long(tmp_path, monkeypatch):
    """The real symptom: 8 short lines, one every 0.8 s, each 0.45 s of speech in a padded file."""
    j = Job(id="p", work_dir=tmp_path / "p")
    write_srt([Cue(i, i * 0.8, i * 0.8 + 0.7, f"câu số {i}") for i in range(1, 9)], j.vi_srt)
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: PaddedTTS())
    segs = tts_mod.synthesize(j, provider="fake", trim=False)
    _p, _s, flagged_padded = assemble._plan_segments(segs)
    tts_mod.trim_files([p for _c, p in segs], j.tts_dir / tts_mod.TRIM_FILE)
    placements, sped, flagged = assemble._plan_segments(segs)
    assert len(flagged_padded) >= 4 and flagged == [] and sped == 0
    assert all(p["carry_s"] == 0 for p in placements)


# --- commentary notes ---------------------------------------------------------------------------------

@needs_ffmpeg
def test_commentary_notes_are_trimmed_too(tmp_path, monkeypatch):
    from app.pipeline import commentary
    from app.providers import registry

    j = Job(id="c", work_dir=tmp_path / "c")
    monkeypatch.setattr(registry, "get_tts", lambda n: PaddedTTS())
    out = commentary.synthesize(j, [commentary.Note(at=1.0, text="lời bình của tôi")],
                                provider="fake", dub_voice=None)
    assert len(out) == 1 and out[0].dur < 0.9               # a "pause" note freezes for this long
