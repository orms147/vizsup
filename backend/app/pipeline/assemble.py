"""Stage: assemble. FFmpeg builds the Vietnamese dub track on an absolute
timeline and burns styled subtitles into the final MP4.

Dub-sync policy (see docs/feature-proposal.md §3 — makes overlap structurally
impossible without cumulative drift):
- The fit budget for a segment is the span until the *next cue's* start, NOT the
  cue's own duration — this reclaims the silent gaps between cues as dub time.
- A segment never starts before the previous segment's audio ends (no overlap).
- If a segment still overruns: speed it up (rubberband, capped ~1.30x), then
  trim a short tail with a fade (small overruns), or push the next segment later
  — the push is bounded: once a line has slipped >= MAX_CARRY behind its cue,
  overruns are trimmed instead of pushed, and any gap the audio doesn't fill
  re-anchors the timeline (start = max(cue.start, prev_end)), so drift self-heals.
- Lines that even max-speed can't fit are reported so the user can shorten them.

Audio mixing (B3): replace original / mix at a set volume / sidechain-duck the
original under the dub. Dub and original volumes are adjustable.

ffmpeg runs with cwd = work_dir so the ``ass=`` filter gets a clean relative
filename (avoids Windows drive-colon escaping headaches).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin, ffprobe_bin
from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_ass


def _load_cue_styles(job: Job) -> dict[int, dict] | None:
    """Per-cue styles for write_ass — delegates to pipeline.sidecars (the single
    loader implementation; kept here for existing callers)."""
    from app.pipeline import sidecars

    return sidecars.load_cue_styles(job)

# Dub-fit constants (KrillinAI/VideoLingo consensus).
SPEED_ACCEPT = 1.15   # below this, speed-up is inaudible → no warning
SPEED_MAX = 1.30      # hard cap; beyond this speech turns "auctioneer"
MAX_CARRY = 1.5       # don't let the dub drift more than this behind the picture
TRIM_BUDGET = 0.4     # tail we'll fade-trim to avoid pushing the next line

_RUBBERBAND: bool | None = None
_RB_OPTS: str | None = None


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


def _rubberband_opts() -> str:
    """Speech-tuned rubberband options, kept only when this ffmpeg build's filter
    actually documents them (probed once via `-h filter=rubberband`)."""
    global _RB_OPTS
    if _RB_OPTS is None:
        try:
            out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-h", "filter=rubberband"],
                                 capture_output=True, text=True).stdout
        except Exception:  # noqa: BLE001
            out = ""
        opts = [f"{k}={v}" for k, v in
                (("transients", "crisp"), ("pitchq", "quality"), ("formant", "preserved"))
                if k in out]
        _RB_OPTS = (":" + ":".join(opts)) if opts else ""
    return _RB_OPTS


def _tempo_filter(tempo: float) -> str:
    """Pitch-preserving speed-up. rubberband sounds better on speech; fall back to
    atempo (fine for our ≤1.3x range — single stage, well within atempo's 0.5–2x)."""
    if _has_rubberband():
        return f"rubberband=tempo={tempo:.4f}:pitch=1{_rubberband_opts()}"
    return f"atempo={tempo:.4f}"


def probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return 0.0   # corrupt/zero-byte segment → the planner skips it (seg_dur<=0)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _has_audio_stream(src: Path) -> bool:
    """Whether the source has any audio track — mix/duck reference [0:a] and hard-
    fail on silent screen-recordings without this check. Probe failure assumes
    audio (the old behavior)."""
    try:
        out = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, check=True,
        )
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return True


def _mix_chunked(labels: list[str], parts: list[str], *, group: int = 32) -> str:
    """amix an arbitrary number of segment labels without one giant N-input amix
    (a 30-60 min video yields hundreds of segments; a single 500-input amix is
    slow and fragile): mix in chunks of ``group``, then mix the chunk outputs,
    repeating until one label remains. Returns that final label."""
    level = 0
    while len(labels) > 1:
        nxt: list[str] = []
        for ci in range(0, len(labels), group):
            chunk = labels[ci:ci + group]
            if len(chunk) == 1:
                nxt.append(chunk[0])
                continue
            lbl = f"[mx{level}_{ci // group}]"
            parts.append(f"{''.join(chunk)}amix=inputs={len(chunk)}"
                         f":normalize=0:dropout_transition=0{lbl}")
            nxt.append(lbl)
        labels = nxt
        level += 1
    return labels[0]


