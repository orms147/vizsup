"""Stage: assemble. FFmpeg builds the Vietnamese dub track on an absolute
timeline and burns styled subtitles into the final MP4.

Dub-sync policy (see docs/feature-proposal.md §3 — makes overlap structurally
impossible without cumulative drift):
- The fit budget for a segment is the span until the *next cue's* start, NOT the
  cue's own duration — this reclaims the silent gaps between cues as dub time.
- A segment never starts before the previous segment's audio ends (no overlap).
- If a segment still overruns: speed it up (rubberband, capped ~1.30x), then
  trim a short tail with a fade, then push the next segment later but with a
  bounded carry that resets at any real gap (≥0.7s) so sync self-heals.
- Lines that even max-speed can't fit are reported so the user can shorten them.

Audio mixing (B3): replace original / mix at a set volume / sidechain-duck the
original under the dub. Dub and original volumes are adjustable.

ffmpeg runs with cwd = work_dir so the ``ass=`` filter gets a clean relative
filename (avoids Windows drive-colon escaping headaches).
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin, ffprobe_bin
from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_ass

# Dub-fit constants (KrillinAI/VideoLingo consensus).
SPEED_ACCEPT = 1.15   # below this, speed-up is inaudible → no warning
SPEED_MAX = 1.30      # hard cap; beyond this speech turns "auctioneer"
GAP_RESET = 0.7       # a gap this big resets accumulated timing carry
MAX_CARRY = 1.5       # don't let the dub drift more than this behind the picture
TRIM_BUDGET = 0.4     # tail we'll fade-trim to avoid pushing the next line

_RUBBERBAND: bool | None = None


def _has_rubberband() -> bool:
    global _RUBBERBAND
    if _RUBBERBAND is None:
        try:
            out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"],
                                 capture_output=True, text=True)
            _RUBBERBAND = "rubberband" in out.stdout
        except Exception:  # noqa: BLE001
            _RUBBERBAND = False
    return _RUBBERBAND


def _tempo_filter(tempo: float) -> str:
    """Pitch-preserving speed-up. rubberband sounds better on speech; fall back to
    atempo (fine for our ≤1.3x range — single stage, well within atempo's 0.5–2x)."""
    if _has_rubberband():
        return f"rubberband=tempo={tempo:.4f}:pitch=1"
    return f"atempo={tempo:.4f}"


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def style_preview_frame(
    job: Job,
    cues: list[Cue],
    *,
    at: float | None = None,
    font: str = "Be Vietnam Pro",
    size: int = 60,
    cover_hardsubs: bool = False,
    style: dict | None = None,
) -> Path:
    """Burn the styled subtitles onto a single source frame → a PNG, for a fast
    WYSIWYG style preview in the UI (no full render). Returns the PNG path."""
    wd = job.work_dir
    write_ass(cues, wd / "_preview.ass", font=font, size=size,
              cover_hardsubs=cover_hardsubs, style=style)
    if at is None:
        at = 1.0
        for c in cues:
            if (c.text or "").strip():
                at = c.start + min(0.4, max(0.0, (c.end - c.start) / 2))
                break
    out = wd / "_preview.png"
    # output-seek (-ss after -i) preserves source PTS so the ass filter shows the
    # cue actually active at `at` (input-seek would reset PTS → always cue #1).
    subprocess.run(
        [ffmpeg_bin(), "-y", "-i", "source.mp4", "-ss", f"{at:.3f}",
         "-frames:v", "1", "-vf", "ass=_preview.ass", "-q:v", "2", "_preview.png"],
        cwd=str(wd), check=True, capture_output=True,
    )
    return out


def _plan_segments(segments: list[tuple[Cue, Path]]):
    """Resolve each segment's placement on the timeline with the overlap-free
    policy. Returns (placements, sped_count, flagged) where each placement is a
    dict {inp, start, tempo, eff, trim}."""
    indexed = list(enumerate(segments, start=1))           # keep ffmpeg input index
    ordered = sorted(indexed, key=lambda x: x[1][0].start)  # process in time order
    placements: list[dict] = []
    flagged: list[tuple[Cue, float, float]] = []
    prev_end = 0.0
    sped = 0
    n = len(ordered)
    for j, (inp, (cue, seg)) in enumerate(ordered):
        seg_dur = probe_duration(Path(seg))
        if seg_dur <= 0:
            continue
        start = max(cue.start, prev_end)                   # never overlap previous audio
        next_ideal = ordered[j + 1][1][0].start if j + 1 < n else float("inf")
        span = next_ideal - start
        tempo = 1.0
        if span > 0 and seg_dur > span:
            tempo = min(seg_dur / span, SPEED_MAX)
        if tempo > SPEED_ACCEPT + 1e-3:
            sped += 1
        eff = seg_dur / tempo
        end = start + eff
        trim = 0.0
        if end > next_ideal:                               # still overruns the next line
            over = end - next_ideal
            carry = start - cue.start
            if over <= TRIM_BUDGET or carry >= MAX_CARRY:  # fade-trim to bound drift
                trim = min(over, max(0.0, eff - 0.25))
                eff -= trim
                end = start + eff
                if over > TRIM_BUDGET:
                    flagged.append((cue, seg_dur, max(span, 0.0)))
            else:                                          # push next later (carry grows)
                flagged.append((cue, seg_dur, max(span, 0.0)))
        placements.append({"inp": inp, "start": start, "tempo": tempo, "eff": eff,
                           "trim": trim > 0.01})
        prev_end = end
    return placements, sped, flagged


def assemble(
    job: Job,
    segments: list[tuple[Cue, Path]],
    *,
    burn_cues: list[Cue] | None = None,
    audio_mode: str = "replace",   # "replace" | "mix" | "duck"
    orig_volume: float = 1.0,      # original-audio gain (0..1+) for mix/duck
    dub_volume: float = 1.0,       # Vietnamese dub gain
    cover_hardsubs: bool = False,
    font: str = "Be Vietnam Pro",
    size: int = 60,
    style: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    ffmpeg = ffmpeg_bin()
    wd = job.work_dir

    cues_for_subs = burn_cues if burn_cues is not None else parse_srt(job.vi_srt)
    write_ass(cues_for_subs, job.subs_ass, font=font, size=size,
              cover_hardsubs=cover_hardsubs, style=style)

    cmd = [ffmpeg, "-y", "-i", "source.mp4"]
    for _, seg in segments:
        cmd += ["-i", str(Path(seg).relative_to(wd).as_posix())]

    placements, sped, flagged = _plan_segments(segments)

    parts: list[str] = []
    labels: list[str] = []
    for p in placements:
        i = p["inp"]
        af: list[str] = []
        if p["tempo"] > 1.001:
            af.append(_tempo_filter(p["tempo"]))
        if p["trim"]:
            eff = p["eff"]
            af.append(f"atrim=0:{eff:.3f}")
            af.append(f"afade=t=out:st={max(0.0, eff - 0.12):.3f}:d=0.12")
        delay = max(0, int(round(p["start"] * 1000)))
        af.append(f"adelay={delay}|{delay}")
        parts.append(f"[{i}:a]{','.join(af)}[a{i}]")
        labels.append(f"[a{i}]")

    if log:
        if sped:
            log(f"… {sped} câu tăng tốc đọc (≤{SPEED_MAX:g}×) để khớp khe thời gian")
        if flagged:
            log(f"⚠ {len(flagged)} câu QUÁ DÀI: kể cả tăng tốc {SPEED_MAX:g}× vẫn không vừa "
                f"→ đã cắt/dời nhẹ. Nên rút gọn các câu này ở bước sửa phụ đề:")
            for cue, sd, sp in flagged[:6]:
                log(f"   • [{cue.start:6.1f}s] cần ~{sd:.1f}s nhưng chỉ có {sp:.1f}s")

    if labels:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[dubraw]")
        dub = "[dubraw]"
        if abs(dub_volume - 1.0) > 1e-3:
            parts.append(f"[dubraw]volume={dub_volume:.3f}[dub]")
            dub = "[dub]"

        if audio_mode == "mix":
            parts.append(f"[0:a]volume={orig_volume:.3f}[bg]")
            parts.append(f"[bg]{dub}amix=inputs=2:normalize=0:dropout_transition=0[outa]")
            audio_map = "[outa]"
        elif audio_mode == "duck":
            # original ducks only while the dub plays (sidechain), then returns
            parts.append(f"{dub}asplit=2[scin][dubv]")
            parts.append(f"[0:a]volume={orig_volume:.3f}[bg]")
            parts.append("[bg][scin]sidechaincompress=threshold=0.05:ratio=8:"
                         "attack=20:release=300:makeup=1:level_sc=1[duck]")
            parts.append(f"[duck][dubv]amix=inputs=2:normalize=0[outa]")
            audio_map = "[outa]"
        else:  # replace
            audio_map = dub
    else:
        audio_map = "0:a?"

    parts.append("[0:v]ass=subs.ass[v]")
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "192k",
        "output.mp4",
    ]

    subprocess.run(cmd, cwd=str(wd), check=True)
    return job.output_video
