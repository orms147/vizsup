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

Long videos (2026-10-13): the dub track is built in windows of ≤5 min / ≤100 lines,
cut in the silence before a line, each by its own small ffmpeg run into
``dub/win_###.flac`` (cached by content) — instead of one ffmpeg with an input per
line, which broke the Windows command line past ~1,600 lines and mixed in time
∝ lines × video length (adelay + amix: ~2 h for a 3-hour video). Inside a window the
lines are concatenated, each padded to the exact SAMPLE where the next one starts, so
placement stays absolute with no drift. The final encode reads the windows through
the concat demuxer, reports progress and can be cancelled; NVENC when available.

ffmpeg runs with cwd = work_dir so the ``ass=`` filter gets a clean relative
filename (avoids Windows drive-colon escaping headaches).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app import fsutil
from app.config import settings
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
LATE_S = 0.2          # F-10: a dub starting this much after its subtitle gets flagged
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


def probe_size(path: Path) -> tuple[int, int] | None:
    """Display size (w, h) of the first video stream — rotated phone clips swapped the
    way ffmpeg autorotates them — or None when it can't be read."""
    try:
        out = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True, encoding="utf-8", errors="replace",
        )
        st = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
        w, h = int(st["width"]), int(st["height"])
    except Exception:  # noqa: BLE001
        return None
    rot = st.get("tags", {}).get("rotate") or next(
        (d.get("rotation") for d in st.get("side_data_list") or [] if "rotation" in d), 0)
    try:
        if abs(int(float(rot))) % 180 == 90:
            w, h = h, w
    except (TypeError, ValueError):
        pass
    return (w, h) if w > 0 and h > 0 else None


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


# The subtitle fonts ship with the app (desktop/assets/fonts, OFL) but are not installed
# in Windows, so libass fell back to Arial ("fontselect: (Be Vietnam Pro) -> ArialMT",
# found 2026-10-13). Copy them next to the job and point libass at them.
_BUNDLED_FONTS = Path(__file__).resolve().parents[3] / "desktop" / "assets" / "fonts"


def _ass_fonts(wd: Path, family: str | None = None) -> str:
    """``:fontsdir=fonts`` for the ass filter (cwd = job dir), or '' if no font file.
    The folder gets the bundled fonts, the user's added fonts and — for a Windows font
    like Arial or Segoe UI — that ``family``'s files (app/fontfiles.py)."""
    from app import fontfiles

    files = fontfiles.files_for(family, settings.vizsup_storage_dir, _BUNDLED_FONTS)
    if not files:
        return ""
    try:
        if not fontfiles.copy_into(wd / "fonts", files):
            return ""
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
    burn = f"ass=_preview.ass{_ass_fonts(wd, font)}"
    video = ["-vf", burn]
    from app.pipeline import glass

    if glass.is_glass(style):           # the frosted glass under the line, as rendered
        src = probe_size(job.source_video) or (1920, 1080)
        W, H = 1080.0 * src[0] / src[1], 1080.0
        region, hardsubs, cn = glass.job_context(job)
        events = glass.plan(cues, W=W, H=H, font=font, size=size, style=style,
                            cue_styles=cue_styles, cn_cues=cn, region=region, hardsubs=hardsubs)
        bnd = glass.band(events)
        if bnd:
            glass.write_mask(events, wd / "_preview_glass.ass", bnd, W=W, H=H)
            parts = glass.filter_parts("[0:v]", bnd, "_preview_glass.ass", "[g]", height=src[1],
                                       **glass.look(style))
            video = ["-filter_complex", ";".join([*parts, f"[g]{burn}[v]"]), "-map", "[v]"]
    # output-seek (-ss after -i) preserves source PTS so the ass filter shows the
    # cue actually active at `at` (input-seek would reset PTS → always cue #1).
    subprocess.run(
        [ffmpeg_bin(), "-y", "-i", "source.mp4", "-ss", f"{at:.3f}",
         "-frames:v", "1", *video, "-q:v", "2", "_preview.png"],
        cwd=str(wd), check=True, capture_output=True,
    )
    return out


def _durations(paths: list[Path]) -> list[float]:
    """Segment durations, cached in ``durations.json`` next to the segments (name →
    size, mtime, seconds): a 3-hour video has ~4,000 of them and probing each on every
    render took minutes. Misses are probed in parallel."""
    if not paths:
        return []
    cache_file = paths[0].parent / "durations.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        cache = cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        cache = {}
    out: list[float] = [0.0] * len(paths)
    todo: list[tuple[int, Path, list | None]] = []
    for i, path in enumerate(paths):
        try:
            st = path.stat()
            sig: list | None = [st.st_size, st.st_mtime_ns]
        except OSError:
            sig = None
        hit = cache.get(path.name)
        if sig and isinstance(hit, list) and hit[:2] == sig:
            out[i] = float(hit[2])
        else:
            todo.append((i, path, sig))
    if todo:
        with ThreadPoolExecutor(max_workers=8) as ex:
            found = list(ex.map(lambda t: probe_duration(t[1]), todo))
        added = False
        for (i, path, sig), d in zip(todo, found):
            out[i] = d
            if sig and d > 0:
                cache[path.name] = [*sig, d]
                added = True
        if added:
            try:
                cache_file.write_text(json.dumps(cache), encoding="utf-8")
            except OSError:
                pass
    return out


def _plan_segments(segments: list[tuple[Cue, Path]]):
    """Resolve each segment's placement on the timeline with the overlap-free
    policy. Returns (placements, sped_count, flagged) where each placement is a
    dict {inp, start, tempo, eff, trim}."""
    indexed = list(enumerate(segments, start=1))           # keep the segment number
    ordered = sorted(indexed, key=lambda x: x[1][0].start)  # process in time order
    durations = _durations([Path(seg) for _i, (_c, seg) in ordered])
    placements: list[dict] = []
    flagged: list[tuple[Cue, float, float]] = []
    prev_end = 0.0
    sped = 0
    n = len(ordered)
    for j, (inp, (cue, seg)) in enumerate(ordered):
        seg_dur = durations[j]
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