# The subtitle fonts ship with the app (desktop/assets/fonts, OFL) but are not installed
# in Windows, so libass fell back to Arial ("fontselect: (Be Vietnam Pro) -> ArialMT",
# found 2026-10-13). Copy them next to the job and point libass at them.
_BUNDLED_FONTS = Path(__file__).resolve().parents[3] / "desktop" / "assets" / "fonts"


def _ass_fonts(wd: Path) -> str:
    """``:fontsdir=fonts`` for the ass filter (cwd = job dir), or '' if none bundled."""
    fonts = sorted(_BUNDLED_FONTS.glob("*.ttf")) if _BUNDLED_FONTS.is_dir() else []
    if not fonts:
        return ""
    dst = wd / "fonts"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for f in fonts:
            target = dst / f.name
            if not target.exists() or target.stat().st_size != f.stat().st_size:
                shutil.copyfile(f, target)
    except OSError:
        return ""
    return ":fontsdir=fonts"


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
    cue_styles = {i + 1: c.style for i, c in enumerate(cues) if getattr(c, "style", None)} or None
    write_ass(cues, wd / "_preview.ass", font=font, size=size,
              cover_hardsubs=cover_hardsubs, style=style, cue_styles=cue_styles)
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
         "-frames:v", "1", "-vf", f"ass=_preview.ass{_ass_fonts(wd)}", "-q:v", "2",
         "_preview.png"],
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
                           "trim": trim > 0.01,
                           "trim_s": round(trim, 3),                # for fit_report.json
                           "carry_s": round(start - cue.start, 3)})  # how far this line slipped
        prev_end = end
    return placements, sped, flagged


