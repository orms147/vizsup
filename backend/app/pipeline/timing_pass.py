"""Subtitle timing post-pass, Netflix / Aegisub "timing post-processor" style.

ASR and OCR hand back cue times that flicker: a line ends 0.2 s before the next one
starts (the picture goes blank for a blink), two lines touch or overlap, a one-word line
is on screen for 0.3 s. This pass tidies the CUE TIMES only — never the text, never the
order — with the rules broadcasters use (Netflix Timed Text Style Guide, timing section;
Aegisub's lead-in/out + chaining):

- **min gap** — neighbours are at least 2 frames apart (any fps): a touching or
  overlapping pair gets a 2-frame gap by trimming the EARLIER line's end (the later line's
  first words are never hidden);
- **chaining** — a gap shorter than ``CHAIN_GAP`` (0.5 s) is closed down to the min gap by
  extending the earlier line's end;
- **min duration** — a line shorter than ``MIN_FRAMES`` (20 frames ≈ 0.8 s) is extended at
  its END into free time, never into its neighbour (``blocked`` counts the ones that
  could not reach it);
- **scene cuts** (optional, off by default — fast-cutting anime would snap everywhere) —
  an edge within ``SCENE_REACH_FRAMES`` of a cut snaps to it (a start onto the cut's first
  frame, an end 2 frames before the cut), and chaining/lengthening never runs over a cut.

Pure and duck-typed on ``.start`` / ``.end`` / optional ``.locked``, like ``snap``, so the
pipeline's ``Cue`` and the editor's ``EditorCue`` share it. Locked cues never move and act
as fixed neighbours.
"""
from __future__ import annotations

import json
import re
import subprocess
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from app.fsutil import atomic_write_text

DEFAULT_FPS = 25.0
MIN_GAP_FRAMES = 2
CHAIN_GAP = 0.5            # s — gaps shorter than this are closed
MIN_FRAMES = 20            # ≈ 0.8 s at 25 fps
SCENE_REACH_FRAMES = 6
SCENE_THRESHOLD = 0.3
SHRINK_FLOOR = 0.2         # s — never trim a line below this to make room
SNAP_FLOOR = 0.4           # s — a scene snap never leaves a line shorter than this
TOL = 0.0015               # s — "already there" (times are kept to the millisecond)


@dataclass
class TimingReport:
    closed: list[int] = field(default_factory=list)       # chained: end extended over a short gap
    min_gap: list[int] = field(default_factory=list)      # touching / overlapping pair separated
    lengthened: list[int] = field(default_factory=list)   # extended to the minimum duration
    blocked: list[int] = field(default_factory=list)      # still too short: neighbours in the way
    snapped: list[int] = field(default_factory=list)      # an edge moved onto a scene cut
    locked: list[int] = field(default_factory=list)       # would have changed, but locked
    unresolved: list[int] = field(default_factory=list)   # overlap that could not be separated
    changed: list[int] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.closed:
            parts.append(f"nối {len(self.closed)} khoảng trống ngắn")
        if self.min_gap:
            parts.append(f"tách {len(self.min_gap)} cặp sát/chồng nhau")
        if self.lengthened:
            parts.append(f"kéo dài {len(self.lengthened)} câu quá ngắn")
        if self.snapped:
            parts.append(f"bám {len(self.snapped)} điểm đổi cảnh")
        return ", ".join(parts)


def _first_cut_after(cuts: list[float], t: float) -> float | None:
    i = bisect_right(cuts, t + 1e-6)
    return cuts[i] if i < len(cuts) else None


def _nearest_cut(cuts: list[float], t: float, reach: float) -> float | None:
    best = None
    i = bisect_right(cuts, t)
    for j in (i - 1, i):
        if 0 <= j < len(cuts) and abs(cuts[j] - t) <= reach:
            if best is None or abs(cuts[j] - t) < abs(best - t):
                best = cuts[j]
    return best


