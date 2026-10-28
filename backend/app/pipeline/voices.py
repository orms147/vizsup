"""Per-line voice by gender: a woman's line gets HoaiMy, a man's NamMinh.

The dub used ONE voice for the whole video. In a series with a man and a woman talking
that sounds flat. Each line carries a tag in its row of ``overrides.json`` (the same
positional sidecar as gain / mute, so split / merge / undo in the editor keep it and
the approved revision sees it): ``"f"`` (female voice), ``"m"`` (male voice) or absent
(the voice picked on the render page).

``guess_job`` fills the tags before the edit gate from the PITCH of each line: median F0
of its clearly voiced frames (``pitch.f0_frames``, local, nothing uploaded) on
``vocals.wav`` when the music was separated, else the source audio. Below
``SPLIT_HZ`` → ``m``, above → ``f``, too little voice → untouched. A tag the user set is
never overwritten. The render page's "Giọng theo nhân vật (nam/nữ)" decides whether TTS
uses the tags (``tts.synthesize(voice_by_gender=True)``).

Who is speaking is NOT known per line (no diarisation), so a series' characters are not
used here — pitch is the only evidence.
"""
from __future__ import annotations

import subprocess

import numpy as np

from app.models import Job
from app.pipeline import pitch, sidecars

FEMALE, MALE = "f", "m"
TAGS = (FEMALE, MALE)
SPLIT_HZ = 165.0          # median F0 below → a man, above → a woman / child
MIN_VOICED = 8            # voiced 20 ms frames (~0.16 s) needed to say anything
_GENDER = {FEMALE: "high", MALE: "low"}      # speakers.voice_for's vocabulary


def cue_gender(f0: np.ndarray) -> str | None:
    """'m' / 'f' from the F0 frames of ONE line; None when too little is voiced. Pure."""
    if len(f0) < MIN_VOICED:
        return None
    return MALE if float(np.median(f0)) < SPLIT_HZ else FEMALE


def guess_cues(x: np.ndarray, cues: list, sr: int = pitch.SR) -> list[str | None]:
    """Gender tag (or None) for every cue (``.start`` / ``.end``) of mono float audio ``x``."""
    out: list[str | None] = []
    for c in cues:
        a, b = int(max(0.0, c.start) * sr), int(max(0.0, c.end) * sr)
        out.append(cue_gender(pitch.f0_frames(x[a:b], sr)) if b > a else None)
    return out


def _audio_source(job: Job):
    for p in (job.work_dir / "vocals.wav", job.source_audio, job.source_video):
        if p.exists():
            return p
    return None


def _decode(src) -> np.ndarray:
    from app.ffmpegutil import ffmpeg_bin

    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(src), "-vn", "-ac", "1",
                          "-ar", str(pitch.SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def apply_tags(rows: list[dict], tags: list[str | None], *, overwrite: bool = False) -> int:
    """Write the guessed tags into the override rows (padded to ``len(tags)``); a tag
    already on a row stays unless ``overwrite``. Returns how many rows were set."""
    while len(rows) < len(tags):
        rows.append({"gain_db": 0.0, "mute": False})
    n = 0
    for row, tag in zip(rows, tags):
        if tag and (overwrite or row.get("voice") not in TAGS):
            if row.get("voice") != tag:
                row["voice"] = tag
                n += 1
    return n


def guess_job(job: Job, *, overwrite: bool = False, log=None) -> dict | None:
    """Tag every line of vi.srt in ``overrides.json``. None when there is nothing to
    measure (no vi.srt / audio); never raises into the caller's pipeline."""
    from app.pipeline.srt import parse_srt
    from app.pipeline.speakers import singing

    if not job.vi_srt.exists():
        return None
    src = _audio_source(job)
    if src is None:
        return None
    if singing(job) == "yes":
        if log:
            log("ℹ Video chủ yếu là hát — bỏ qua đoán giọng nam/nữ")
        return None
    cues = parse_srt(job.vi_srt)
    tags = guess_cues(_decode(src), cues)
    rows = sidecars.load_overrides(job)
    rows = [dict(r) if isinstance(r, dict) else {} for r in rows]
    n = apply_tags(rows, tags, overwrite=overwrite)
    if n:
        sidecars.save_overrides(job, rows)
    rep = {"set": n, "female": tags.count(FEMALE), "male": tags.count(MALE),
           "unknown": tags.count(None)}
    if log:
        log(f"✓ Đoán giọng theo cao độ: {rep['male']} câu nam · {rep['female']} câu nữ"
            f" · {rep['unknown']} câu không rõ")
    return rep


def voice_name(voices: list[str], tag: str | None) -> str | None:
    """The provider voice for a tag ('f' → HoaiMy …), None when it has none."""
    from app.pipeline.speakers import voice_for

    return voice_for(voices, _GENDER.get(tag or ""))


def line_voices(rows: list[dict], voices: list[str], default: str | None) -> list[str | None]:
    """The voice for every line in order: its tag's voice, else ``default``."""
    out = []
    for r in rows:
        tag = r.get("voice") if isinstance(r, dict) else None
        out.append(voice_name(voices, tag) or default)
    return out
