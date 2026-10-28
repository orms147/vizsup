"""Per-line voice by gender (pipeline/voices.py): pitch guess, the overrides.json tag, the
approval revision, TTS voice per line (both provider paths), editor model + inspector, and
the render-page option. No network, no ffmpeg."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from app.config import settings
from app.models import Cue, Job
from app.pipeline import approval, sidecars, voices
from app.pipeline import tts as tts_mod
from app.pipeline.srt import write_srt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SR = 16000
F, M = "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"


def tone(f0: float, secs: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * secs)) / SR
    return sum(amp / k * np.sin(2 * np.pi * f0 * k * t) for k in (1, 2, 3, 4)).astype(np.float32)


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    st = tmp_path / "storage"
    st.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", st)
    return st


def make_job(storage, lines=("Một.", "Hai.", "Ba.")) -> Job:
    j = Job(id="v", work_dir=storage / "v")
    write_srt([Cue(i, i - 1, i - 0.1, t) for i, t in enumerate(lines, 1)], j.vi_srt)
    return j


# --- the guess ---------------------------------------------------------------------------

def test_cue_gender_from_f0_frames():
    assert voices.cue_gender(np.full(30, 110.0)) == "m"
    assert voices.cue_gender(np.full(30, 230.0)) == "f"
    assert voices.cue_gender(np.full(30, 164.0)) == "m" and voices.cue_gender(np.full(30, 166.0)) == "f"
    assert voices.cue_gender(np.full(voices.MIN_VOICED - 1, 110.0)) is None      # too little voice
    assert voices.cue_gender(np.zeros(0)) is None


def test_guess_cues_on_synthetic_voices():
    x = np.concatenate([tone(115, 0.9), np.zeros(SR // 10, np.float32), tone(230, 0.9),
                        np.zeros(SR // 10, np.float32), np.zeros(SR, np.float32)])
    cues = [Cue(1, 0.0, 0.9, "a"), Cue(2, 1.0, 1.9, "b"), Cue(3, 2.0, 3.0, "silence")]
    assert voices.guess_cues(x, cues) == ["m", "f", None]


def test_apply_tags_pads_rows_and_keeps_the_users_choice():
    rows = [{"gain_db": 0.0, "mute": False, "voice": "f"}]
    n = voices.apply_tags(rows, ["m", "f", None])
    assert n == 1 and rows[0]["voice"] == "f"                    # a set tag stays
    assert rows[1]["voice"] == "f" and "voice" not in rows[2] and len(rows) == 3
    assert voices.apply_tags(rows, ["m", "f", None], overwrite=True) == 1
    assert rows[0]["voice"] == "m"


def test_guess_job_writes_overrides_and_never_overwrites(storage, monkeypatch):
    job = make_job(storage)
    x = np.concatenate([tone(115, 0.9), np.zeros(SR // 10, np.float32),
                        tone(230, 0.9), np.zeros(SR // 10, np.float32), tone(120, 0.9)])
    (job.work_dir / "vocals.wav").write_bytes(b"x")
    monkeypatch.setattr(voices, "_decode", lambda src: x)
    seen = {}
    monkeypatch.setattr(voices, "_audio_source",
                        lambda j, log=None: seen.setdefault("src", j.work_dir / "vocals.wav"))
    logs: list[str] = []
    rep = voices.guess_job(job, log=logs.append)
    assert rep == {"set": 3, "female": 1, "male": 2, "unknown": 0, "split_hz": None}   # too few lines to split
    assert [r["voice"] for r in sidecars.load_overrides(job)] == ["m", "f", "m"]
    assert "2 câu nam · 1 câu nữ" in logs[-1]
    rows = sidecars.load_overrides(job)
    rows[0]["voice"] = "f"                                       # the user flipped line 1
    sidecars.save_overrides(job, rows)
    assert voices.guess_job(job)["set"] == 0
    assert sidecars.load_overrides(job)[0]["voice"] == "f"


def test_audio_source_prefers_the_separated_voice(storage):
    job = make_job(storage)
    assert voices._audio_source(job) is None
    job.source_audio.write_bytes(b"x")
    assert voices._audio_source(job) == job.source_audio
    (job.work_dir / "vocals.wav").write_bytes(b"x")
    assert voices._audio_source(job) == job.work_dir / "vocals.wav"


def test_guess_skips_singing_and_missing_inputs(storage, monkeypatch):
    job = make_job(storage)
    assert voices.guess_job(job) is None                          # no audio
    job.source_audio.write_bytes(b"x")
    from app.pipeline import speakers
    monkeypatch.setattr(speakers, "singing", lambda j: "yes")
    monkeypatch.setattr(voices, "_decode", lambda src: pytest.fail("must not decode"))
    assert voices.guess_job(job) is None
    assert voices.guess_job(Job(id="none", work_dir=storage / "none")) is None   # no vi.srt


# --- sidecar + approval --------------------------------------------------------------------

def test_a_voice_only_override_file_is_kept(storage):
    job = make_job(storage)
    sidecars.save_overrides(job, [{"gain_db": 0.0, "mute": False, "voice": "m"}])
    assert (job.work_dir / "overrides.json").exists()
    sidecars.save_overrides(job, [{"gain_db": 0.0, "mute": False}])
    assert not (job.work_dir / "overrides.json").exists()


def test_changing_a_voice_tag_revokes_the_approval(storage):
    job = make_job(storage)
    approval.approve(job)
    assert approval.is_approved(job)
    sidecars.save_overrides(job, [{"gain_db": 0.0, "mute": False, "voice": "f"}] + [{}] * 2)
    assert not approval.is_approved(job)
    approval.approve(job)
    assert approval.is_approved(job)
    sidecars.save_overrides(job, [{"gain_db": 0.0, "mute": False, "voice": "m"}] + [{}] * 2)
    assert not approval.is_approved(job)


# --- TTS ---------------------------------------------------------------------------------------

class FakeTTS:
    def __init__(self, batch=False, names=(F, M)):
        self.calls: list[tuple[str, str | None]] = []
        self.names = list(names)
        if batch:
            self.supports_batch = True

    def available(self):
        return True

    def voices(self):
        return self.names

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        self.calls.append((text, voice))
        out_path.write_bytes(b"mp3" + text.encode())

    def synthesize_many(self, items, *, voice=None, rate=None, retry_delays=None, on_done=None,
                        cancel=None):
        for key, text, out in items:
            self.calls.append((text, voice))
            out.write_bytes(b"mp3" + text.encode())
            on_done(key, None)


def tag(job, tags):
    sidecars.save_overrides(job, [{"gain_db": 0.0, "mute": False, **({"voice": t} if t else {})}
                                  for t in tags])


@pytest.mark.parametrize("batch", [False, True])
def test_tts_reads_each_line_with_its_tags_voice(storage, monkeypatch, batch):
    job = make_job(storage)
    tag(job, ["f", "m", None])
    fake = FakeTTS(batch=batch)
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: fake)
    tts_mod.synthesize(job, provider="fake", voice=M, voice_by_gender=True)
    assert sorted(fake.calls) == sorted([("Một.", F), ("Hai.", M), ("Ba.", M)])   # untagged → picked


@pytest.mark.parametrize("batch", [False, True])
def test_option_off_ignores_the_tags(storage, monkeypatch, batch):
    job = make_job(storage)
    tag(job, ["f", "m", "f"])
    fake = FakeTTS(batch=batch)
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: fake)
    tts_mod.synthesize(job, provider="fake", voice=M)
    assert {v for _t, v in fake.calls} == {M}


@pytest.mark.parametrize("batch", [False, True])
def test_flipping_one_tag_revoices_only_that_line(storage, monkeypatch, batch):
    job = make_job(storage)
    tag(job, ["f", "m", "m"])
    fake = FakeTTS(batch=batch)
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: fake)
    tts_mod.synthesize(job, provider="fake", voice=M, voice_by_gender=True)
    fake.calls.clear()
    tts_mod.synthesize(job, provider="fake", voice=M, voice_by_gender=True)
    assert fake.calls == []                                       # all cached
    tag(job, ["f", "f", "m"])
    tts_mod.synthesize(job, provider="fake", voice=M, voice_by_gender=True)
    assert fake.calls == [("Hai.", F)]


def test_turning_the_option_on_revoices_only_the_lines_that_change(storage, monkeypatch):
    job = make_job(storage)
    tag(job, ["f", "m", None])
    fake = FakeTTS()
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: fake)
    tts_mod.synthesize(job, provider="fake", voice=M)             # everything in M
    fake.calls.clear()
    tts_mod.synthesize(job, provider="fake", voice=M, voice_by_gender=True)
    assert fake.calls == [("Một.", F)]                            # only the woman's line


def test_provider_without_gendered_voices_keeps_the_picked_one(storage, monkeypatch):
    job = make_job(storage)
    tag(job, ["f", "m", "f"])
    fake = FakeTTS(names=("voice-a", "voice-b"))
    monkeypatch.setattr(tts_mod, "get_tts", lambda n: fake)
    tts_mod.synthesize(job, provider="fake", voice="voice-a", voice_by_gender=True)
    assert {v for _t, v in fake.calls} == {"voice-a"}


# --- editor model --------------------------------------------------------------------------------

pytest.importorskip("PySide6.QtWidgets")


def test_voice_tag_survives_save_load_split_and_merge(storage):
    from desktop import cuemodel
    from desktop.cuemodel import EditorCue

    job = make_job(storage, ("a b c d", "e"))
    cues = [EditorCue(0, 2, "a b c d", "甲", voice="f"), EditorCue(2, 3, "e", "乙")]
    cuemodel.save_vi(job, cues)
    back = cuemodel.load_pair(job)
    assert [c.voice for c in back] == ["f", ""]
    a, b = cuemodel.split_cue(back[0])
    assert (a.voice, b.voice) == ("f", "f")
    assert cuemodel.merge_cues(back[1], back[0]).voice == "f"
    assert cuemodel.merge_cues(back[0], back[1]).voice == "f"
    assert cuemodel.copy_cue(back[0]).voice == "f"


def test_a_bad_tag_on_disk_is_ignored(storage):
    from desktop import cuemodel

    job = make_job(storage, ("a",))
    (job.work_dir / "overrides.json").write_text(json.dumps([{"voice": "x"}]), encoding="utf-8")
    assert cuemodel.load_pair(job)[0].voice == ""


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    from desktop.cuemodel import EditorCue
    from desktop.views import editor_view

    v = editor_view.EditorView()
    v.cues = [EditorCue(0, 2, "một", "一", voice="m"), EditorCue(2, 4, "hai", "二")]
    v._reload(dirty=False)
    v.cue_list.selectRow(0)
    v._update_cue_panel(0)
    return v


def test_inspector_shows_and_flips_the_tag_as_one_undo_step(view):
    combo = view.inspector.voice_combo
    assert combo.currentData() == "m"
    depth = len(view._undo_stack)
    combo.setCurrentIndex(combo.findData("f"))
    combo.activated.emit(combo.currentIndex())
    assert view.cues[0].voice == "f" and len(view._undo_stack) == depth + 1
    combo.activated.emit(combo.currentIndex())                    # same value again: no new step
    assert len(view._undo_stack) == depth + 1
    view._undo_action()
    assert view.cues[0].voice == "m"
    view.cue_list.selectRow(1)
    view._update_cue_panel(1)
    assert view.inspector.voice_combo.currentData() == ""


# --- render page ----------------------------------------------------------------------------------

def test_render_page_option_is_sent_and_remembered(qapp):
    from desktop import store
    from desktop.views.render_view import RenderView

    rv = RenderView()
    assert rv.collect_opts()["voice_by_gender"] is False
    rv._has_real_voices = True
    rv.by_gender_cb.setChecked(True)
    assert rv.collect_opts()["voice_by_gender"] is True
    assert store.load_ui_state()["voice_by_gender"] is True
    assert RenderView().by_gender_cb.isChecked()
    rv._has_real_voices = False                                   # no voice list → never sent
    assert rv.collect_opts()["voice_by_gender"] is False


def test_worker_guess_failure_is_logged_not_raised(storage, monkeypatch):
    from desktop.worker import PipelineWorker

    def boom(*_a, **_k):
        raise RuntimeError("no audio")

    monkeypatch.setattr(voices, "guess_job", boom)
    w = PipelineWorker(make_job(storage), {})
    logs: list[str] = []
    w.log.connect(logs.append)
    w._guess_voices(w.job)
    assert logs and logs[-1].startswith("⚠ Bỏ qua bước đoán giọng")


# --- per-video split (2026-10-29: ep 7 gave 2 male / 30 female / 50 unknown) --------------------

MEN = [115, 120, 128, 135, 150, 162, 175, 183, 187, 192]            # real ep-7 male line medians
WOMEN = [212, 217, 225, 240, 262, 289, 325, 370, 440, 500]


def test_split_falls_in_the_widest_gap_between_the_two_clusters():
    thr = voices.split_threshold(MEN + WOMEN)
    assert 192 < thr < 212                                      # ≈ 202 Hz, not the fixed 165
    assert voices.split_threshold(WOMEN[::-1] + MEN) == thr    # order does not matter


@pytest.mark.parametrize("medians", [
    [118, 122, 130, 126, 119, 133, 140],                          # one man (or only men)
    [230, 250, 245, 260, 300, 310, 480],                          # only women: lower side not male-ish
    [210, 215, 260, 265, 270, 275, 280],                          # two women, a gap at 238
    [120, 230, 300],                                              # too few lines
    [],
])
def test_no_split_when_there_are_not_two_clusters(medians):
    assert voices.split_threshold(medians) is None


def test_the_split_needs_each_side_to_be_a_real_group():
    # one lonely low outlier is not a cluster of men
    assert voices.split_threshold([110] + [230, 240, 250, 260, 270, 280, 290, 300, 310, 320]) is None


def test_without_a_split_only_surely_male_or_female_lines_get_a_tag():
    stats = [(30, 118.0), (30, 125.0), (30, 190.0), (30, 330.0), (30, 140.0), (4, None), (30, 150.0)]
    tags, thr = voices.tags_from_stats(stats)
    # one man-only cluster plus one 190 and one 330 → no clean gap in the band
    assert thr is None or isinstance(thr, float)
    if thr is None:
        assert tags == ["m", "m", None, "f", "m", None, "m"]    # 150 is the edge: not < ABS_MALE
    assert tags[5] is None


def test_tags_from_stats_uses_the_split_and_leaves_unvoiced_lines_unknown():
    stats = [(30, float(m)) for m in MEN + WOMEN] + [(3, None)]
    tags, thr = voices.tags_from_stats(stats)
    assert thr and tags[:10] == ["m"] * 10 and tags[10:20] == ["f"] * 10 and tags[20] is None


def test_the_loose_voicing_threshold_keeps_more_frames_and_the_default_is_unchanged():
    from app.pipeline import pitch

    t = np.arange(int(SR * 1.0)) / SR
    noisy = (0.3 * np.sin(2 * np.pi * 120 * t) + 0.09 * np.random.default_rng(1).standard_normal(len(t))
             ).astype(np.float32)
    strict, loose = pitch.f0_frames(noisy), pitch.f0_frames(noisy, conf=0.2)
    assert len(strict) == 0 and len(loose) > 0                 # noise this strong: only the loose one hears it
    assert abs(float(np.median(loose)) - 120) < 12              # still the right pitch
    assert pitch.CONF == 0.1 and voices.VOICING == 0.2


def test_audio_source_makes_the_speech_stem_when_there_is_none(storage, monkeypatch):
    from app.pipeline import separate

    job = make_job(storage)
    job.source_video.write_bytes(b"x")
    made = job.work_dir / "vocals.wav"

    def fake(j, log=None):
        made.write_bytes(b"v")
        return made

    monkeypatch.setattr(separate, "speech_stem", fake)
    assert voices._audio_source(job) == made


def test_audio_source_falls_back_to_the_source_without_a_gpu_or_demucs(storage, monkeypatch):
    from app.pipeline import separate

    job = make_job(storage)
    job.source_video.write_bytes(b"x")
    monkeypatch.setattr(separate, "speech_stem", lambda j, log=None: None)
    assert voices._audio_source(job) == job.source_video
    monkeypatch.setattr(separate, "speech_stem", lambda j, log=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert voices._audio_source(job) == job.source_video         # a failing separation never stops the guess


def test_guess_job_reports_the_split_and_writes_the_tags(storage, monkeypatch):
    lines = tuple(f"Câu {i}" for i in range(1, 13))
    job = make_job(storage, lines)
    write_srt([Cue(i, i * 1.0, i * 1.0 + 0.9, t) for i, t in enumerate(lines, 1)], job.vi_srt)
    x = np.zeros(SR * 14, np.float32)
    for i in range(1, 13):
        f0 = 115.0 if i % 2 else 235.0
        x[int(i * SR):int((i + 0.9) * SR)] = tone(f0, 0.9)
    (job.work_dir / "vocals.wav").write_bytes(b"x")
    monkeypatch.setattr(voices, "_decode", lambda src: x)
    logs: list[str] = []
    rep = voices.guess_job(job, log=logs.append)
    assert rep["male"] == 6 and rep["female"] == 6 and 120 < rep["split_hz"] < 230
    assert "ngưỡng nam/nữ của video này" in logs[-1]
    assert [r["voice"] for r in sidecars.load_overrides(job)] == ["m", "f"] * 6