def plan(cues: list, fps: float | None = None, *, scenes: list[float] | None = None,
         duration: float | None = None, min_frames: int = MIN_FRAMES,
         chain_gap: float = CHAIN_GAP, reach_frames: int = SCENE_REACH_FRAMES,
         ) -> tuple[list[tuple[float, float]], TimingReport]:
    """New (start, end) for every cue, in order, plus what changed. Does not mutate."""
    fps = fps if fps and fps > 0 else DEFAULT_FPS
    gap = MIN_GAP_FRAMES / fps
    min_dur = min_frames / fps
    cuts = sorted(scenes) if scenes else []
    n = len(cues)
    t = [[float(c.start), float(c.end)] for c in cues]
    orig = [tuple(x) for x in t]
    lock = [bool(getattr(c, "locked", False)) for c in cues]
    rep = TimingReport()
    touched: dict[str, set[int]] = {k: set() for k in
                                    ("closed", "min_gap", "lengthened", "snapped", "locked", "unresolved")}

    def limit_end(i: int) -> float | None:
        """Latest end for cue i: before the next cue (min gap) and the video's end."""
        lim = t[i + 1][0] - gap if i + 1 < n else None
        if duration is not None:
            lim = duration if lim is None else min(lim, duration)
        return lim

    def cap_at_cut(old_end: float, new_end: float) -> float:
        c = _first_cut_after(cuts, old_end)
        if c is not None and c <= new_end + 1e-6:
            return max(old_end, c - gap)
        return new_end

    # 1) scene cuts (optional)
    if cuts:
        reach = reach_frames / fps
        for i in range(n):
            if lock[i]:
                continue
            s, e = t[i]
            cs = _nearest_cut(cuts, s, reach)
            if cs is not None and abs(cs - s) > TOL:
                floor = t[i - 1][1] + gap if i else 0.0
                if cs >= floor - TOL and e - cs >= SNAP_FLOOR:
                    t[i][0] = cs
                    touched["snapped"].add(i)
            ce = _nearest_cut(cuts, e, reach)
            if ce is not None:
                target = ce - gap
                top = limit_end(i)
                if abs(target - e) > TOL and t[i][0] + SNAP_FLOOR <= target \
                        and (top is None or target <= top + TOL):
                    t[i][1] = target
                    touched["snapped"].add(i)

    # 2) minimum duration, extended at the end into free time
    for i in range(n):
        if lock[i]:
            continue
        s, e = t[i]
        if e - s >= min_dur - TOL:
            continue
        want = s + min_dur
        top = limit_end(i)
        new_end = want if top is None else min(want, top)
        if cuts:
            new_end = cap_at_cut(e, new_end)
        if new_end > e + TOL:
            t[i][1] = new_end
            touched["lengthened"].add(i)
        if t[i][1] - s < min_dur - TOL:
            rep.blocked.append(i)

    # 3) gaps: separate touching/overlapping pairs, chain short gaps
    for i in range(n - 1):
        a, b = t[i], t[i + 1]
        g = b[0] - a[1]
        if g >= chain_gap or abs(g - gap) <= TOL:
            continue
        if g > gap:                                   # closing a short gap
            if lock[i]:
                touched["locked"].add(i)
                continue
            new_end = b[0] - gap
            if cuts:
                new_end = cap_at_cut(a[1], new_end)
            if new_end > a[1] + TOL:
                a[1] = new_end
                touched["closed"].add(i)
        elif not lock[i] and b[0] - gap - a[0] >= SHRINK_FLOOR:   # too tight / overlapping
            a[1] = b[0] - gap
            touched["min_gap"].add(i)
        elif not lock[i + 1] and b[1] - (a[1] + gap) >= SHRINK_FLOOR:
            b[0] = a[1] + gap
            touched["min_gap"].add(i + 1)
        else:
            (touched["locked"] if lock[i] and lock[i + 1] else touched["unresolved"]).add(i)

    if duration is not None:
        for i in range(n):
            if not lock[i] and t[i][1] > duration and t[i][0] < duration:
                t[i][1] = duration

    out = [(round(s, 3), round(e, 3)) for s, e in t]
    rep.closed = sorted(touched["closed"])
    rep.min_gap = sorted(touched["min_gap"])
    rep.lengthened = sorted(touched["lengthened"])
    rep.snapped = sorted(touched["snapped"])
    rep.locked = sorted(touched["locked"])
    rep.unresolved = sorted(touched["unresolved"])
    rep.blocked = sorted(set(rep.blocked))
    rep.changed = [i for i, (o, nw) in enumerate(zip(orig, out))
                   if abs(o[0] - nw[0]) > TOL or abs(o[1] - nw[1]) > TOL]
    return out, rep


