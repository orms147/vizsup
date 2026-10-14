"""Long videos: split one source into ~30-minute PARTS that run as separate projects,
then JOIN the rendered parts (and their subtitles) back into one video.

Why parts: a 3-hour video is ~4,000 subtitle lines — too many to review in one sitting,
and every later step waits for the slowest one. As parts, part 1 can be reviewed while
part 2 is still being transcribed.

Cutting (2026-10-14):
- A cut must not fall inside a sentence. Voice gaps come from local VAD (`vad.spans_for`,
  cached), or ffmpeg silencedetect when the VAD model is unavailable.
- The PICTURE is cut with stream copy (lossless, one read of the file) by ffmpeg's segment
  muxer, which splits exactly at the given keyframes: the parts' video packets add up to
  the source's, nothing duplicated or lost (a per-part ``-ss/-t`` copy overlapped
  ~0.2-0.5 s at every cut — B-frame reordering). A cut can only be a video KEYFRAME.
- The SOUND of each part is cut from the source sample-accurately over the same span and
  re-encoded once (AAC), starting with the part's first frame. Copying it through the
  segment muxer put part 1's picture 0.161 s behind its sound on a real Douyin video (the
  source's B-frame delay lived in an edit list the cut dropped). Containers disagree on
  where that first frame sits, so every part's sync is then MEASURED against the source
  (cross-correlating its loudest seconds) and corrected once when off by > 10 ms — on that
  video: part 1 −132 ms → 0, parts 2-3 0 ms as cut. So each cut is a keyframe that lies inside a voice gap, as close as possible
  to the planned boundary (±3 min). If no keyframe in reach lies in a gap, the keyframe
  nearest to one is used and the cut is reported as ``in_gap: false``.
- All times are relative to the file start (ffmpeg -ss adds the container start_time).

Joining: each part's output.mp4 lasts exactly as long as its source (assemble pins the
encode with -t), so concatenating them restores the original timeline; the subtitles of
part k are shifted by the summed durations of parts 1..k-1. Stream copy when every
output has the same codec parameters, else one re-encode through the concat filter.

Bookkeeping: the parent's folder keeps source.mp4 + ``group.json`` (no job.json, so it is
never listed as a project); each part is an ordinary job folder whose job.json carries
``opts["_group"] = {id, index, total, start, end, title}``.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin, ffprobe_bin
from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_srt

SPLIT_MIN = 45 * 60.0     # only videos longer than this are split
PART_TARGET = 30 * 60.0   # planned part length
SEARCH = 180.0            # a cut may move this far from its planned boundary
MIN_GAP = 0.35            # a voice gap must last this long to cut in
EDGE = 0.1                # …and the keyframe must sit this far inside it
GROUP_FILE = "group.json"

Span = tuple[float, float]


@dataclass
class Cut:
    t: float              # seconds from the file start (a keyframe)
    in_gap: bool          # False → fell back to a cut in speech/music


def should_split(duration: float, *, minimum: float | None = None) -> bool:
    return duration > (SPLIT_MIN if minimum is None else minimum)


def part_count(duration: float, target: float = PART_TARGET) -> int:
    return max(2, round(duration / target))


def voice_gaps(spans: list[Span], duration: float) -> list[Span]:
    """Silences between voice spans (and before the first / after the last). Pure."""
    gaps: list[Span] = []
    t = 0.0
    for a, b in sorted(spans):
        if a > t:
            gaps.append((t, a))
        t = max(t, b)
    if duration > t:
        gaps.append((t, duration))
    return gaps


def plan_cuts(duration: float, gaps: list[Span], keyframes_in: Callable[[float, float], list[float]],
              *, target: float = PART_TARGET) -> list[Cut]:
    """One cut per planned boundary (duration·i/n): the keyframe inside a voice gap
    nearest to the boundary, else the keyframe nearest to any gap. Pure apart from
    ``keyframes_in(lo, hi)``. Raises when a boundary has no keyframe in reach."""
    n = part_count(duration, target)
    usable = [(a + EDGE, b - EDGE) for a, b in gaps if b - a >= MIN_GAP]
    cuts: list[Cut] = []
    last = 0.0
    for i in range(1, n):
        aim = duration * i / n
        lo, hi = max(last + 60.0, aim - SEARCH), min(duration - 60.0, aim + SEARCH)
        kfs = [k for k in keyframes_in(lo, hi) if lo <= k <= hi]
        if not kfs:
            raise RuntimeError(f"Không tìm thấy keyframe quanh {aim / 60:.0f} phút để cắt video.")
        good = [k for k in kfs if any(a <= k <= b for a, b in usable)]
        if good:
            k = min(good, key=lambda x: abs(x - aim))
            cuts.append(Cut(round(k, 3), True))
        else:
            def miss(x: float) -> float:
                return min((max(a - x, 0.0, x - b) for a, b in gaps), default=0.0)
            k = min(kfs, key=lambda x: (round(miss(x), 1), abs(x - aim)))
            cuts.append(Cut(round(k, 3), False))
        last = cuts[-1].t
    return cuts


# --- ffmpeg / ffprobe ---------------------------------------------------------------

def start_time(src: Path) -> float:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=start_time",
                        "-of", "default=nw=1:nk=1", str(src)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def keyframes(src: Path, lo: float, hi: float, *, offset: float = 0.0) -> list[tuple[float, float]]:
    """Video keyframes in [lo, hi] as (pts, dts) seconds relative to the file start, from
    packet flags — no decoding, fast even on hours of video. The DTS matters: with
    B-frames it runs ~0.1 s ahead of the PTS, and the segment muxer compares cut times
    against it (asked to cut at a keyframe's PTS it skipped to the NEXT keyframe, 7 s
    later, on a real Douyin video)."""
    a, b = max(0.0, lo + offset), hi + offset
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-read_intervals", f"{a:.3f}%{b:.3f}",
                        "-show_entries", "packet=pts_time,dts_time,flags", "-of", "csv=p=0",
                        str(src)], capture_output=True, text=True)
    out: dict[float, float] = {}
    for line in r.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 3 and "K" in parts[2]:
            try:
                pts = round(float(parts[0]) - offset, 3)
                dts = float(parts[1]) - offset if parts[1] not in ("", "N/A") else pts
            except ValueError:
                continue
            out[pts] = round(dts, 3)
    return sorted(out.items())


_SILENCE = re.compile(r"silence_(start|end): (-?[\d.]+)")


def silence_gaps(src: Path) -> list[Span]:
    """Fallback voice gaps from ffmpeg silencedetect (no VAD model)."""
    r = subprocess.run([ffmpeg_bin(), "-v", "info", "-i", str(src), "-vn", "-af",
                        "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    gaps, start = [], None
    for kind, val in _SILENCE.findall(r.stderr):
        if kind == "start":
            start = max(0.0, float(val))
        elif start is not None:
            gaps.append((start, float(val)))
            start = None
    return gaps


def _video_duration(path: Path) -> float:
    """Length of the video stream (a part's container duration also counts the audio
    tail running past the last frame, ~0.1 s, which would shift every later start)."""
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return _probe_duration(path)


def _probe_duration(path: Path) -> float:
    from app.pipeline.assemble import probe_duration

    return probe_duration(path)


def _segment(src: Path, out_dir: Path, cuts: list[float], *, total: float,
             progress: Callable[[float], None] | None = None,
             cancel: Callable[[], bool] | None = None) -> list[Path]:
    """One stream-copy pass of the source PICTURE into ``out_dir/video_###.mp4``, split
    at ``cuts`` (the sound is added per part by :func:`_add_sound`)."""
    from app.pipeline.assemble import _run_ffmpeg

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.mp4"):
        old.unlink()
    cmd = [ffmpeg_bin(), "-y", "-i", str(src), "-map", "0:v:0", "-c", "copy",
           "-f", "segment", "-segment_times", ",".join(f"{c:.3f}" for c in cuts),
           "-reset_timestamps", "1", "-segment_format_options", "movflags=+faststart",
           "video_%03d.mp4"]
    _run_ffmpeg(cmd, out_dir, total=total, progress=progress, cancel=cancel)
    return sorted(out_dir.glob("video_*.mp4"))


def _first_frame(path: Path) -> float:
    """Presentation time of the first video frame (a cut part can start at ~0.16 s)."""
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-read_intervals", "%+#1", "-show_entries", "frame=pts_time",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.split()[0].strip(","))
    except (ValueError, IndexError):
        return 0.0


def _add_sound(src: Path, video: Path, dst: Path, start: float, end: float | None,
               offset: float | None = None) -> float:
    """``video`` + the source's sound over [start, end), sample-accurate, placed so its
    first sample plays with the first frame (or at ``offset``). Silent sources stay
    silent. Returns the offset used."""
    offset = _first_frame(video) if offset is None else offset
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-i", str(video),
           "-itsoffset", f"{offset:.6f}", "-ss", f"{start:.6f}"]
    if end is not None:
        cmd += ["-t", f"{end - start:.6f}"]
    cmd += ["-i", str(src), "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not dst.exists():
        tail = (r.stderr or "").strip().splitlines()[-1:] or ["ffmpeg lỗi"]
        raise RuntimeError(f"Ghép tiếng cho phần video lỗi: {tail[0]}")
    return offset


SYNC_RATE = 8000


def _pcm(path: Path, *, start: float = 0.0, dur: float | None = None):
    """Mono PCM on the file's presentation timeline (t=0 = ``start``; a sound that begins
    later is preceded by silence, so samples line up with the picture)."""
    import numpy as np

    cmd = [ffmpeg_bin(), "-v", "error", "-ss", f"{start:.4f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.4f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1",
            "-af", f"aresample={SYNC_RATE}:async=1:first_pts=0", "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw[: len(raw) // 2 * 2], np.int16).astype(np.float32)


def sync_error(src: Path, part: Path, start: float) -> float | None:
    """Seconds by which the part's sound runs EARLY against its picture, compared with
    the source at the same moment (negative = late). None when there is no sound loud
    enough in the part's first minute to tell."""
    import numpy as np

    mine = _pcm(part, dur=60.0)
    if len(mine) < SYNC_RATE * 4:
        return None
    rms = [float(np.sqrt(np.mean(mine[i:i + SYNC_RATE * 3] ** 2)))
           for i in range(0, len(mine) - SYNC_RATE * 3, SYNC_RATE // 2)]
    if not rms or max(rms) < 200.0:
        return None
    w = rms.index(max(rms)) * (SYNC_RATE // 2)
    probe = mine[w:w + SYNC_RATE * 3]
    expected = start + w / SYNC_RATE - _first_frame_norm(part)
    lo = max(0.0, expected - 1.0)
    ref = _pcm(src, start=lo, dur=5.0)
    if len(ref) < len(probe):
        return None
    corr = np.correlate(ref, probe, mode="valid")
    found = lo + int(np.argmax(corr)) / SYNC_RATE
    return round(found - expected, 4)


def _first_frame_norm(path: Path) -> float:
    """First frame on the timeline ffmpeg decodes with (pts − the file's start time)."""
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=start_time",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        st = float(r.stdout.strip() or 0)
    except ValueError:
        st = 0.0
    return _first_frame(path) - st


# --- split ----------------------------------------------------------------------------

def split_job(job: Job, storage: Path, *, title: str, target: float = PART_TARGET,
              log: Callable[[str], None] | None = None,
              progress: Callable[[float, str], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> dict:
    """Cut ``job.source_video`` into part folders under ``storage`` and write
    ``group.json`` in the job's folder. Returns the group dict. Does not touch job.json
    (the caller hides the parent from the project list)."""
    say = log or (lambda _m: None)
    tick = progress or (lambda _f, _m: None)
    src = job.source_video
    duration = _probe_duration(src)
    n = part_count(duration, target)
    say(f"Video dài {duration / 60:.0f} phút → chia {n} phần ~{duration / n / 60:.0f} phút "
        f"(cắt ở chỗ lặng, không cắt giữa câu)")
    tick(0.0, "Tìm chỗ lặng để cắt…")
    from app.pipeline import vad

    spans = vad.spans_for(src, job.work_dir, log=say)
    gaps = voice_gaps(spans, duration) if spans is not None else silence_gaps(src)
    offset = start_time(src)
    dts_of: dict[float, float] = {}

    def keyframes_in(lo: float, hi: float) -> list[float]:
        found = keyframes(src, lo, hi, offset=offset)
        dts_of.update(found)
        return [pts for pts, _dts in found]

    cuts = plan_cuts(duration, gaps, keyframes_in, target=target)
    for c in cuts:
        if not c.in_gap:
            say(f"⚠ Quanh {c.t / 60:.0f} phút không có chỗ lặng trùng keyframe — cắt ở "
                f"{c.t:.1f} s, có thể giữa một câu")
    tmp = job.work_dir / "_parts"
    # just before the keyframe's DTS: the segment muxer then cuts exactly at it
    at = [min(c.t, dts_of.get(c.t, c.t)) - 0.002 for c in cuts]
    files = _segment(src, tmp, at, total=duration, cancel=cancel,
                     progress=lambda f: tick(0.9 * f, f"Cắt video thành {n} phần · {int(f * 100)}%"))
    if len(files) != n:
        raise RuntimeError(f"Cắt video ra {len(files)} phần thay vì {n}.")
    vdurs = [_video_duration(f) for f in files]
    real = [round(sum(vdurs[:k]), 3) for k in range(1, n)]   # where the parts really start
    for c, r in zip(cuts, real, strict=True):
        if abs(c.t - r) > 0.3:
            say(f"⚠ Điểm cắt thật {r:.2f} s lệch dự tính {c.t:.2f} s")
            c.in_gap = any(a <= r <= b for a, b in gaps)
    gid = job.id
    parts = []
    starts = [0.0, *real]
    for k, start in enumerate(starts):
        if cancel and cancel():
            raise InterruptedError("Đã hủy.")
        end = starts[k + 1] if k + 1 < len(starts) else duration
        pid = uuid.uuid4().hex[:12]
        pdir = storage / pid
        pdir.mkdir(parents=True, exist_ok=True)
        tick(0.9 + 0.1 * k / n, f"Ghép tiếng phần {k + 1}/{n}…")
        stop = end if k + 1 < len(starts) else None
        used = _add_sound(src, files[k], pdir / "source.mp4", start, stop)
        err = sync_error(src, pdir / "source.mp4", start)
        if err is not None and abs(err) > 0.01:
            _add_sound(src, files[k], pdir / "source.mp4", start, stop, offset=used + err)
            after = sync_error(src, pdir / "source.mp4", start)
            say(f"… phần {k + 1}: chỉnh tiếng lệch hình {err * 1000:+.0f} ms"
                + (f" → còn {after * 1000:+.0f} ms" if after is not None else ""))
        dur = _probe_duration(pdir / "source.mp4")
        info = {"id": pid, "index": k + 1, "start": round(start, 3),
                "end": round(end, 3), "duration": round(dur, 3),
                "in_gap": True if k == 0 else cuts[k - 1].in_gap}
        meta = {"title": f"{title} · Phần {k + 1}/{n}", "duration": dur,
                "platform": job.metadata.get("platform"),
                "group": {"id": gid, "index": k + 1, "total": n, "start": info["start"]}}
        (pdir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        parts.append(info)
    group = {"id": gid, "title": title, "url": job.url, "duration": round(duration, 3),
             "target": target, "parts": parts, "created": int(time.time())}
    (job.work_dir / GROUP_FILE).write_text(json.dumps(group, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)       # ffmpeg left its render.log there
    tick(1.0, f"Đã chia {n} phần")
    return group


def load_group(group_dir: Path) -> dict | None:
    try:
        data = json.loads((group_dir / GROUP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("parts") else None


# --- join -----------------------------------------------------------------------------

def part_jobs(group: dict, storage: Path) -> list[Job]:
    jobs = []
    for p in sorted(group["parts"], key=lambda x: x["index"]):
        j = Job(id=p["id"], work_dir=storage / p["id"])
        jobs.append(j)
    return jobs


def readiness(group: dict, storage: Path) -> list[dict]:
    """Per part: approved (current revision), rendered (output.mp4 newer than the
    approval). Join needs every part rendered."""
    from app.pipeline import approval

    out = []
    for p, j in zip(sorted(group["parts"], key=lambda x: x["index"]), part_jobs(group, storage),
                    strict=True):
        ok = j.work_dir.is_dir() and approval.is_approved(j)
        stamp = j.work_dir / "approved.json"
        rendered = (ok and j.output_video.exists() and stamp.exists()
                    and j.output_video.stat().st_mtime >= stamp.stat().st_mtime)
        out.append({"id": p["id"], "index": p["index"], "approved": ok, "rendered": rendered,
                    "has_vi": j.vi_srt.exists()})
    return out


def _streams(path: Path) -> list[dict]:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,profile,width,height,pix_fmt,r_frame_rate,"
                        "sample_rate,channels", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("streams") or []
    except ValueError:
        return []


def same_params(paths: list[Path]) -> bool:
    """True when every file has identical stream parameters (concat copy is safe)."""
    first = _streams(paths[0]) if paths else []
    return bool(first) and all(_streams(p) == first for p in paths[1:])


def merge_subtitles(parts: list[tuple[list[Cue], float]]) -> list[Cue]:
    """Concatenate per-part cues, each shifted by its part's offset; renumbered. Pure."""
    out: list[Cue] = []
    for cues, offset in parts:
        for c in cues:
            out.append(Cue(len(out) + 1, round(c.start + offset, 3), round(c.end + offset, 3), c.text))
    return out


def join(group_dir: Path, storage: Path, *,
         progress: Callable[[float, str], None] | None = None,
         cancel: Callable[[], bool] | None = None,
         log: Callable[[str], None] | None = None) -> Path:
    """Join every part's output.mp4 into ``group_dir/output.mp4`` and their vi.srt into
    ``group_dir/vi.srt``. Raises with the part numbers when a part is not ready."""
    from app.pipeline import assemble

    say = log or (lambda _m: None)
    tick = progress or (lambda _f, _m: None)
    group = load_group(group_dir)
    if group is None:
        raise RuntimeError("Không đọc được thông tin nhóm (group.json).")
    ready = readiness(group, storage)
    missing = [r["index"] for r in ready if not r["rendered"]]
    if missing:
        raise RuntimeError("Chưa dựng xong (hoặc đã sửa sau khi dựng) phần: "
                           + ", ".join(str(i) for i in missing))
    jobs = part_jobs(group, storage)
    outs = [j.output_video for j in jobs]
    durations = [_probe_duration(o) for o in outs]
    offsets = [sum(durations[:k]) for k in range(len(outs))]
    merged = merge_subtitles([(parse_srt(j.vi_srt), off) for j, off in zip(jobs, offsets, strict=True)])
    write_srt(merged, group_dir / "vi.srt")

    lst = group_dir / "join.txt"
    lst.write_text("".join(f"file '{o.resolve().as_posix()}'\n" for o in outs), encoding="utf-8")
    total = sum(durations)
    if same_params(outs):
        say(f"Ghép {len(outs)} phần (không mã hóa lại)…")
        cmd = [ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", "join.txt",
               "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart",
               "output.mp4"]
    else:
        say(f"Các phần khác thông số mã hóa — ghép và mã hóa lại {len(outs)} phần…")
        first = next((s for s in _streams(outs[0]) if s.get("codec_type") == "video"), {})
        w, h = int(first.get("width") or 1280), int(first.get("height") or 720)
        cmd = [ffmpeg_bin(), "-y"]
        for o in outs:
            cmd += ["-i", o.resolve().as_posix()]
        graph = "".join(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];"
            f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}];" for i in range(len(outs)))
        graph += "".join(f"[v{i}][a{i}]" for i in range(len(outs))) + \
            f"concat=n={len(outs)}:v=1:a=1[v][a]"
        (group_dir / "join_graph.txt").write_text(graph, encoding="utf-8")
        cmd += ["-filter_complex_script", "join_graph.txt", "-map", "[v]", "-map", "[a]",
                *assemble._video_args(assemble._video_encoder(), False),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "output.mp4"]
    assemble._run_ffmpeg(cmd, group_dir, total=total, cancel=cancel,
                         progress=lambda f: tick(f, f"Ghép video · {int(f * 100)}%"))
    say(f"✓ Ghép xong: {len(outs)} phần · {total / 60:.0f} phút · {len(merged)} câu phụ đề")
    return group_dir / "output.mp4"


# --- one video, consistent parts ------------------------------------------------------
# Part k>1 reuses the context brief built for the first part (who speaks, xưng hô, names),
# merges the glossary suggestions of earlier parts (earlier wins) and starts from the
# subtitle style last set on a sibling. The global TM / glossary / style rules are shared
# by every job already (memory.py, translate.load_glossary).

def group_info(job: Job) -> dict | None:
    """``{id, index, total, start}`` when ``job`` is a part (from metadata.json)."""
    g = job.metadata.get("group") if isinstance(job.metadata, dict) else None
    if not g:
        try:
            g = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8")).get("group")
        except (OSError, ValueError, AttributeError):
            g = None
    return g if isinstance(g, dict) and g.get("id") else None


def share_brief(job: Job, storage: Path) -> bool:
    """Publish this part's brief to the group (only the first one wins)."""
    from app.pipeline.brief import BRIEF_FILE

    g = group_info(job)
    src, dst = job.work_dir / BRIEF_FILE, storage / str(g["id"]) / BRIEF_FILE if g else None
    if dst is None or not src.exists() or dst.exists() or not dst.parent.is_dir():
        return False
    shutil.copyfile(src, dst)
    return True


def inherit_brief(job: Job, storage: Path) -> str | None:
    """Give a part without a brief the group's; returns the brief text or None."""
    from app.pipeline import brief

    g = group_info(job)
    if not g or (job.work_dir / brief.BRIEF_FILE).exists():
        return None
    shared = storage / str(g["id"]) / brief.BRIEF_FILE
    try:
        data = json.loads(shared.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    text = (data.get("brief") or "").strip()
    if not text:
        return None
    brief._save(job, brief=text, note=data.get("note"), key=f"group:{g['id']}", source="group")
    return text


def share_terms(job: Job, storage: Path) -> int:
    """Merge the group's suggested glossary and this part's: earlier parts win, so a name
    keeps the rendering it got first. Writes both files; returns the merged size."""
    g = group_info(job)
    if not g:
        return 0
    name = "glossary.suggested.json"
    shared, own = storage / str(g["id"]) / name, job.work_dir / name

    def read(p: Path) -> dict:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    merged = {**read(own), **read(shared)}
    if not merged:
        return 0
    text = json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=1)
    own.write_text(text, encoding="utf-8")
    if shared.parent.is_dir():
        shared.write_text(text, encoding="utf-8")
    return len(merged)


def inherit_style(job: Job, storage: Path) -> bool:
    """A part opened without style.json starts from the newest sibling's."""
    g = group_info(job)
    if not g or (job.work_dir / "style.json").exists():
        return False
    group = load_group(storage / str(g["id"]))
    if not group:
        return False
    found = [storage / p["id"] / "style.json" for p in group["parts"] if p["id"] != job.id]
    found = [p for p in found if p.exists()]
    if not found:
        return False
    shutil.copyfile(max(found, key=lambda p: p.stat().st_mtime), job.work_dir / "style.json")
    return True
