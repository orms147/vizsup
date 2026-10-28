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
SPLIT_HZ = 165.0          # the old fixed split - kept for cue_gender(); the guess is per video now
MIN_VOICED = 8            # voiced 20 ms frames (~0.16 s) needed to say anything
VOICING = 0.2             # YIN threshold for the guess (pitch.CONF 0.1 kept only ~40 % of the lines)
_GENDER = {FEMALE: "high", MALE: "low"}      # speakers.voice_for's vocabulary

# Per-video split (2026-10-29, an AI-anime episode with a young male lead at 115-190 Hz and two
# young women at 210-520 Hz: the fixed 165 Hz and the 8-frame floor gave 2 male / 30 female /
# 50 unknown). The line medians of a dialogue form two clusters; the split is the biggest GAP
# between them, searched in SPLIT_BAND, and only when the lower cluster really is male-ish.
SPLIT_BAND = (150.0, 260.0)      # Hz the split may fall in
MIN_GAP_RATIO = 1.06             # the gap must be at least 6 % wide
MIN_LINES_FOR_SPLIT = 6
MALE_CLUSTER_MAX = 185.0         # the lower cluster's median must be below this
ABS_MALE, ABS_FEMALE = 150.0, 215.0   # without a split: surely a man / surely a woman, else unknown


def cue_gender(f0: np.ndarray, split: float = SPLIT_HZ) -> str | None:
    """'m' / 'f' from the F0 frames of ONE line against a fixed ``split``; None when too
    little is voiced. Pure."""
    if len(f0) < MIN_VOICED:
        return None
    return MALE if float(np.median(f0)) < split else FEMALE


def split_threshold(medians: list[float]) -> float | None:
    """The Hz that separates the men from the women among the lines' median F0s: the widest
    gap (ratio of neighbours) inside SPLIT_BAND, each side with >= 10 % of the lines, the
    lower side's median below MALE_CLUSTER_MAX. None when the lines do not form two
    clusters (one speaker, two women, a monotone narrator ...). Pure."""
    import math

    v = sorted(m for m in medians if m and m > 0)
    if len(v) < MIN_LINES_FOR_SPLIT:
        return None
    best: tuple[float, float] | None = None
    for lo, hi in zip(v, v[1:]):
        mid = (lo + hi) / 2
        if not SPLIT_BAND[0] <= mid <= SPLIT_BAND[1] or hi / lo < MIN_GAP_RATIO:
            continue
        below = [m for m in v if m < mid]
        if min(len(below), len(v) - len(below)) < max(2, 0.1 * len(v)):
            continue
        if float(np.median(below)) > MALE_CLUSTER_MAX:
            continue
        gap = math.log(hi / lo)
        if best is None or gap > best[0]:
            best = (gap, mid)
    return best[1] if best else None


def line_stats(x: np.ndarray, cues: list, sr: int = pitch.SR,
               conf: float = VOICING) -> list[tuple[int, float | None]]:
    """(voiced frames, median F0) per cue; the median is None below MIN_VOICED frames."""
    out: list[tuple[int, float | None]] = []
    for c in cues:
        a, b = int(max(0.0, c.start) * sr), int(max(0.0, c.end) * sr)
        f = pitch.f0_frames(x[a:b], sr, conf) if b > a else np.zeros(0)
        out.append((len(f), float(np.median(f)) if len(f) >= MIN_VOICED else None))
    return out


def tags_from_stats(stats: list[tuple[int, float | None]]) -> tuple[list[str | None], float | None]:
    """Gender tags (and the split used, None = absolute thresholds) from :func:`line_stats`."""
    meds = [m for _n, m in stats if m]
    thr = split_threshold(meds)
    out: list[str | None] = []
    for _n, m in stats:
        if not m:
            out.append(None)
        elif thr is not None:
            out.append(MALE if m < thr else FEMALE)
        else:
            out.append(MALE if m < ABS_MALE else FEMALE if m > ABS_FEMALE else None)
    return out, thr


def guess_cues(x: np.ndarray, cues: list, sr: int = pitch.SR) -> list[str | None]:
    """Gender tag (or None) for every cue (``.start`` / ``.end``) of mono float audio ``x``."""
    return tags_from_stats(line_stats(x, cues, sr))[0]


def _audio_source(job: Job, log=None):
    """The separated voice (vocals.wav) when there is one; else try to make it (Demucs -
    local and free, GPU only: ~10 s for a 2-minute clip), else the source audio / video."""
    voc = job.work_dir / "vocals.wav"
    if voc.exists():
        return voc
    if job.source_video.exists():
        from app.pipeline import separate

        try:
            made = separate.speech_stem(job, log=log)
        except Exception:  # noqa: BLE001 - an aid: the mix still has the pitch
            made = None
        if made is not None and made.exists():
            return made
    for p in (job.source_audio, job.source_video):
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
    src = _audio_source(job, log)
    if src is None:
        return None
    if singing(job) == "yes":
        if log:
            log("ℹ Video chủ yếu là hát — bỏ qua đoán giọng nam/nữ")
        return None
    cues = parse_srt(job.vi_srt)
    tags, thr = tags_from_stats(line_stats(_decode(src), cues))
    rows = sidecars.load_overrides(job)
    rows = [dict(r) if isinstance(r, dict) else {} for r in rows]
    n = apply_tags(rows, tags, overwrite=overwrite)
    if n:
        sidecars.save_overrides(job, rows)
    rep = {"set": n, "female": tags.count(FEMALE), "male": tags.count(MALE),
           "unknown": tags.count(None), "split_hz": round(thr) if thr else None}
    if log:
        log(f"✓ Đoán giọng theo cao độ: {rep['male']} câu nam · {rep['female']} câu nữ"
            f" · {rep['unknown']} câu không rõ"
            + (f" (ngưỡng nam/nữ của video này ≈ {rep['split_hz']} Hz)" if thr
               else " (không tách được hai nhóm giọng — chỉ gán câu chắc chắn)"))
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