def _load_region(job: Job) -> dict | None:
    """The detected hardsub band ({x,y,w,h} normalized) written by hardsub_detect,
    or None (old job / SRT source / detection failed)."""
    try:
        r = json.loads((job.work_dir / "hardsub_region.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return r if isinstance(r, dict) and all(k in r for k in ("x", "y", "w", "h")) else None


def _blur_band_filters(region: dict | None) -> tuple[str, str]:
    """(crop_expr, overlay_pos) for the blur cover. Uses the persisted normalized
    hardsub region when present (tighter → less of the frame blurred), else the
    bottom-third fallback. Pure."""
    if region and region.get("w", 0) > 0 and region.get("h", 0) > 0:
        x, y, w, h = region["x"], region["y"], region["w"], region["h"]
        return (f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f}",
                f"overlay=main_w*{x:.4f}:main_h*{y:.4f}")
    return "crop=iw:ih/3:0:ih*2/3", "overlay=0:main_h*2/3"


TIKTOK_W, TIKTOK_H = 1080, 1920


def _ass_name(preview: bool) -> str:
    return "preview.ass" if preview else "subs.ass"


def _build_render(
    job: Job,
    segments: list[tuple[Cue, Path]],
    *,
    audio_mode: str,
    orig_volume: float,
    dub_volume: float,
    cover_mode: str = "off",   # "off" | "box" (opaque box in the ASS style) | "blur"
    log: Callable[[str], None] | None = None,
    preview: bool = False,     # E-15: fast low-res preview.mp4 instead of output.mp4
    frame: str = "source",     # E-16: "source" | "tiktok" (1080x1920, fit + pad)
) -> list[str]:
    """Plan the timeline and build the ffmpeg command. The filter graph goes to
    ``<work_dir>/filter_complex.txt`` (+ ``-filter_complex_script``) instead of
    the argv — one giant ``-filter_complex`` argument used to hit the Windows
    32k CreateProcess ceiling at roughly 200-250 cues. Pure construction (plus
    ffprobe calls through probe_duration/_has_audio_stream) — no encode here,
    so tests can assert on the produced command/graph without running ffmpeg."""
    wd = job.work_dir

    # ENG-2: "keep music" needs a FRESH Demucs instrumental (bg.wav newer than the
    # source). Missing, or stale from a prior/re-downloaded source → fall back to mix
    # (so a failed/degraded separation can't silently mix a truncated/mismatched stem).
    if audio_mode == "keep_music":
        bg = wd / "bg.wav"
        src = job.source_video
        fresh = bg.exists() and (not src.exists() or bg.stat().st_mtime >= src.stat().st_mtime)
        if not fresh:
            if log:
                log("⚠ Chưa tách được nhạc nền (bg.wav) — chuyển sang chế độ trộn (mix).")
            audio_mode = "mix"

    cmd = [ffmpeg_bin(), "-y", "-i", "source.mp4"]
    for _, seg in segments:
        cmd += ["-i", str(Path(seg).relative_to(wd).as_posix())]
    if audio_mode == "keep_music":
        cmd += ["-i", "bg.wav"]   # last input → the instrumental background

    placements, sped, flagged = _plan_segments(segments)

    # Machine-readable fit report next to the output: what got sped up, trimmed,
    # or slipped — the metric N-6 (length-aware drafting) optimizes against.
    report = {
        "sped": sped,
        "flagged": [{"index": c.index, "start": round(c.start, 3),
                     "need_s": round(sd, 2), "have_s": round(sp, 2)}
                    for c, sd, sp in flagged],
        "total_trim_s": round(sum(p["trim_s"] for p in placements), 3),
        "max_carry_s": round(max((p["carry_s"] for p in placements), default=0.0), 3),
    }
    (wd / "fit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    from app.pipeline.tts import load_overrides
    ov = load_overrides(job)

    def _gain_db(cue: Cue) -> float:
        k = cue.index - 1
        return float(ov[k].get("gain_db", 0.0)) if 0 <= k < len(ov) else 0.0

    parts: list[str] = []
    labels: list[str] = []
    for p in placements:
        i = p["inp"]
        # resample every segment up-front (edge-tts mp3 is 24 kHz): letting amix
        # implicitly resample N inputs is slower and a quality risk
        af: list[str] = ["aresample=48000"]
        if p["tempo"] > 1.001:
            af.append(_tempo_filter(p["tempo"]))
        gain = _gain_db(segments[i - 1][0])
        if abs(gain) > 0.01:
            af.append(f"volume={gain:.1f}dB")
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

    if audio_mode in ("mix", "duck") and not _has_audio_stream(job.source_video):
        if log:
            log("⚠ Video nguồn không có track âm thanh — chuyển sang chế độ thay (replace).")
        audio_mode = "replace"

    if labels:
        dub = _mix_chunked(labels, parts)
        # consistent dub level across voices/clips; before mixing with the original
        parts.append(f"{dub}loudnorm=I=-16:TP=-1.5:LRA=11[dubn]")
        dub = "[dubn]"
        if abs(dub_volume - 1.0) > 1e-3:
            parts.append(f"{dub}volume={dub_volume:.3f}[dubvol]")
            dub = "[dubvol]"

        if audio_mode == "keep_music":
            # background = the Demucs instrumental (last input), dub on top; orig_volume
            # rides the instrumental. Speech is gone with the vocals stem, so no ducking.
            bg_idx = len(segments) + 1
            parts.append(f"[{bg_idx}:a]aresample=48000,volume={orig_volume:.3f}[bg]")
            parts.append(f"[bg]{dub}amix=inputs=2:normalize=0:dropout_transition=0[outa]")
            audio_map = "[outa]"
        elif audio_mode == "mix":
            parts.append(f"[0:a]volume={orig_volume:.3f}[bg]")
            parts.append(f"[bg]{dub}amix=inputs=2:normalize=0:dropout_transition=0[outa]")
            audio_map = "[outa]"
        elif audio_mode == "duck":
            # original ducks only while the dub plays (sidechain), then returns
            parts.append(f"{dub}asplit=2[scin][dubv]")
            parts.append(f"[0:a]volume={orig_volume:.3f}[bg]")
            parts.append("[bg][scin]sidechaincompress=threshold=0.05:ratio=8:"
                         "attack=20:release=300:makeup=1:level_sc=1[duck]")
            parts.append("[duck][dubv]amix=inputs=2:normalize=0[outa]")
            audio_map = "[outa]"
        else:  # replace
            audio_map = dub
    else:
        audio_map = "0:a?"

    ass = _ass_name(preview)
    vsrc = "[0:v]"
    if cover_mode == "blur":
        # blur the hardsub band (the detected region if we have one, else the
        # bottom-third) before burning the VI subs on top — softer than the opaque
        # box, still hides the original
        crop_expr, overlay_pos = _blur_band_filters(_load_region(job))
        parts.append("[0:v]split[vsrc][vband]")
        parts.append(f"[vband]{crop_expr},boxblur=10:2[vblur]")
        parts.append(f"[vsrc][vblur]{overlay_pos}[vbg]")
        vsrc = "[vbg]"
    if frame == "tiktok":
        # fit inside 1080x1920 BEFORE burning (sharper text, same layout as the source
        # render), then pad the bars — the cover/blur band stays on the picture
        parts.append(f"{vsrc}scale={TIKTOK_W}:{TIKTOK_H}:force_original_aspect_ratio=decrease"
                     f":force_divisible_by=2,setsar=1[vfit]")
        vsrc = "[vfit]"
    parts.append(f"{vsrc}ass={ass}{_ass_fonts(wd)}[v]")
    vmap = "[v]"
    if frame == "tiktok":
        parts.append(f"[v]pad={TIKTOK_W}:{TIKTOK_H}:(ow-iw)/2:(oh-ih)/2:color=black[vt]")
        vmap = "[vt]"
    if preview:                     # same graph, scaled down after the subs are burned
        parts.append(f"{vmap}scale=-2:640[vp]")
        vmap = "[vp]"
    (wd / "filter_complex.txt").write_text(";\n".join(parts) + "\n", encoding="utf-8")
    quality = ["-crf", "30", "-preset", "ultrafast"] if preview else ["-crf", "20", "-preset",
                                                                      "veryfast"]
    cmd += [
        "-filter_complex_script", "filter_complex.txt",
        "-map", vmap, "-map", audio_map,
        "-c:v", "libx264", *quality,
        "-c:a", "aac", "-b:a", "128k" if preview else "192k", "-ar", "48000",
        "preview.mp4" if preview else "output.mp4",
    ]
    return cmd


def assemble(
    job: Job,
    segments: list[tuple[Cue, Path]],
    *,
    burn_cues: list[Cue] | None = None,
    audio_mode: str = "replace",   # "replace" | "mix" | "duck"
    orig_volume: float = 1.0,      # original-audio gain (0..1+) for mix/duck
    dub_volume: float = 1.0,       # Vietnamese dub gain
    cover_hardsubs: bool = False,        # legacy bool → "box"
    cover_mode: str | None = None,       # "off" | "box" | "blur" (wins over the bool)
    font: str = "Be Vietnam Pro",
    size: int = 60,
    style: dict | None = None,
    log: Callable[[str], None] | None = None,
    preview: bool = False,
    frame: str = "source",               # "source" | "tiktok"
) -> Path:
    mode = cover_mode or ("box" if cover_hardsubs else "off")
    cues_for_subs = burn_cues if burn_cues is not None else parse_srt(job.vi_srt)
    # a quick preview writes its own .ass: subs.ass must keep matching output.mp4
    # (the soft-subtitle export pairs the two)
    write_ass(cues_for_subs, job.work_dir / _ass_name(preview), font=font, size=size,
              cover_hardsubs=(mode == "box"), style=style, cue_styles=_load_cue_styles(job))

    cmd = _build_render(job, segments, audio_mode=audio_mode, orig_volume=orig_volume,
                        dub_volume=dub_volume, cover_mode=mode, log=log, preview=preview,
                        frame=frame)
    subprocess.run(cmd, cwd=str(job.work_dir), check=True)
    return job.work_dir / "preview.mp4" if preview else job.output_video
