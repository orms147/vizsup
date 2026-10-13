"""Snap cue edges to the voice ("tự canh lời"): a subtitle starts when the speaker
starts and stops when the speaker stops.

Pure and duck-typed — works on anything with ``.start`` / ``.end`` (and optional
``.locked``), so the pipeline's ``Cue`` and the editor's ``EditorCue`` share it.
Speech spans come from ``app.pipeline.vad``.

The rules are deliberately conservative (cloud ASR is often 1–2 s off, VAD can miss
quiet singing under music):
- a cue only uses the voice that OVERLAPS it — the onset/offset of the next or
  previous sentence's voice is never a candidate — and each edge moves only to the
  nearest such boundary within ``REACH``;
- a move never cuts more than ``MAX_CUT`` of the cue, and tiny moves are dropped;
- proposals are computed from the ORIGINAL times, then each shared boundary is
  resolved once: an overlap clamps the earlier cue's end — never pushes the later
  cue's start forward (that would hide its first words); if that is infeasible the
  boundary keeps its original edges;
- locked cues never move and act as fixed neighbours.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

REACH = 1.0       # s — an edge snaps only to a voice boundary this close
PAD_IN = 0.08     # s — start a little before the voice
PAD_OUT = 0.15    # s — keep the line up a little after the voice stops
MAX_CUT = 0.4     # a move may remove at most this fraction of the cue
MIN_SHIFT = 0.05  # s — smaller moves are noise, ignored
GAP = 0.05        # s — minimum gap kept between neighbouring cues
MIN_LEN = 0.4     # s — snapping never makes a cue shorter than this (or than it was)

Span = tuple[float, float]


@dataclass
class SnapReport:
    changed: list[int] = field(default_factory=list)
    locked: list[int] = field(default_factory=list)
    no_voice: list[int] = field(default_factory=list)


def _nearest(points: list[float], t: float, reach: float) -> float | None:
    """The value in sorted ``points`` closest to ``t`` within ``reach``."""
    i = bisect_left(points, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(points) and abs(points[j] - t) <= reach:
            if best is None or abs(points[j] - t) < abs(best - t):
                best = points[j]
    return best


def _propose(start: float, end: float, spans: list[Span],
             duration: float | None) -> tuple[float, float] | None:
    """Independent proposal for one cue (no neighbours yet); None = no voice inside it.
    Only spans overlapping the cue count: a neighbour's voice is not this cue's."""
    own = [(s, e) for s, e in spans if s < end and e > start]
    if not own:
        return None
    onsets = sorted(s for s, _e in own)
    offsets = sorted(e for _s, e in own)
    length = max(end - start, 0.0)
    max_cut = MAX_CUT * length
    ns, ne = start, end

    on = _nearest(onsets, start, REACH)
    if on is not None:
        cand = max(0.0, on - PAD_IN)
        if abs(cand - start) >= MIN_SHIFT and cand - start <= max_cut:   # later start = a cut
            ns = cand
    off = _nearest(offsets, end, REACH)
    if off is not None:
        cand = off + PAD_OUT
        if duration:
            cand = min(cand, duration)
        if abs(cand - end) >= MIN_SHIFT and end - cand <= max_cut:       # earlier end = a cut
            ne = cand
    floor = min(MIN_LEN, length) if length > 0 else MIN_LEN
    if ne - ns < floor:            # both edges together cut too much → keep the original
        return start, end
    return ns, ne


def plan(cues: list, spans: list[Span], *, duration: float | None = None,
         only: set[int] | None = None) -> tuple[list[tuple[float, float]], SnapReport]:
    """New (start, end) for every cue (unchanged where nothing applies) + a report.
    ``only``: indices allowed to move (others stay fixed, like locked ones)."""
    spans = sorted((float(s), float(e)) for s, e in spans if e > s)
    rep = SnapReport()
    orig = [(float(c.start), float(c.end)) for c in cues]
    new = list(orig)
    movable = []
    for i, c in enumerate(cues):
        mov = not getattr(c, "locked", False) and (only is None or i in only)
        if getattr(c, "locked", False) and (only is None or i in only):
            rep.locked.append(i)
        movable.append(mov)
        if not mov:
            continue
        p = _propose(orig[i][0], orig[i][1], spans, duration)
        if p is None:
            rep.no_voice.append(i)
        else:
            new[i] = p

    # resolve each shared boundary once, in time order
    order = sorted(range(len(cues)), key=lambda k: orig[k][0])
    for a, b in zip(order, order[1:]):
        (sa, ea), (sb, _eb) = new[a], new[b]
        if ea <= sb - GAP + 1e-9:
            continue
        floor_a = min(MIN_LEN, orig[a][1] - orig[a][0])
        if movable[a] and sb - GAP - sa >= floor_a and sb - GAP >= orig[a][0]:
            new[a] = (sa, sb - GAP)       # clamp the earlier end, never push b's start
        elif movable[a] or movable[b]:
            # infeasible → this boundary keeps its original edges
            if movable[a]:
                new[a] = (new[a][0], orig[a][1])
            if movable[b]:
                new[b] = (orig[b][0], new[b][1])

    for i, (o, n) in enumerate(zip(orig, new)):
        if movable[i] and (abs(n[0] - o[0]) >= 1e-6 or abs(n[1] - o[1]) >= 1e-6):
            rep.changed.append(i)
    return [(round(s, 3), round(e, 3)) for s, e in new], rep


def snap_cues(cues: list, spans: list[Span], *, duration: float | None = None,
              only: set[int] | None = None) -> SnapReport:
    """Apply :func:`plan` in place. Returns what changed / was locked / had no voice."""
    times, rep = plan(cues, spans, duration=duration, only=only)
    for i in rep.changed:
        cues[i].start, cues[i].end = times[i]
    return rep


def snap_srt(job, srt_path, *, log=None) -> SnapReport | None:
    """Pipeline step: snap an .srt in the job dir to the voice and rewrite it.
    None when there is no voice data (no model / offline / no audio) — the file is
    then left untouched. Shared by the desktop worker and the CLI."""
    from app.pipeline import vad
    from app.pipeline.assemble import probe_duration
    from app.pipeline.srt import parse_srt, write_srt

    spans = vad.voice_spans(job, log=log)      # + the word track where music hid the voice
    if not spans:
        return None
    cues = parse_srt(srt_path)
    src = job.source_video if job.source_video.exists() else job.source_audio
    duration = probe_duration(src) if src.exists() else None
    rep = snap_cues(cues, spans, duration=duration or None)
    if rep.changed:
        write_srt(cues, srt_path)
    if log:
        log(f"✓ Tự canh lời theo giọng nói: chỉnh {len(rep.changed)}/{len(cues)} câu"
            + (f" · {len(rep.no_voice)} câu không thấy giọng (giữ nguyên)" if rep.no_voice else ""))
    return rep