# --- the dub track, window by window ---------------------------------------------------
RATE = 48000
# "Giảm nền tự động" (duck): the original stays a constant DUCK_BED_DB lower — music and
# effects audible everywhere, the Chinese voice never back at full level between lines —
# and the sidechain pushes it ~10 dB further down while the dub speaks. Measured
# 2026-10-27 on a 45 s cut of a Douyin review (voice + music + effects), stereo RMS: the
# old full-level bed was 4.6 dB LOUDER than the dub between lines and only 7 dB under it
# during them; now between lines it sits 3.4 dB under the dub level, and under a line the
# original (Chinese voice included, still faintly audible) is 19 dB below the dub.
DUCK_BED_DB = -8.0
DUCK_SC = "threshold=0.02:ratio=4:attack=15:release=400:makeup=1:level_sc=1"
WINDOW_SEC = 300.0     # a window closes at the first line starting this long after it opened…
WINDOW_MAX = 100       # …or after this many lines (≈ 2 KB of command line)
WINDOW_WORKERS = 4
DUB_DIR = "dub"


def _sample(t: float) -> int:
    return int(round(t * RATE))


def _windows(placements: list[dict]) -> list[dict]:
    """Group time-ordered placements into windows ``{lo, hi, items}``. A window ends
    where the next one's first line starts — placements never overlap
    (start[i+1] >= end[i]), so every cut falls in silence and no line straddles it.
    Pure."""
    wins: list[dict] = []
    cur: list[dict] = []
    lo = 0.0
    for p in placements:
        if cur and (len(cur) >= WINDOW_MAX or p["start"] - lo >= WINDOW_SEC):
            wins.append({"lo": lo, "hi": p["start"], "items": cur})
            lo, cur = p["start"], []
        cur.append(p)
    if cur:
        wins.append({"lo": lo, "hi": cur[-1]["start"] + cur[-1]["eff"], "items": cur})
    return wins


def _window_graph(win: dict) -> str:
    """Filter graph for one window: each line resampled to 48 kHz mono, sped up /
    gain / fade-trimmed as planned, then padded (or cut) to EXACTLY the samples until
    the next line starts, and all concatenated after a lead silence. The window lasts
    _sample(hi) - _sample(lo) samples, so windows chain without drift. Pure."""
    fmt = f"aformat=sample_fmts=fltp:sample_rates={RATE}:channel_layouts=mono"
    s0, s1 = _sample(win["lo"]), _sample(win["hi"])
    items = win["items"]
    parts: list[str] = []
    labels: list[str] = []
    lead = _sample(items[0]["start"]) - s0
    if lead > 0:
        parts.append(f"anullsrc=r={RATE}:cl=mono,atrim=end_sample={lead},{fmt}[lead]")
        labels.append("[lead]")
    for k, p in enumerate(items):
        nxt = _sample(items[k + 1]["start"]) if k + 1 < len(items) else s1
        n = max(1, nxt - _sample(p["start"]))
        af = [f"aresample={RATE}", fmt]
        if p["tempo"] > 1.001:
            af.append(_tempo_filter(p["tempo"]))
        if abs(p.get("gain", 0.0)) > 0.01:
            af.append(f"volume={p['gain']:.1f}dB")
        if p["trim"]:
            eff = p["eff"]
            af.append(f"atrim=0:{eff:.3f}")
            af.append(f"afade=t=out:st={max(0.0, eff - 0.12):.3f}:d=0.12")
        af += [f"apad=whole_len={n}", f"atrim=end_sample={n}", fmt]
        parts.append(f"[{k}:a]{','.join(af)}[s{k}]")
        labels.append(f"[s{k}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]")
    return ";\n".join(parts) + "\n"


def _window_key(win: dict, files: list[Path]) -> str:
    """Everything that shapes a window's audio — a re-render after editing one line
    rebuilds only the window(s) whose key changed."""
    rows = []
    for p, f in zip(win["items"], files):
        try:
            st = f.stat()
            sig = (st.st_size, st.st_mtime_ns)
        except OSError:
            sig = None
        rows.append([f.name, sig, _sample(p["start"]), round(p["tempo"], 4), p["trim"],
                     round(p["eff"], 3), round(p.get("gain", 0.0), 2)])
    raw = json.dumps([_sample(win["lo"]), _sample(win["hi"]), _tempo_filter(1.2), rows])
    return hashlib.sha1(raw.encode()).hexdigest()