def apply(cues: list, fps: float | None = None, **kw) -> TimingReport:
    """Run :func:`plan` and write the new times into ``cues`` in place."""
    times, rep = plan(cues, fps, **kw)
    for i in rep.changed:
        cues[i].start, cues[i].end = times[i]
    return rep


# --- the video side: frame rate and scene cuts -------------------------------------------

def probe_fps(path: Path) -> float:
    """Frame rate of the first video stream; DEFAULT_FPS when it can't be read."""
    from app.ffmpegutil import ffprobe_bin

    try:
        out = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=avg_frame_rate,r_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True, timeout=60).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_FPS
    for tok in out:
        try:
            num, _, den = tok.partition("/")
            v = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 10 <= v <= 120:
            return v
    return DEFAULT_FPS


_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def parse_showinfo(stderr: str) -> list[float]:
    return sorted({round(float(m.group(1)), 3) for m in _PTS.finditer(stderr or "")})


def detect_scenes(video: Path, cache: Path | None = None, *, threshold: float = SCENE_THRESHOLD,
                  log=None) -> list[float]:
    """Cut times (s) from ONE ffmpeg pass (``select='gt(scene,T)',showinfo`` on a 320 px
    copy), cached in ``cache`` (scenes.json) keyed by file size + mtime + threshold."""
    from app.ffmpegutil import ffmpeg_bin

    st = video.stat()
    key = f"{st.st_size}:{st.st_mtime_ns}:{threshold}"
    if cache is not None:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("key") == key:
                return [float(x) for x in data["cuts"]]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if log:
        log("Dò điểm đổi cảnh…")
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(video), "-an", "-vf",
         f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg scene detection failed ({proc.returncode})")
    cuts = parse_showinfo(proc.stderr)
    if cache is not None:
        atomic_write_text(cache, json.dumps({"key": key, "cuts": cuts}))
    return cuts


def run_srt(job, srt_path, *, scenes: bool = False, log=None) -> TimingReport | None:
    """Pipeline step: tidy an .srt in the job dir and rewrite it when something changed.
    Shared by the desktop worker and the CLI. None = nothing to do (no cues)."""
    from app.pipeline.assemble import probe_duration
    from app.pipeline.srt import parse_srt, write_srt

    cues = parse_srt(srt_path)
    if not cues:
        return None
    src = job.source_video if job.source_video.exists() else None
    fps = probe_fps(src) if src else DEFAULT_FPS
    duration = (probe_duration(src) or None) if src else None
    cuts = None
    if scenes and src:
        try:
            cuts = detect_scenes(src, job.work_dir / "scenes.json", log=log)
        except Exception as exc:  # noqa: BLE001 - the scene snap is an extra
            if log:
                log(f"⚠ Bỏ qua bám điểm đổi cảnh ({type(exc).__name__}: {exc})")
    rep = apply(cues, fps, scenes=cuts, duration=duration)
    if rep.changed:
        write_srt(cues, srt_path)
    if log:
        log("✓ Chỉnh thời gian chuẩn: " + (rep.summary() or "không cần đổi gì"))
    return rep
