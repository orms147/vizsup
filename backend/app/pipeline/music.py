"""Background music ("Nhạc nền") the user adds on top of the render — for the whole video
or per section.

A project keeps a list of clips in ``music.json``; each clip is an audio file copied into
``<job>/music/`` (so the project stays self-contained) placed on the VIDEO (source)
timeline:

- ``start`` / ``end`` — seconds on the source timeline; ``end = None`` = to the end of
  the video. A clip shorter than its span loops (``loop``) or just ends.
- ``volume`` (linear gain, 0..2), ``fade_in`` / ``fade_out`` (seconds),
- ``duck`` — pushed down automatically while the Vietnamese voice speaks.

Render maps the span to the OUTPUT timeline with the same shift as the subtitles
(:func:`app.pipeline.commentary.to_out`): F-4 "pause" notes freeze the picture and push
everything after them later, so music placed after a pause stays on the picture it was
placed on. A clip spanning a pause keeps playing through the freeze (under the note),
i.e. its output span is the source span plus the pauses inside it; a clip that starts
exactly at a pause starts with the freeze.

Music is a render-time mix setting like the audio mode and the volumes — it is neither
voiced nor burned text — so ``music.json`` does NOT count towards the approved subtitle
revision (approval.py); every render reads the current file.

Everything here is pure (no ffmpeg run) except :func:`import_file` (a copy) and the
duration probe :func:`place` takes as an argument.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.models import Job
from app.fsutil import atomic_write_text

FILE = "music.json"
DIR = "music"
EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma")
RATE = 48000

VOLUME_DEFAULT = 0.3          # ≈ −10 dB: a bed under the voice, not a second lead
VOLUME_MAX = 2.0
FADE_IN_DEFAULT = 1.0
FADE_OUT_DEFAULT = 2.0
FADE_MAX = 30.0
# ducking under the Vietnamese voice: ≈ 12 dB down while the dub speaks, back within
# ~0.5 s after a line (sidechaincompress keyed by the loudnormed dub; measured 2026-10-27
# on a real render: a 14 kHz marker in the music read −41.9 dB free, −53.9 dB under lines)
DUCK = "threshold=0.04:ratio=5:attack=30:release=500:makeup=1:level_sc=1"
LIMIT = "alimiter=limit=0.95:level=0:latency=1"   # music + voice + bed summed: no clipping


@dataclass
class Clip:
    file: str                       # job-relative path (music/…)
    start: float = 0.0              # VIDEO (source) timeline seconds
    end: float | None = None        # None → to the end of the video
    volume: float = VOLUME_DEFAULT  # linear gain
    fade_in: float = FADE_IN_DEFAULT
    fade_out: float = FADE_OUT_DEFAULT
    loop: bool = True               # repeat when the file is shorter than its span
    duck: bool = True               # quieter under the Vietnamese voice
    name: str = ""                  # the original file name, for the UI
    id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.id:
            self.id = hashlib.sha1(f"{self.file}|{self.start}|{self.end}".encode()).hexdigest()[:8]


def _num(v, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) != float("inf") else default   # NaN / inf → default


def clean(clip: Clip) -> Clip:
    """Clamp a clip to sane values (pure): times ≥ 0, end after start (or None),
    volume 0..VOLUME_MAX, fades 0..FADE_MAX."""
    start = max(0.0, _num(clip.start, 0.0))
    end = None if clip.end is None else _num(clip.end, -1.0)
    if end is not None and end <= start + 0.05:
        end = None if end < 0 else start + 0.05
    return Clip(file=str(clip.file), start=round(start, 3),
                end=None if end is None else round(end, 3),
                volume=round(min(VOLUME_MAX, max(0.0, _num(clip.volume, VOLUME_DEFAULT))), 3),
                fade_in=round(min(FADE_MAX, max(0.0, _num(clip.fade_in, 0.0))), 3),
                fade_out=round(min(FADE_MAX, max(0.0, _num(clip.fade_out, 0.0))), 3),
                loop=bool(clip.loop), duck=bool(clip.duck), name=str(clip.name or ""),
                id=str(clip.id or ""))


def validate(job: Job, clips: list[Clip]) -> list[str]:
    """Problems the user should see (Vietnamese), one per clip at most. Pure apart from
    checking that the files exist."""
    out = []
    for k, c in enumerate(clips, 1):
        if not c.file or not (job.work_dir / c.file).is_file():
            out.append(f"Đoạn nhạc {k}: không tìm thấy tệp {c.name or c.file}")
        elif c.end is not None and c.end <= c.start:
            out.append(f"Đoạn nhạc {k}: điểm kết thúc phải sau điểm bắt đầu")
        elif c.volume <= 0:
            out.append(f"Đoạn nhạc {k}: âm lượng 0 — sẽ không nghe thấy")
    return out


def load(job: Job) -> list[Clip]:
    """The project's clips (cleaned, in start order); bad rows are dropped."""
    try:
        data = json.loads((job.work_dir / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("clips") if isinstance(data, dict) else None
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("file"):
            continue
        end = r.get("end")
        out.append(clean(Clip(file=str(r["file"]), start=r.get("start", 0.0),
                              end=None if end is None else end,
                              volume=r.get("volume", VOLUME_DEFAULT),
                              fade_in=r.get("fade_in", FADE_IN_DEFAULT),
                              fade_out=r.get("fade_out", FADE_OUT_DEFAULT),
                              loop=r.get("loop", True), duck=r.get("duck", True),
                              name=r.get("name", ""), id=str(r.get("id", "")))))
    return sorted(out, key=lambda c: c.start)


def save(job: Job, clips: list[Clip]) -> None:
    path = job.work_dir / FILE
    if not clips:
        path.unlink(missing_ok=True)          # no clips == no file
        return
    rows = [asdict(clean(c)) for c in sorted(clips, key=lambda c: c.start)]
    atomic_write_text(path, json.dumps({"clips": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


def import_file(job: Job, src: Path) -> str:
    """Copy an audio file into ``<job>/music/`` (content-addressed, so importing the same
    song twice keeps one copy); returns its job-relative path."""
    d = job.work_dir / DIR
    d.mkdir(exist_ok=True)
    h = hashlib.sha1()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    dst = d / f"bgm_{h.hexdigest()[:10]}{src.suffix.lower()}"
    if not dst.exists():
        shutil.copyfile(src, dst)
    return dst.relative_to(job.work_dir).as_posix()


def summary(clips: list[Clip]) -> str:
    """'Nhạc nền: 2 đoạn' for the render page ('' when there is none)."""
    if not clips:
        return ""
    if len(clips) == 1 and clips[0].start <= 0 and clips[0].end is None:
        return "Nhạc nền: cả video"
    return f"Nhạc nền: {len(clips)} đoạn"


# --- render: output timeline + ffmpeg graph ------------------------------------------------

def place(clips: list[Clip], ins: list[tuple[float, float]], out_len: float, *,
          duration: Callable[[Path], float], job: Job) -> list[dict]:
    """Each clip on the OUTPUT timeline (F-4 pauses shift it like the subtitles):
    ``{file, start, play, loop, volume, fade_in, fade_out, duck}`` — ``play`` is how long
    it sounds (its span, or the file's length when it is shorter and not looped; None
    when unbounded). Clips that are missing, silent or fall after the end are left out.
    Pure apart from ``duration`` (an injected probe)."""
    from app.pipeline.commentary import to_out

    out: list[dict] = []
    for c in clips:
        path = job.work_dir / c.file
        if c.volume <= 0 or not path.is_file():
            continue
        file_dur = duration(path)
        if file_dur <= 0:
            continue
        # a pause AT the start belongs to the clip (it plays under the freeze); one at the
        # end does not — the same rule as a subtitle that ends there
        start = to_out(c.start, ins, end=True)
        end = out_len if c.end is None else to_out(c.end, ins, end=True)
        if out_len > 0:
            end = min(end, out_len) if end else out_len
        span = (end - start) if end and end > 0 else None
        if span is not None and span <= 0.05:
            continue
        loop = c.loop and span is not None and file_dur < span
        play = span if (loop or span is not None and file_dur >= span) else file_dur
        fi = min(c.fade_in, play / 2) if play else c.fade_in
        fo = min(c.fade_out, play / 2) if play else 0.0
        out.append({"file": c.file, "start": round(start, 3),
                    "play": None if play is None else round(play, 3), "loop": loop,
                    "volume": c.volume, "fade_in": round(fi, 3), "fade_out": round(fo, 3),
                    "duck": c.duck})
    return out


def input_args(placed: list[dict]) -> list[str]:
    """ffmpeg input options for the placed clips (paths relative to the job dir); a
    looped clip repeats at the demuxer (-stream_loop) and is cut to its span."""
    args: list[str] = []
    for p in placed:
        if p["loop"]:
            args += ["-stream_loop", "-1"]
        args += ["-i", p["file"]]
    return args


def clip_filter(p: dict) -> str:
    """One clip's chain: 48 kHz stereo, cut to its span, gain, fades, then delayed to its
    start on the output timeline. Pure."""
    f = [f"aresample={RATE}", f"aformat=sample_fmts=fltp:sample_rates={RATE}:"
         "channel_layouts=stereo"]
    play = p.get("play")
    if play:
        f += [f"atrim=end={play:.3f}", "asetpts=PTS-STARTPTS"]
    if abs(p["volume"] - 1.0) > 1e-3:
        f.append(f"volume={p['volume']:.3f}")
    if p["fade_in"] > 0:
        f.append(f"afade=t=in:st=0:d={p['fade_in']:.3f}")
    if play and p["fade_out"] > 0:
        f.append(f"afade=t=out:st={max(0.0, play - p['fade_out']):.3f}:d={p['fade_out']:.3f}")
    ms = int(round(p["start"] * 1000))
    if ms > 0:
        f.append(f"adelay={ms}:all=1")
    return ",".join(f)


def _mix(labels: list[str], out: str) -> str:
    if len(labels) == 1:
        return f"{labels[0]}anull{out}"
    return f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0{out}"


def graph(placed: list[dict], first_input: int, *, key: str | None) -> tuple[list[str], str]:
    """Filter-graph parts for the music bed: every clip from input ``first_input`` on,
    the ducked ones mixed and pushed down by ``key`` (an already padded dub label; None =
    no Vietnamese voice → nothing to duck under), the rest mixed as they are. Returns
    (parts, output label). Pure."""
    parts: list[str] = []
    ducked: list[str] = []
    plain: list[str] = []
    for k, p in enumerate(placed):
        lbl = f"[mus{k}]"
        parts.append(f"[{first_input + k}:a]{clip_filter(p)}{lbl}")
        (ducked if p["duck"] and key else plain).append(lbl)
    groups: list[str] = []
    if ducked:
        parts.append(_mix(ducked, "[musdk]"))
        parts.append(f"[musdk]{key}sidechaincompress={DUCK}[musd]")
        groups.append("[musd]")
    if plain:
        parts.append(_mix(plain, "[musp]"))
        groups.append("[musp]")
    if len(groups) == 1:
        return parts, groups[0]
    parts.append(_mix(groups, "[music]"))
    return parts, "[music]"