def _render_dub(job: Job, segments: list[tuple[Cue, Path]], placements: list[dict], *,
                log: Callable[[str], None] | None = None,
                cancel: Callable[[], bool] | None = None,
                progress: Callable[[float], None] | None = None) -> str | None:
    """Render the dub windows (cached) and write the concat list; returns the list's
    path relative to the job dir, or None when there is nothing to dub."""
    if not placements:
        return None
    wd = job.work_dir
    d = wd / DUB_DIR
    d.mkdir(exist_ok=True)
    wins = _windows(placements)
    man_path = d / "manifest.json"
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        manifest = manifest if isinstance(manifest, dict) else {}
    except (OSError, ValueError):
        manifest = {}
    todo = []
    for i, w in enumerate(wins):
        files = [Path(segments[p["inp"] - 1][1]) for p in w["items"]]
        name = f"win_{i:03d}"
        key = _window_key(w, files)
        if not ((d / f"{name}.flac").exists() and manifest.get(name) == key):
            todo.append((name, key, w, files))
    if log and len(todo) < len(wins):
        log(f"↷ Giữ lại {len(wins) - len(todo)}/{len(wins)} đoạn lồng tiếng đã ghép trước đó")

    def run(item) -> tuple[str, str]:
        name, key, w, files = item
        if cancel and cancel():
            raise InterruptedError("Đã hủy.")
        (d / f"{name}.txt").write_text(_window_graph(w), encoding="utf-8")
        cmd = [ffmpeg_bin(), "-y", "-v", "error"]
        for f in files:
            cmd += ["-i", f.resolve().relative_to(wd.resolve()).as_posix()]
        cmd += ["-filter_complex_script", f"{DUB_DIR}/{name}.txt", "-map", "[out]",
                "-c:a", "flac", f"{DUB_DIR}/{name}.flac"]
        r = subprocess.run(cmd, cwd=str(wd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()[-1:] or ["ffmpeg lỗi"]
            raise RuntimeError(f"Ghép lồng tiếng đoạn {name}: {tail[0]}")
        return name, key

    if todo:
        with ThreadPoolExecutor(max_workers=WINDOW_WORKERS) as ex:
            futures = [ex.submit(run, t) for t in todo]
            try:
                for k, fut in enumerate(as_completed(futures), 1):
                    name, key = fut.result()
                    manifest[name] = key
                    man_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
                    if progress:
                        progress(k / len(todo))
            except BaseException:
                for fut in futures:
                    fut.cancel()
                raise
    for f in d.glob("win_*.flac"):                     # windows of a longer earlier cut
        if int(f.stem[4:] or 0) >= len(wins):
            f.unlink(missing_ok=True)
            manifest.pop(f.stem, None)
    man_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (d / "list.txt").write_text("".join(f"file 'win_{i:03d}.flac'\n" for i in range(len(wins))),
                                encoding="utf-8")
    return f"{DUB_DIR}/list.txt"


_NVENC: bool | None = None


def _video_encoder() -> str:
    """'h264_nvenc' when the setting allows it and a one-frame test encode works
    (listed encoders lie without an NVIDIA driver), else 'libx264'."""
    global _NVENC
    pref = (settings.vizsup_video_encoder or "auto").strip().lower()
    if pref == "cpu":
        return "libx264"
    if _NVENC is None:
        try:
            r = subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i",
                                "color=c=black:s=256x256:d=0.1", "-frames:v", "1",
                                "-c:v", "h264_nvenc", "-f", "null", "-"],
                               capture_output=True, timeout=30)
            _NVENC = r.returncode == 0
        except Exception:  # noqa: BLE001
            _NVENC = False
    return "h264_nvenc" if _NVENC else "libx264"


# Output quality ("Chất lượng" on the render page). Resolution = the SHORT side (720p is
# 1280×720 landscape, 720×1280 portrait / 9:16); never above the source. Presets map to
# libx264 CRF / h264_nvenc CQ (NVENC needs ~1–3 lower numbers for the same look) and
# the AAC bitrate. "sharp" = the values every render used before the choice existed;
# its 1080p NVENC output measured ~4.8 Mb/s on a talking-head video (1 GB / 28 min).
RESOLUTIONS = {"source": None, "1080": 1080, "720": 720, "480": 480}
PRESETS = {                      # crf, cq, audio, ~Mb/s at 1080p (size estimate only)
    "light": {"crf": 27, "cq": 30, "audio": "128k", "mbps": 1.5},
    "balanced": {"crf": 23, "cq": 25, "audio": "192k", "mbps": 2.8},
    "sharp": {"crf": 20, "cq": 21, "audio": "192k", "mbps": 4.8},
}
DEFAULT_PRESET = "sharp"


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def tiktok_canvas(res: str | None) -> tuple[int, int]:
    """The 9:16 canvas for a quality: 1080×1920 at source/1080, 720×1280, 480×854."""
    t = RESOLUTIONS.get(res or "source")
    if not t or t >= TIKTOK_W:
        return TIKTOK_W, TIKTOK_H
    return t, _even(t * TIKTOK_H / TIKTOK_W)


def scale_filter(res: str | None) -> str | None:
    """The downscale for a "source"-frame render: short side → the chosen lines, aspect
    kept, even sizes, and ``min`` so a smaller source is never upscaled. Pure; None =
    keep the source size."""
    t = RESOLUTIONS.get(res or "source")
    if not t:
        return None
    return (f"scale=w='if(gte(iw,ih),-2,min(iw,{t}))':h='if(gte(iw,ih),min(ih,{t}),-2)'"
            ":flags=lanczos")


