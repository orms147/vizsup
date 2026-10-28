"""F-4: the user's own commentary — the one thing YouTube, Meta and TikTok all name as
making a reused clip acceptable ("add a storyline and commentary", docs/plan/F-roadmap).

A commentary note is the user's text (spoken by a TTS voice that differs from the dub,
or their own recording) placed at a moment of the SOURCE video, in one of two modes:
- ``pause``: the picture freezes and the original audio stops while the note is spoken,
  then the video goes on — everything after it shifts later (intro = at 0, outro = at
  the end). Nothing is talked over.
- ``over``: spoken over the running video; the original ducks under it like the dub.

Notes live in ``commentary.json`` (not in vi.srt, so translation memory, QA and the
cn/vi pairing never see them) and count towards the approved revision. Render maps
source times to the output timeline with :func:`to_out`.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.models import Cue, Job
from app.fsutil import atomic_write_text
from app.pipeline import speak

FILE = "commentary.json"
DIR = "commentary"
LEAD = 0.25            # pause mode: silence before / after the note inside the freeze
TAIL = 0.25
WPS = 2.8              # Vietnamese TTS words per second, for estimates before synthesis
MODES = ("pause", "over")


@dataclass
class Note:
    at: float                     # source-timeline seconds
    text: str
    mode: str = "pause"           # "pause" | "over"
    voice: str | None = None      # TTS voice id; None → a voice other than the dub's
    audio: str | None = None      # own recording, relative to the job dir (wins over TTS)
    id: str = field(default="")

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            self.mode = "pause"
        if not self.id:
            self.id = hashlib.sha1(f"{self.at}|{self.text}".encode()).hexdigest()[:8]

    def est_seconds(self) -> float:
        return len(self.text.split()) / WPS


@dataclass
class Voiced:
    note: Note
    path: Path
    dur: float


def load(job: Job) -> list[Note]:
    try:
        data = json.loads((job.work_dir / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("notes") if isinstance(data, dict) else None
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        text, audio = str(r.get("text", "")), r.get("audio") or None
        if not text.strip() and not audio:
            continue
        out.append(Note(at=float(r.get("at", 0.0)), text=text, mode=r.get("mode", "pause"),
                        voice=r.get("voice") or None, audio=audio, id=str(r.get("id", ""))))
    return sorted(out, key=lambda n: n.at)


def save(job: Job, notes: list[Note]) -> None:
    path = job.work_dir / FILE
    if not notes:
        path.unlink(missing_ok=True)       # no notes == no file (approval hash unchanged)
        return
    atomic_write_text(path, json.dumps({"notes": [asdict(n) for n in sorted(notes, key=lambda n: n.at)]},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def pick_voice(voices: list[str], dub_voice: str | None) -> str | None:
    """A voice that is NOT the dub's, so the commentary sounds like another person:
    prefer the other gender among Vietnamese voices. None when there is no choice."""
    vi = [v for v in voices if v.lower().startswith("vi-")] or list(voices)
    others = [v for v in vi if v != dub_voice]
    return others[0] if others else None


def import_audio(job: Job, src: Path) -> str:
    """Copy the user's own recording into the job; returns its job-relative path."""
    d = job.work_dir / DIR
    d.mkdir(exist_ok=True)
    dst = d / f"own_{hashlib.sha1(src.read_bytes()).hexdigest()[:10]}{src.suffix.lower()}"
    if not dst.exists():
        shutil.copyfile(src, dst)
    return dst.relative_to(job.work_dir).as_posix()


def synthesize(job: Job, notes: list[Note], *, provider: str, dub_voice: str | None,
               rate: str | None = None, log: Callable[[str], None] | None = None) -> list[Voiced]:
    """Audio for every note: the user's recording, or TTS in the note's voice (a voice
    other than the dub's by default), cached in ``commentary/`` by text+voice+rate."""
    from app.pipeline.assemble import probe_duration
    from app.pipeline.tts import _synth_retry
    from app.providers.registry import get_tts

    if not notes:
        return []
    d = job.work_dir / DIR
    d.mkdir(exist_ok=True)
    tts = None
    rules = speak.load_rules(job)
    out: list[Voiced] = []
    for n in notes:
        if n.audio and (job.work_dir / n.audio).exists():
            path = job.work_dir / n.audio
        elif n.text.strip():
            tts = tts or get_tts(provider)
            voice = n.voice or pick_voice(_voices(tts), dub_voice) or dub_voice
            said = speak.spoken(n.text, rules)       # the written note stays as typed
            key = hashlib.sha1(f"{provider}|{voice}|{rate}|{said}".encode()).hexdigest()[:16]
            path = d / f"tts_{key}.mp3"
            if not (path.exists() and path.stat().st_size > 0):
                _synth_retry(tts, said, path, voice=voice, rate=rate,
                             sleep=__import__("time").sleep)
        else:
            continue
        dur = probe_duration(path)
        if dur > 0:
            out.append(Voiced(n, path, dur))
    if log and out:
        log(f"✓ Bình luận của bạn: {len(out)} đoạn · {sum(v.dur for v in out):.0f} s")
    return out


def _voices(tts) -> list[str]:
    try:
        return list(tts.voices() or [])
    except Exception:  # noqa: BLE001
        return []


# --- the output timeline -----------------------------------------------------------------

def inserts(voiced: list[Voiced]) -> list[tuple[float, float]]:
    """(source time, freeze length) for every pause note, in time order."""
    return sorted((v.note.at, LEAD + v.dur + TAIL) for v in voiced if v.note.mode == "pause")


def to_out(t: float, ins: list[tuple[float, float]], *, end: bool = False) -> float:
    """Source time → output time. A pause at ``at`` comes BEFORE what starts at ``at``
    but AFTER what ends there (``end=True``)."""
    return t + sum(d for at, d in ins if (at < t if end else at <= t))


def shift_cues(cues: list[Cue], ins: list[tuple[float, float]]) -> list[Cue]:
    if not ins:
        return cues
    return [Cue(index=c.index, start=to_out(c.start, ins), end=to_out(c.end, ins, end=True),
                text=c.text) for c in cues]


def speech_cues(voiced: list[Voiced], ins: list[tuple[float, float]], *,
                base_index: int = 100_000) -> list[tuple[Cue, Path]]:
    """Each note as a dub "line" on the output timeline (for the windowed dub track)."""
    out = []
    for k, v in enumerate(sorted(voiced, key=lambda v: v.note.at)):
        start = to_out(v.note.at, ins, end=True)
        if v.note.mode == "pause":
            start += LEAD                  # inside its own freeze
        out.append((Cue(index=base_index + k, start=start, end=start + v.dur,
                        text=v.note.text), v.path))
    return out


def subtitle_cues(voiced: list[Voiced], ins: list[tuple[float, float]]) -> list[Cue]:
    """The notes' text, timed like their speech, for the "Comment" subtitle style."""
    return [Cue(index=c.index, start=c.start, end=c.end, text=c.text)
            for c, _p in speech_cues(voiced, ins) if c.text.strip()]


def share(voiced_seconds: float, source_seconds: float, inserted: float) -> float:
    """Share of the output's runtime that is the user's own commentary (0..1)."""
    total = source_seconds + inserted
    return voiced_seconds / total if total > 0 else 0.0


def estimate(notes: list[Note], source_seconds: float) -> tuple[float, float]:
    """(estimated commentary seconds, estimated share) before anything is synthesized."""
    secs = sum(n.est_seconds() for n in notes)
    added = sum(LEAD + n.est_seconds() + TAIL for n in notes if n.mode == "pause")
    return secs, share(secs, source_seconds, added)