def burn_size(src: tuple[int, int] | None, frame: str, res: str | None) -> tuple[int, int] | None:
    """Size of the picture the subtitles are burned on (before 9:16 bars) — mirrors
    :func:`scale_filter` and the tiktok fit. Pure; None when the source size is unknown."""
    if not src:
        return None
    w, h = src
    if frame == "tiktok":
        cw, ch = tiktok_canvas(res)
        s = min(cw / w, ch / h)        # ffmpeg's "decrease" + divisible_by=2 rounds down
        return min(cw, int(w * s) // 2 * 2), min(ch, int(h * s) // 2 * 2)
    t = RESOLUTIONS.get(res or "source")
    if not t or t >= min(w, h):
        return w, h
    long_side = _even(max(w, h) * t / min(w, h))
    std = _even(t * 16 / 9)
    if abs(long_side - std) <= max(2, std // 100):
        # a source a hair off 16:9 (1924×1080 → 1282×720) snaps to the standard size
        # (≤1 % stretch, invisible): every episode of a series then has the same size
        # and "Ghép tập" joins them by stream copy instead of re-encoding
        long_side = std
    return (long_side, t) if w >= h else (t, long_side)


def output_size(src: tuple[int, int] | None, frame: str, res: str | None) -> tuple[int, int] | None:
    """Final video size (the 9:16 canvas for tiktok). Pure."""
    if frame == "tiktok":
        return tiktok_canvas(res)
    return burn_size(src, frame, res)


def estimate_size_mb(src: tuple[int, int] | None, frame: str, res: str | None,
                     preset: str | None, duration: float) -> float | None:
    """Rough output size in MB: the preset's 1080p bitrate scaled by pixel count
    (^0.75 — fewer pixels compress worse per pixel) + the audio. Content changes this
    a lot; the UI shows it as an estimate. None when the size or length is unknown."""
    size = output_size(src, frame, res)
    if not size or duration <= 0:
        return None
    p = PRESETS.get(preset or DEFAULT_PRESET, PRESETS[DEFAULT_PRESET])
    if frame == "tiktok":       # the black bars cost ~nothing: count the picture only
        size = burn_size(src, frame, res) or size
    mbps = p["mbps"] * ((size[0] * size[1]) / (1920 * 1080)) ** 0.75
    audio = int(p["audio"].rstrip("k")) / 1000
    return (mbps + audio) * duration / 8


def _video_args(encoder: str, preview: bool, preset: str | None = None) -> list[str]:
    p = PRESETS.get(preset or DEFAULT_PRESET, PRESETS[DEFAULT_PRESET])
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p1" if preview else "p5", "-rc", "vbr",
                "-cq", "30" if preview else str(p["cq"]), "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-crf", "30" if preview else str(p["crf"]),
            "-preset", "ultrafast" if preview else "veryfast", "-pix_fmt", "yuv420p"]


def _audio_bitrate(preview: bool, preset: str | None = None) -> str:
    if preview:
        return "128k"
    return PRESETS.get(preset or DEFAULT_PRESET, PRESETS[DEFAULT_PRESET])["audio"]


def _run_ffmpeg(cmd: list[str], cwd: Path, *, total: float,
                progress: Callable[[float], None] | None = None,
                cancel: Callable[[], bool] | None = None) -> None:
    """Run the final encode with ``-progress`` on stdout: report the fraction done,
    and kill ffmpeg when ``cancel()`` turns true (a 3-hour encode must be stoppable).
    stderr goes to ``render.log`` in the job dir; its tail is the error message."""
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    log_path = cwd / "render.log"
    with log_path.open("w", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=err,
                                text=True, encoding="utf-8", errors="replace")
        best = 0.0                       # the last report can be an earlier stream's time
        try:
            for line in proc.stdout:
                if cancel and cancel():
                    proc.terminate()
                    raise InterruptedError("Đã hủy.")
                if line.startswith("out_time_us=") and progress and total > 0:
                    try:
                        frac = min(1.0, int(line.split("=", 1)[1]) / 1e6 / total)
                    except ValueError:
                        continue                   # "N/A" before the first frame
                    if frac > best:
                        best = frac
                        progress(frac)
            rc = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    if rc != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        raise RuntimeError("ffmpeg: " + " | ".join(tail or ["lỗi không rõ"]))


def _pause_graph(ins: list[tuple[float, float]], src_dur: float, video: str,
                 audios: list[str]) -> tuple[list[str], str, list[str]]:
    """F-4 "pause" notes: freeze the picture and silence the audio while the user's
    note is spoken. The source is cut at the pause points; each piece gets its pause
    appended (tpad clones the last frame, apad adds silence) — a pause at 0 is
    prepended to the first piece — and the pieces are concatenated. Returns (graph
    parts, new video label, new audio labels). Pure."""
    eps = 0.01
    intro = sum(d for at, d in ins if at <= eps)
    outro = sum(d for at, d in ins if src_dur > 0 and at >= src_dur - eps)
    inner: dict[float, float] = {}
    for at, d in ins:
        if at > eps and not (src_dur > 0 and at >= src_dur - eps):
            inner[round(at, 3)] = inner.get(round(at, 3), 0.0) + d
    cuts = sorted(inner)
    n = len(cuts) + 1
    parts: list[str] = []

    def piece(i: int, video_kind: bool) -> str:
        lo = cuts[i - 1] if i > 0 else None
        hi = cuts[i] if i < len(cuts) else None
        rng = ":".join(x for x in (f"start={lo:.3f}" if lo is not None else "",
                                   f"end={hi:.3f}" if hi is not None else "") if x)
        post = inner[hi] if hi is not None else outro
        pre = intro if i == 0 else 0.0
        if video_kind:
            f = [f"trim={rng}" if rng else "null", "setpts=PTS-STARTPTS"]
            # ONE tpad for both ends: a second chained tpad silently drops its stop
            # padding (measured: 13.1 s instead of 15 s, 2026-10-16)
            pads = [x for x in (f"start_mode=clone:start_duration={pre:.3f}" if pre > 0 else "",
                                f"stop_mode=clone:stop_duration={post:.3f}" if post > 0 else "")
                    if x]
            if pads:
                f.append("tpad=" + ":".join(pads))
        else:
            f = [f"atrim={rng}" if rng else "anull", "asetpts=PTS-STARTPTS"]
            if pre > 0:
                f.append(f"adelay={int(round(pre * 1000))}:all=1")
            if post > 0:
                f.append(f"apad=pad_dur={post:.3f}")
        return ",".join(f)

    def stage(label: str, kind: str, video_kind: bool) -> str:
        if n == 1:
            parts.append(f"{label}{piece(0, video_kind)}[{kind}fz]")
            return f"[{kind}fz]"
        split = "split" if video_kind else "asplit"
        parts.append(f"{label}{split}={n}" + "".join(f"[{kind}s{i}]" for i in range(n)))
        for i in range(n):
            parts.append(f"[{kind}s{i}]{piece(i, video_kind)}[{kind}p{i}]")
        tail = "v=1:a=0" if video_kind else "v=0:a=1"
        parts.append("".join(f"[{kind}p{i}]" for i in range(n)) + f"concat=n={n}:{tail}[{kind}fz]")
        return f"[{kind}fz]"

    v = stage(video, "v", True)
    a = [stage(lbl, f"a{k}", False) for k, lbl in enumerate(audios)]
    return parts, v, a


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
    cover_mode: str = "off",   # "off" | "box" (opaque ASS box) | "blur" | "glass" (glass.py)
    log: Callable[[str], None] | None = None,
    preview: bool = False,     # E-15: fast low-res preview.mp4 instead of output.mp4
    frame: str = "source",     # E-16: "source" | "tiktok" (1080x1920, fit + pad)
    plan: tuple | None = None,  # (placements, sped, flagged) when already planned
    dub: str | None = f"{DUB_DIR}/list.txt",   # concat list of the dub windows
    encoder: str | None = None,
    duration: float = 0.0,     # source length: the output is pinned to it (long-video join)
    inserts: list[tuple[float, float]] | None = None,   # F-4 pause notes (source time, length)
    music: list[dict] | None = None,   # background music clips placed by music.place()
    res: str = "source",       # output lines (RESOLUTIONS) — ignored by the 640p preview
    preset: str | None = None,  # PRESETS key → CRF / CQ / audio bitrate
    glass_band: tuple | None = None,   # cover "glass": the band (glass.band) to blur…
    glass_mask: str = "glass_mask.ass",  # …and the mask .ass drawn by glass.write_mask
    burn_h: int = 1080,        # height of the picture the subs are burned on (blur sigma)
    burn_w: int | None = None,  # its width: an exact downscale (burn_size snaps to 16:9)
    glass_look: dict | None = None,  # glass.look(style): white tint + blur strength
    fill: str = "blur",        # 9:16 bars: "blur" (the picture, blurred, behind) | "black"
    loudness: bool = True,     # the final mix at FINAL_LUFS — episodes join without jumps
) -> list[str]:
    """Plan the timeline and build the final ffmpeg command: the source, the dub
    track (concat list of the windows from :func:`_render_dub`) and, for "keep
    music", the instrumental. The filter graph goes to ``filter_complex.txt``.
    Pure construction (plus ffprobe through probe_duration/_has_audio_stream) — no
    encode here, so tests can assert on the produced command/graph."""
    wd = job.work_dir

    # ENG-2: "keep music" needs a FRESH separated instrumental (bg.wav newer than the
    # source). Missing, or stale from a prior/re-downloaded source → fall back to "duck"
    # (so a failed/degraded separation can't silently mix a truncated/mismatched stem) —
    # duck still keeps the original music + effects, only the Chinese voice goes down.
    if audio_mode == "keep_music":
        bg = wd / "bg.wav"
        src = job.source_video
        fresh = bg.exists() and (not src.exists() or bg.stat().st_mtime >= src.stat().st_mtime)
        if not fresh:
            if log:
                log("⚠ Chưa tách được nhạc nền (bg.wav) — dùng “Giữ nguyên âm gốc + lồng "
                    "tiếng”: nhạc, hiệu ứng gốc vẫn còn, giọng Trung nhỏ lại dưới giọng Việt.")
            audio_mode = "duck"

    placements, sped, flagged = plan if plan is not None else _plan_segments(segments)
    cmd = [ffmpeg_bin(), "-y", "-i", "source.mp4"]
    dubbed = bool(placements) and dub is not None
    if dubbed:
        cmd += ["-f", "concat", "-safe", "0", "-i", dub]          # input 1 → the dub track
    bg_idx = 2 if dubbed else 1
    if audio_mode == "keep_music" and dubbed:
        cmd += ["-i", "bg.wav"]                                   # the instrumental
    music = music or []
    mus_idx = bg_idx + (1 if audio_mode == "keep_music" and dubbed else 0)
    if music:
        from app.pipeline import music as music_mod

        cmd += music_mod.input_args(music)                        # the user's music clips

    # Machine-readable fit report next to the output: what got sped up, trimmed,
    # or slipped — the metric N-6 (length-aware drafting) optimizes against.
    report = {
        "sped": sped,
        "flagged": [{"index": c.index, "start": round(c.start, 3),
                     "need_s": round(sd, 2), "have_s": round(sp, 2)}
                    for c, sd, sp in flagged],
        "total_trim_s": round(sum(p["trim_s"] for p in placements), 3),
        "max_carry_s": round(max((p["carry_s"] for p in placements), default=0.0), 3),
        # F-10: lines whose dub starts noticeably after their subtitle (= the speech) —
        # an earlier line ran long and pushed it; ITU-R BT.1359 tolerates ~0.2 s
        "late": [{"index": c.index, "text": c.text[:60], "carry_s": p["carry_s"]}
                 for p in placements
                 for c in [segments[p["inp"] - 1][0]]
                 if p["carry_s"] > LATE_S and c.index < 100_000],    # not F-4 notes
    }
    (wd / "fit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    parts: list[str] = []
    src_v, src_a, bg_a = "[0:v]", "[0:a]", f"[{bg_idx}:a]"

    if log:
        if sped:
            log(f"… {sped} câu tăng tốc đọc (≤{SPEED_MAX:g}×) để khớp khe thời gian")
        if flagged:
            log(f"⚠ {len(flagged)} câu QUÁ DÀI: kể cả tăng tốc {SPEED_MAX:g}× vẫn không vừa "
                f"→ đã cắt/dời nhẹ. Nên rút gọn các câu này ở bước sửa phụ đề:")
            for cue, sd, sp in flagged[:6]:
                log(f"   • [{cue.start:6.1f}s] cần ~{sd:.1f}s nhưng chỉ có {sp:.1f}s")

    has_audio = _has_audio_stream(job.source_video)
    if audio_mode in ("mix", "duck") and not has_audio:
        if log:
            log("⚠ Video nguồn không có track âm thanh — chuyển sang chế độ thay (replace).")
        audio_mode = "replace"

    out_len = duration + sum(d for _at, d in inserts or [])
    if inserts:
        # F-4: freeze picture + original sound (and the instrumental) at the pause notes
        uses = []
        if has_audio and (audio_mode in ("mix", "duck") or not dubbed):
            uses.append(("orig", "[0:a]"))
        if audio_mode == "keep_music" and dubbed:
            uses.append(("bg", f"[{bg_idx}:a]"))
        pre, src_v, labels = _pause_graph(inserts, duration, "[0:v]", [lbl for _k, lbl in uses])
        parts += pre
        for (kind, _lbl), new in zip(uses, labels):
            if kind == "orig":
                src_a = new
            else:
                bg_a = new

    music_key = dubbed and any(p.get("duck") for p in music)
    if dubbed:
        # consistent dub level across voices/clips; before mixing with the original
        parts.append("[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[dubn]")
        dub = "[dubn]"
        if abs(dub_volume - 1.0) > 1e-3:
            parts.append(f"{dub}volume={dub_volume:.3f}[dubvol]")
            dub = "[dubvol]"
        if music_key:
            # the voice also keys the music's ducking; padded so the music plays on
            # after the last line (sidechaincompress stops with its key)
            parts.append(f"{dub}asplit=2[dubm][mkey]")
            parts.append("[mkey]apad[mkeyp]")
            dub = "[dubm]"

        if audio_mode == "keep_music":
            # background = the Demucs instrumental (last input), dub on top; orig_volume
            # rides the instrumental. Speech is gone with the vocals stem, so no ducking.
            parts.append(f"{bg_a}aresample=48000,volume={orig_volume:.3f}[bg]")
            parts.append(f"[bg]{dub}amix=inputs=2:normalize=0:dropout_transition=0[outa]")
            audio_map = "[outa]"
        elif audio_mode == "mix":
            parts.append(f"{src_a}volume={orig_volume:.3f}[bg]")
            parts.append(f"[bg]{dub}amix=inputs=2:normalize=0:dropout_transition=0[outa]")
            audio_map = "[outa]"
        elif audio_mode == "duck":
            # the original (music + effects + the Chinese voice) stays as a bed a little
            # lower all the time (DUCK_BED_DB), and goes down further while the dub
            # speaks (sidechain) — so between lines the Chinese voice can't jump back to
            # full volume over the music, and under a line it is well below the dub
            parts.append(f"{dub}asplit=2[scin][dubv]")
            bed = orig_volume * 10 ** (DUCK_BED_DB / 20)
            parts.append(f"{src_a}volume={bed:.3f}[bg]")
            # the key ends with the last dub line; padded with silence so the ducked
            # original keeps playing to the end (it used to stop there)
            parts.append("[scin]apad[scpad]")
            parts.append(f"[bg][scpad]sidechaincompress={DUCK_SC}[duck]")
            parts.append("[duck][dubv]amix=inputs=2:normalize=0[outa]")
            audio_map = "[outa]"
        else:  # replace
            audio_map = dub
        if duration > 0:
            # the dub ends with its last line; pad it so the audio lasts as long as the
            # picture (with -t below) — parts then join without drifting
            parts.append(f"{audio_map}apad[aout]")
            audio_map = "[aout]"
    else:
        audio_map = src_a if inserts and src_a != "[0:a]" else "0:a?"

    if music:
        # the user's background music on top of whatever the mode produced; ducked under
        # the Vietnamese voice when asked, then a limiter (three sources summed)
        mparts, mus = music_mod.graph(music, mus_idx, key="[mkeyp]" if music_key else None)
        parts += mparts
        base = audio_map if audio_map.startswith("[") else ("[0:a]" if has_audio else None)
        if base:
            parts.append(f"{base}{mus}amix=inputs=2:normalize=0:duration=first:"
                         f"dropout_transition=0,{music_mod.LIMIT}[amus]")
        else:
            parts.append(f"{mus}{'apad,' if duration > 0 else ''}{music_mod.LIMIT}[amus]")
        audio_map = "[amus]"
        if log:
            log(f"♪ Nhạc nền: {len(music)} đoạn")

    if loudness and (dubbed or has_audio):
        # every episode at the same loudness (true-peak limited): joined episodes no
        # longer jump in volume, and the platforms' own normalisation has less to do
        src = audio_map if audio_map.startswith("[") else "[0:a]"
        parts.append(f"{src}loudnorm=I={FINAL_LUFS}:TP=-1.5:LRA=11,aresample=48000[afin]")
        audio_map = "[afin]"

    ass = _ass_name(preview)
    vsrc = src_v
    if cover_mode == "blur":
        # blur the hardsub band (the detected region if we have one, else the
        # bottom-third) before burning the VI subs on top — softer than the opaque
        # box, still hides the original
        crop_expr, overlay_pos = _blur_band_filters(_load_region(job))
        parts.append(f"{src_v}split[vsrc][vband]")
        parts.append(f"[vband]{crop_expr},boxblur=10:2[vblur]")
        parts.append(f"[vsrc][vblur]{overlay_pos}[vbg]")
        vsrc = "[vbg]"
    if preview:
        res = "source"              # the quick check is always the 640p proof
    cw, ch = tiktok_canvas(res)
    if frame == "tiktok":
        # fit inside the 9:16 canvas BEFORE burning (sharper text, same layout as the
        # source render), then pad the bars — the cover/blur band stays on the picture
        if fill == "blur":
            # the bars become the same picture, zoomed to fill, blurred and a little
            # darker (cheap: blurred at quarter size, then scaled up)
            parts.append(f"{vsrc}split[vfi][vbgs]")
            qw, qh = max(2, cw // 4 // 2 * 2), max(2, ch // 4 // 2 * 2)
            parts.append(f"[vbgs]scale={qw}:{qh}:force_original_aspect_ratio=increase,"
                         f"crop={qw}:{qh},boxblur=8:2,eq=brightness=-0.08,"
                         f"scale={cw}:{ch},setsar=1[vbgfill]")
            vsrc = "[vfi]"
        parts.append(f"{vsrc}scale={cw}:{ch}:force_original_aspect_ratio=decrease"
                     f":force_divisible_by=2,setsar=1[vfit]")
        vsrc = "[vfit]"
    elif scale_filter(res):
        # downscale BEFORE the burn: libass draws the text at the final size (sharp),
        # and the .ass layout (PlayRes 1920x1080) scales with the frame
        # setsar=1: a snapped 1922→1280 width would otherwise carry SAR 961:960, and the
        # episode headers no longer match for a stream-copy join (2026-10-29, ep 9)
        exact = (f"scale={burn_w}:{burn_h}:flags=lanczos,setsar=1" if burn_w
                 else scale_filter(res))
        parts.append(f"{vsrc}{exact}[vscl]")
        vsrc = "[vscl]"
    if cover_mode == "glass" and glass_band:
        from app.pipeline import glass

        parts += glass.filter_parts(vsrc, glass_band, glass_mask, "[vglass]", height=burn_h,
                                    **(glass_look or {}))
        vsrc = "[vglass]"
    parts.append(f"{vsrc}ass={ass}{_ass_fonts(wd)}[v]")
    vmap = "[v]"
    if frame == "tiktok":
        if fill == "blur":
            parts.append("[vbgfill][v]overlay=(W-w)/2:(H-h)/2[vt]")
        else:
            parts.append(f"[v]pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color=black[vt]")
        vmap = "[vt]"
    if preview:                     # same graph, scaled down after the subs are burned
        parts.append(f"{vmap}scale=-2:640[vp]")
        vmap = "[vp]"
    (wd / "filter_complex.txt").write_text(";\n".join(parts) + "\n", encoding="utf-8")
    cmd += [
        "-filter_complex_script", "filter_complex.txt",
        "-map", vmap, "-map", audio_map,
        *_video_args(encoder or _video_encoder(), preview, preset),
        "-c:a", "aac", "-b:a", _audio_bitrate(preview, preset), "-ar", "48000",
        *(["-t", f"{out_len:.3f}"] if duration > 0 else []),
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
    cover_mode: str | None = None,       # "off" | "box" | "blur" | "glass" (wins over the bool)
    font: str = "Be Vietnam Pro",
    size: int = 60,
    style: dict | None = None,
    log: Callable[[str], None] | None = None,
    preview: bool = False,
    frame: str = "source",               # "source" | "tiktok"
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    overlays: list[dict] | None = None,  # F-1/F-2 corner notes (credit, "Lồng tiếng AI")
    watermark: dict | None = None,       # the translator's own mark (disclosure.WM_DEFAULT)
    fill: str = "blur",                  # 9:16 bars: blurred picture | "black"
    episode_card: dict | None = None,    # "Tập 9" chip + end card (disclosure.CARD_DEFAULT)
    commentary: list | None = None,      # F-4 commentary.Voiced — the user's own notes
    res: str = "source",                 # "source" | "1080" | "720" | "480" (short side)
    preset: str | None = None,           # "light" | "balanced" | "sharp" (default sharp)
) -> Path:
    global _NVENC
    from app.pipeline import commentary as cm
    from app.pipeline import glass

    mode = cover_mode or ("box" if cover_hardsubs else "off")
    # the glass look is one thing: the "Kính mờ trắng" style always renders the glass
    # cover, and the glass cover always gets text that reads on white
    if glass.is_glass(style):
        mode = "glass"
    if mode == "glass":
        style = glass.text_style(style)
    res = res if res in RESOLUTIONS else "source"
    preset = preset if preset in PRESETS else None
    cues_for_subs = burn_cues if burn_cues is not None else parse_srt(job.vi_srt)
    voiced = commentary or []
    ins = cm.inserts(voiced)
    # everything below lives on the OUTPUT timeline (pause notes push the rest later)
    cues_for_subs = cm.shift_cues(cues_for_subs, ins)
    note_subs = cm.subtitle_cues(voiced, ins)
    segments = [(c, p) for c, p in zip(cm.shift_cues([c for c, _p in segments], ins),
                                       [p for _c, p in segments])] + cm.speech_cues(voiced, ins)
    # a quick preview writes its own .ass: subs.ass must keep matching output.mp4
    # (the soft-subtitle export pairs the two)
    cue_styles = _load_cue_styles(job)
    from app.pipeline import compilation, disclosure

    out_dur = ((probe_duration(job.source_video) if job.source_video.exists() else 0.0)
               + sum(d for _a, d in ins))
    marks = disclosure.watermark_events(watermark, out_dur)
    tags = [*(overlays or []), *disclosure.episode_events(
        episode_card, compilation._job_title(job.work_dir) if episode_card else "", out_dur)]
    write_ass(cues_for_subs, job.work_dir / _ass_name(preview), font=font, size=size,
              cover_hardsubs=(mode == "box"), style=style, cue_styles=cue_styles,
              extra=tags or None, comments=note_subs, mark=marks, mark_look=watermark)
    _ass_fonts(job.work_dir, font)      # the chosen family's files (a Windows font too)
    src_size = probe_size(job.source_video) if job.source_video.exists() else None
    burn = burn_size(src_size, frame, "source" if preview else res) or (1920, 1080)
    glass_band = None
    mask_name = "preview_glass.ass" if preview else glass.MASK_FILE
    if mode == "glass":
        # geometry on a nominal 1080-line picture of the source's shape (it is stored
        # normalized, so it fits whatever size the picture is scaled to)
        W, H = 1080.0 * burn[0] / burn[1], 1080.0
        region, hardsubs, cn = glass.job_context(job)
        events = glass.plan([*cues_for_subs, *note_subs], W=W, H=H, font=font, size=size,
                            style=style, cue_styles=cue_styles, cn_cues=cm.shift_cues(cn, ins),
                            region=region, hardsubs=hardsubs)
        glass_band = glass.band(events)
        if glass_band:
            glass.write_mask(events, job.work_dir / mask_name, glass_band, W=W, H=H)

    say = progress or (lambda _f, _m: None)
    plan = _plan_segments(segments)
    dub = _render_dub(job, segments, _with_gains(job, segments, plan[0]), log=log,
                      cancel=cancel,
                      progress=lambda f: say(0.2 * f, f"Ghép lồng tiếng · {int(f * 100)}%"))
    total = probe_duration(job.source_video) if job.source_video.exists() else 0.0
    out_total = total + sum(d for _at, d in ins)
    # the user's background music, read fresh on every render (not part of the approved
    # revision) and placed on the output timeline like the subtitles
    from app.pipeline import music as music_mod

    placed = music_mod.place(music_mod.load(job), ins, out_total if total > 0 else 0.0,
                             duration=probe_duration, job=job)
    encoder = _video_encoder()
    while True:
        cmd = _build_render(job, segments, audio_mode=audio_mode, orig_volume=orig_volume,
                            dub_volume=dub_volume, cover_mode=mode, log=log, preview=preview,
                            frame=frame, plan=plan, dub=dub, encoder=encoder,
                            duration=total, inserts=ins, music=placed, res=res,
                            preset=preset, glass_band=glass_band, glass_mask=mask_name,
                            burn_h=burn[1],
                            # the exact size only for a real downscale: a source kept at its
                            # own (maybe odd, VP9) size keeps the even-rounding scale=-2
                            burn_w=burn[0] if src_size and burn != tuple(src_size) else None,
                            glass_look=glass.look(style), fill=fill)
        # encode into a temp name and swap it in when complete: an app killed or a
        # power cut mid-encode must not leave a broken output.mp4 that looks rendered
        final = job.work_dir / cmd[-1]
        part = final.with_name(f"{final.stem}{RENDERING_SUFFIX}")
        cmd[-1] = part.name
        try:
            _run_ffmpeg(cmd, job.work_dir, total=out_total, cancel=cancel,
                        progress=lambda f: say(0.2 + 0.8 * f, f"Đang dựng MP4 · {int(f * 100)}%"))
            if part.exists():                  # (always, unless ffmpeg is faked in a test)
                fsutil.replace(part, final)
            break
        except RuntimeError:
            part.unlink(missing_ok=True)
            if encoder == "libx264":
                raise
            _NVENC = False                 # don't try the GPU again this session
            encoder = "libx264"
            if log:
                log("⚠ Mã hóa bằng GPU (NVENC) lỗi — dựng lại bằng CPU.")
        except BaseException:
            part.unlink(missing_ok=True)   # Hủy / crash in Python: no half file left
            raise
    if preview:
        return job.work_dir / "preview.mp4"
    # how output.mp4 was made — the soft-subtitle export repeats the cover (export.py)
    note_secs = sum(v.dur for v in voiced)
    (job.work_dir / RENDER_INFO).write_text(
        json.dumps({"cover_mode": mode, "frame": frame, "audio_mode": audio_mode,
                    "encoder": encoder, "duration": round(total, 3), "music": len(placed),
                    "inserts": [[round(a, 3), round(d, 3)] for a, d in ins],
                    "commentary_s": round(note_secs, 2),
                    "commentary_share": round(cm.share(note_secs, total, out_total - total), 4),
                    "res": res, "preset": preset or DEFAULT_PRESET,
                    "size": list(output_size(src_size, frame, res) or ()),
                    "glass_band": list(glass_band) if glass_band else None,
                    # the glass look in style.json terms (export.py re-reads it via look())
                    "glass_tint": round(glass.look(style)["tint"] * 100),
                    "glass_blur": glass.look(style)["blur"]}),
        encoding="utf-8")
    # the subtitles exactly as burned (output timeline, the user's notes included) — what
    # "Xuất .srt" and the long-video join use, since vi.srt stays on the source timeline
    from app.pipeline.srt import write_srt

    write_srt(sorted([*cues_for_subs, *note_subs], key=lambda c: c.start),
              job.work_dir / OUTPUT_SRT)
    return job.output_video


def _with_gains(job: Job, segments: list[tuple[Cue, Path]], placements: list[dict]) -> list[dict]:
    """Per-line dub gain (sidecar overrides) onto each placement."""
    from app.pipeline.tts import load_overrides

    ov = load_overrides(job)
    for p in placements:
        k = segments[p["inp"] - 1][0].index - 1
        p["gain"] = float(ov[k].get("gain_db", 0.0)) if 0 <= k < len(ov) else 0.0
    return placements


FINAL_LUFS = -14          # final mix loudness (YouTube / TikTok normalise to about this)
RENDER_INFO = "render.json"
OUTPUT_SRT = "output.srt"
RENDERING_SUFFIX = ".rendering.mp4"   # output.rendering.mp4 → output.mp4 when complete


def load_render_info(job: Job) -> dict:
    try:
        d = json.loads((job.work_dir / RENDER_INFO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}
