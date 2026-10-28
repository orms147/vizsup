"""Ghép tập — join finished episodes (and earlier compilations) into one long video.

AI-animation series come as dozens of short episodes; viewers want them back to back.
Each episode is translated, reviewed and rendered as its own project first (it can be
posted alone), and a compilation only JOINS rendered videos — no translation, no TTS.
A compilation is itself a source for the next one: "Tập 1–50" + 51 … 100 → "Tập 1–100"
re-uses the 1–50 file as it is.

Sources (``source_from_dir``): a project folder (its output.mp4), a split long video
(the joined output, or its rendered parts back to back) or a compilation folder
(``compilation.json``). Each brings its subtitles (output.srt as rendered, else vi.srt)
and chapter marks; a compilation brings all of its chapters, so "Tập 1–100" still has
one chapter per episode.

Joining (``compile_videos``):
- Stream copy (lossless, minutes for hours of video) when every file has the same
  stream parameters INCLUDING the codec headers: x264's PPS depends on the CRF, so two
  renders at different "Chất lượng" presets differ although size/fps/profile match
  (measured 2026-10-28).
- Otherwise the files that differ from the majority (by duration) are re-encoded alone
  to its size / fps / audio format with the encoder + preset it was rendered with, and
  checked; if their headers still differ, every file is re-encoded the same way. Either
  way the final step is a concat-demuxer copy, one file at a time (a concat FILTER would
  open every input at once — 100 decoders).
- Offsets are written into the concat list (``duration``) so subtitles and chapters sit
  exactly where the demuxer puts each file. Chapters are embedded in the MP4 and written
  as YouTube-style timestamps (chapters.txt) for the post description.

Folder: storage/<id>/ with output.mp4, vi.srt, chapters.txt and compilation.json (no
job.json — never listed as an editable project)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.ffmpegutil import ffmpeg_bin, ffprobe_bin
from app.models import Cue, Job
from app.pipeline.srt import parse_srt, write_srt
from app.fsutil import atomic_write_text

COMP_FILE = "compilation.json"
CHAPTERS_TXT = "chapters.txt"
TMP_OUT = "output.joining.mp4"
NORM_DIR = "_norm"


@dataclass
class Clip:
    video: Path
    srt: Path | None = None
    encode: dict = field(default_factory=dict)     # how it was rendered: encoder, preset


@dataclass
class Source:
    """One row of a compilation: an episode, a split video or an earlier compilation."""
    id: str
    kind: str                                      # "job" | "group" | "compilation"
    title: str
    clips: list[Clip]
    chapters: list[tuple[float, str]]              # relative to this source's start
    episodes: list[dict]                           # flattened [{id, number, title}]
    state: str = "ok"                              # "ok" | "stale" | "missing"
    note: str = ""
    duration: float = 0.0                          # seconds, from metadata (display only)

    @property
    def number(self) -> int | None:
        nums = [e["number"] for e in self.episodes if e.get("number") is not None]
        return min(nums) if nums else None


# --- episode numbers / names (pure) -------------------------------------------------

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
             "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_NUM = r"([0-9]{1,4}|[零〇一二两三四五六七八九十百千]{1,8})"
_PATTERNS = (
    re.compile(r"第\s*" + _NUM + r"\s*[集话話期回]"),
    re.compile(r"(?i)(?<![a-z0-9])(?:ep|episode|tập|tap|part|p)\s*[.\-_#:]?\s*([0-9]{1,4})(?![0-9])"),
    re.compile(r"第\s*" + _NUM + r"\s*[章节節部]"),
    re.compile(r"^\s*([0-9]{1,4})(?:\s*[.、:：\-_)）\]]|\s|$)"),
    re.compile(r"[（(【\[]\s*([0-9]{1,4})\s*[)）】\]]"),
)


def _cn_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    total, cur = 0, 0
    for ch in text:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            total += (cur or 1) * _CN_UNIT[ch]
            cur = 0
        else:
            return None
    return total + cur or None


def episode_number(title: str | None) -> int | None:
    """The episode number in a title: 第173集 / 第十二话 / EP12 / Tập 5 / P3 / "12. …" /
    "(12)". None when there is none."""
    t = unicodedata.normalize("NFKC", title or "")
    for pat in _PATTERNS:
        m = pat.search(t)
        if m:
            n = _cn_number(m.group(1))
            if n is not None:
                return n
    return None


def clean_title(title: str, limit: int = 60) -> str:
    """A title without hashtags / @mentions and extra spaces, cut at ``limit``."""
    t = re.sub(r"[#＃@][^\s#＃@]+", " ", title or "")
    t = re.sub(r"\s+", " ", t).strip(" -|·") or (title or "").strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def chapter_name(title: str, number: int | None) -> str:
    return f"Tập {number}" if number is not None else clean_title(title)


def order(sources: list[Source]) -> list[Source]:
    """Numbered sources by episode number (a compilation by its first), the rest after
    them in the given order. Stable."""
    numbered = sorted((s for s in sources if s.number is not None), key=lambda s: s.number)
    return numbered + [s for s in sources if s.number is None]


def default_title(sources: list[Source]) -> str:
    nums = sorted({e["number"] for s in sources for e in s.episodes if e.get("number") is not None})
    if len(nums) >= 2:
        return f"Tổng hợp tập {nums[0]}–{nums[-1]}"
    count = sum(max(1, len(s.episodes)) for s in sources)
    return f"Video ghép {count} tập"


def warnings(sources: list[Source]) -> list[str]:
    """What the user should know before joining: unusable or stale sources, episodes
    that appear twice, numbers missing from the run. Pure."""
    out: list[str] = []
    for s in sources:
        if s.state != "ok":
            out.append(f"{clean_title(s.title, 40)}: {s.note}")
    seen: dict[str, str] = {}
    for s in sources:
        for e in s.episodes:
            key = e.get("id") or ""
            if key and key in seen and seen[key] != s.id:
                out.append(f"{chapter_name(e.get('title') or '', e.get('number'))} có hai lần "
                           f"(trong “{clean_title(s.title, 30)}” và một mục khác)")
            seen.setdefault(key, s.id)
    nums = [e["number"] for s in sources for e in s.episodes if e.get("number") is not None]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        out.append("Trùng số tập: " + ", ".join(str(n) for n in dup[:10]))
    if len(set(nums)) >= 2:
        lo, hi = min(nums), max(nums)
        missing = [n for n in range(lo, hi + 1) if n not in set(nums)]
        if missing:
            more = f" … ({len(missing)} tập)" if len(missing) > 10 else ""
            out.append("Thiếu tập " + ", ".join(str(n) for n in missing[:10]) + more)
    return out


def fmt_chapter_time(t: float) -> str:
    """YouTube description timestamps: 0:00 / 12:05 / 1:02:03."""
    s = int(round(max(0.0, t)))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def chapters_text(chapters: list[tuple[float, str]]) -> str:
    return "".join(f"{fmt_chapter_time(t)} {name}\n" for t, name in chapters)


def _meta_escape(text: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def ffmetadata(title: str, chapters: list[tuple[float, str]], total: float) -> str:
    """FFMETADATA1 with the title and one [CHAPTER] per mark (ms). Pure."""
    lines = [";FFMETADATA1", f"title={_meta_escape(title)}"]
    marks = sorted(chapters)
    for k, (t, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else total
        if end - t <= 0:
            continue
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(round(t * 1000))}",
                  f"END={int(round(end * 1000))}", f"title={_meta_escape(name)}"]
    return "\n".join(lines) + "\n"


# --- sources --------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _job_title(d: Path) -> str:
    meta, saved = _read_json(d / "metadata.json"), _read_json(d / "job.json")
    title = meta.get("title") or (saved.get("title") if saved.get("title") != d.name else None)
    if not title and (saved.get("opts") or {}).get("file"):
        title = Path(saved["opts"]["file"]).stem
    return str(title or d.name)


def _seconds(value) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _encode_of(d: Path) -> dict:
    info = _read_json(d / "render.json")
    return {k: info[k] for k in ("encoder", "preset") if info.get(k)}


def _rendered_srt(d: Path) -> Path | None:
    for name in ("output.srt", "vi.srt"):
        if (d / name).exists():
            return d / name
    return None


def _job_state(d: Path) -> tuple[str, str]:
    from app.pipeline import approval

    out = d / "output.mp4"
    if not out.exists():
        return "missing", "chưa dựng video"
    stamp = d / "approved.json"
    try:
        current = approval.is_approved(Job(id=d.name, work_dir=d)) and stamp.exists() \
            and out.stat().st_mtime >= stamp.stat().st_mtime
    except OSError:
        current = False
    return ("ok", "") if current else ("stale", "phụ đề đã sửa sau lần dựng — dùng bản dựng cũ")


def source_from_dir(storage: Path, item_id: str) -> Source | None:
    """The compilation source for a project / split video / compilation folder, or None
    when the folder is gone. Never creates folders."""
    from app.pipeline import split

    d = storage / item_id
    if not d.is_dir():
        return None
    if (d / COMP_FILE).exists():
        info = _read_json(d / COMP_FILE)
        title = info.get("title") or item_id
        chapters = [(float(c["t"]), str(c["name"])) for c in info.get("chapters") or []
                    if isinstance(c, dict) and "t" in c]
        eps = [e for e in info.get("episodes") or [] if isinstance(e, dict)]
        out = d / "output.mp4"
        ok = out.exists()
        return Source(item_id, "compilation", title,
                      [Clip(out, d / "vi.srt" if (d / "vi.srt").exists() else None,
                            dict(info.get("encode") or {}))] if ok else [],
                      chapters or [(0.0, clean_title(title))], eps,
                      "ok" if ok else "missing", "" if ok else "không còn file video ghép",
                      _seconds(info.get("duration")))
    group = split.load_group(d)
    if group is not None:
        title = str(group.get("title") or item_id)
        num = episode_number(title)
        eps = [{"id": item_id, "number": num, "title": title}]
        chapters = [(0.0, chapter_name(title, num))]
        dur = _seconds(group.get("duration"))
        if split.joined_is_current(d, storage):
            return Source(item_id, "group", title,
                          [Clip(d / "output.mp4", d / "vi.srt" if (d / "vi.srt").exists() else None,
                                _encode_of(storage / group["parts"][0]["id"]))], chapters, eps,
                          duration=dur)
        parts = sorted(group["parts"], key=lambda p: p["index"])
        dirs = [storage / p["id"] for p in parts]
        states = [_job_state(pd)[0] if pd.is_dir() else "missing" for pd in dirs]
        if all(s != "missing" for s in states):
            stale = any(s == "stale" for s in states)
            return Source(item_id, "group", title,
                          [Clip(pd / "output.mp4", _rendered_srt(pd), _encode_of(pd)) for pd in dirs],
                          chapters, eps, "stale" if stale else "ok",
                          "có phần đã sửa sau lần dựng — dùng bản dựng cũ" if stale else "", dur)
        left = sum(s == "missing" for s in states)
        return Source(item_id, "group", title, [], chapters, eps, "missing",
                      f"còn {left}/{len(parts)} phần chưa dựng", dur)
    title = _job_title(d)
    num = episode_number(title)
    state, note = _job_state(d)
    clips = [Clip(d / "output.mp4", _rendered_srt(d), _encode_of(d))] if state != "missing" else []
    dur = _seconds(_read_json(d / "render.json").get("duration")) or \
        _seconds(_read_json(d / "metadata.json").get("duration"))
    return Source(item_id, "job", title, clips, [(0.0, chapter_name(title, num))],
                  [{"id": item_id, "number": num, "title": title}], state, note, dur)


# --- probing / normalising ------------------------------------------------------------

_V_KEYS = ("codec_name", "profile", "level", "width", "height", "pix_fmt", "r_frame_rate",
           "sample_aspect_ratio", "color_range", "color_space", "color_transfer",
           "color_primaries", "extradata_hash")
_A_KEYS = ("codec_name", "profile", "sample_rate", "channels", "extradata_hash")


def probe(path: Path) -> dict:
    """{"v": {...}, "a": {...} | None, "duration": s} — the stream parameters that must
    match for a stream-copy concat, codec headers (extradata) included."""
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_data_hash", "MD5",
                        "-show_entries", "format=duration:stream=codec_type," + ",".join(
                            sorted(set(_V_KEYS) | set(_A_KEYS))),
                        "-of", "json", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        dur = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"v": {k: v.get(k) for k in _V_KEYS} if v else None,
            "a": {k: a.get(k) for k in _A_KEYS} if a else None, "duration": dur}


def signature(info: dict) -> str:
    return json.dumps([info.get("v"), info.get("a")], sort_keys=True)


def pick_target(infos: list[dict]) -> int:
    """Index of a file with the signature that covers the most playing time (ties: the
    earliest) — those files are copied, the others converted to match. Pure."""
    weight: dict[str, float] = {}
    for info in infos:
        weight[signature(info)] = weight.get(signature(info), 0.0) + max(info["duration"], 0.001)
    best = max(weight.values())
    return next(k for k, info in enumerate(infos) if weight[signature(info)] == best)


def normalize_cmd(src: Path, dst: Path, target: dict, encode: dict, *, src_has_audio: bool,
                  canonical: bool = False) -> list[str]:
    """ffmpeg command that re-encodes ``src`` to the target's picture size, frame rate,
    pixel format, colour tags and audio format (letterboxed, never stretched). Pure."""
    from app.pipeline import assemble

    tv, ta = target["v"] or {}, target.get("a")
    w, h = int(tv.get("width") or 1280), int(tv.get("height") or 720)
    fps = tv.get("r_frame_rate") or "30/1"
    pix = tv.get("pix_fmt") or "yuv420p"
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format={pix}")
    cmd = [ffmpeg_bin(), "-y", "-i", str(src)]
    if ta and not src_has_audio:
        cmd += ["-f", "lavfi", "-i",
                f"anullsrc=r={ta.get('sample_rate') or 48000}:cl="
                f"{'mono' if int(ta.get('channels') or 2) == 1 else 'stereo'}"]
    cmd += ["-map", "0:v:0"]
    if ta:
        cmd += ["-map", "0:a:0" if src_has_audio else "1:a:0"]
    cmd += ["-vf", vf]
    encoder = encode.get("encoder") or "libx264"
    cmd += assemble._video_args(encoder, False, encode.get("preset"))
    tags = {"color_range": "-color_range", "color_space": "-colorspace",
            "color_transfer": "-color_trc", "color_primaries": "-color_primaries"}
    for key, flag in tags.items():
        val = "tv" if canonical and key == "color_range" else "bt709" if canonical else tv.get(key)
        if val and val != "unknown":
            cmd += [flag, str(val)]
    if ta:
        cmd += ["-c:a", "aac", "-b:a", assemble._audio_bitrate(False, encode.get("preset")),
                "-ar", str(ta.get("sample_rate") or 48000), "-ac", str(ta.get("channels") or 2)]
        if not src_has_audio:
            cmd += ["-shortest"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", str(dst)]
    return cmd


def _usable_encode(encode: dict) -> dict:
    """The encoder a file was rendered with, if this machine can still use it."""
    from app.pipeline import assemble

    enc = dict(encode or {})
    if enc.get("encoder") == "h264_nvenc" and assemble._video_encoder() != "h264_nvenc":
        enc["encoder"] = "libx264"
    enc.setdefault("encoder", assemble._video_encoder())
    return enc


# --- subtitles / chapters (pure) ------------------------------------------------------

def place(sources: list[Source], durations: list[float]
          ) -> tuple[list[tuple[float, Clip]], list[tuple[float, str]], list[float]]:
    """Every clip's start on the joined timeline, every chapter mark moved there and
    each source's start. ``durations`` = one per clip, in order. Pure."""
    placed: list[tuple[float, Clip]] = []
    chapters: list[tuple[float, str]] = []
    starts: list[float] = []
    t, k = 0.0, 0
    for s in sources:
        starts.append(t)
        length = sum(durations[k:k + len(s.clips)])
        for name_t, name in s.chapters:
            if name_t < max(length, 0.001):
                chapters.append((round(t + name_t, 3), name))
        for c in s.clips:
            placed.append((t, c))
            t += durations[k]
            k += 1
    return placed, chapters, starts


def merge_cues(parts: list[tuple[list[Cue], float, float]]) -> list[Cue]:
    """(cues, offset, clip length): each clip's cues clipped to its length, shifted by
    its offset, renumbered. Pure."""
    from app.pipeline.split import slice_subtitles

    out: list[Cue] = []
    for cues, offset, length in parts:
        for c in slice_subtitles(cues, 0.0, length if length > 0 else None):
            out.append(Cue(len(out) + 1, round(c.start + offset, 3), round(c.end + offset, 3),
                           c.text))
    return out


# --- join -----------------------------------------------------------------------------

def _clip_key(clip: Clip, target_sig: str, encode: dict, canonical: bool) -> str:
    st = clip.video.stat()
    raw = f"{clip.video.resolve()}|{st.st_mtime_ns}|{st.st_size}|{target_sig}|" \
          f"{json.dumps(encode, sort_keys=True)}|{canonical}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def compile_videos(out_dir: Path, sources: list[Source], *, title: str,
                   progress: Callable[[float, str], None] | None = None,
                   cancel: Callable[[], bool] | None = None,
                   log: Callable[[str], None] | None = None) -> Path:
    """Join the sources (in this order) into ``out_dir/output.mp4`` with merged
    subtitles, chapters and compilation.json. Sources marked "missing" are refused."""
    from app.pipeline import assemble

    say = log or (lambda _m: None)
    tick = progress or (lambda _f, _m: None)
    bad = [clean_title(s.title, 40) for s in sources if s.state == "missing" or not s.clips]
    if bad:
        raise RuntimeError("Chưa ghép được — chưa có video dựng xong: " + ", ".join(bad[:5]))
    if not sources:
        raise RuntimeError("Chưa chọn video nào để ghép.")
    clips = [c for s in sources for c in s.clips]
    gone = [c.video.name for c in clips if not c.video.exists()]
    if gone:
        raise RuntimeError("Không thấy file video: " + ", ".join(gone[:3]))
    out_dir.mkdir(parents=True, exist_ok=True)

    def stopped() -> bool:
        return bool(cancel and cancel())

    tick(0.0, "Kiểm tra thông số các video…")
    infos = [probe(c.video) for c in clips]
    total_in = sum(i["duration"] for i in infos) or 1.0
    files = [c.video for c in clips]
    reencoded = 0
    target_i = pick_target(infos)
    target = infos[target_i]
    tsig = signature(target)
    encode = clips[target_i].encode
    if any(signature(i) != tsig for i in infos):
        norm = out_dir / NORM_DIR
        norm.mkdir(exist_ok=True)

        def convert(idxs: list[int], tgt: dict, enc: dict, canonical: bool, base: float,
                    span: float) -> list[Path]:
            outs = []
            done = 0.0
            weight = sum(infos[k]["duration"] for k in idxs) or 1.0
            for n, k in enumerate(idxs, 1):
                if stopped():
                    raise InterruptedError("Đã hủy.")
                dst = norm / f"{_clip_key(clips[k], signature(tgt), enc, canonical)}.mp4"
                if not dst.exists():
                    tmp = dst.with_suffix(".part.mp4")
                    cmd = normalize_cmd(clips[k].video, tmp, tgt, enc,
                                        src_has_audio=infos[k]["a"] is not None,
                                        canonical=canonical)
                    first = done

                    def step(f: float, first=first, n=n) -> None:
                        frac = (first + f * infos[k]["duration"]) / weight
                        tick(base + span * frac,
                             f"Chuyển định dạng video {n}/{len(idxs)} · {int(frac * 100)}%")
                    try:
                        assemble._run_ffmpeg(cmd, norm, total=infos[k]["duration"],
                                             progress=step, cancel=cancel)
                    except BaseException:
                        tmp.unlink(missing_ok=True)
                        raise
                    tmp.replace(dst)
                done += infos[k]["duration"]
                outs.append(dst)
            return outs

        odd = [k for k, i in enumerate(infos) if signature(i) != tsig]
        enc = _usable_encode(encode)
        say(f"{len(odd)}/{len(clips)} video khác thông số (kích thước, khung hình, chất lượng "
            f"dựng) — chuyển cho khớp rồi ghép…")
        share = sum(infos[k]["duration"] for k in odd) / total_in
        converted = convert(odd, target, enc, False, 0.02, 0.83 * share)
        if all(signature(probe(p)) == tsig for p in converted):
            for k, p in zip(odd, converted, strict=True):
                files[k] = p
            reencoded = len(odd)
        else:
            say("Thông số sau khi chuyển vẫn lệch — chuyển mọi video về cùng một định dạng…")
            canon = dict(target)
            allk = list(range(len(clips)))
            converted = convert(allk, canon, enc, True, 0.02 + 0.83 * share, 0.83 * (1 - share))
            sigs = {signature(probe(p)) for p in converted}
            if len(sigs) > 1:
                say("⚠ Các video vẫn khác nhau sau khi chuyển — vẫn ghép, kiểm tra lại bản ghép.")
            files = converted
            reencoded = len(allk)
        encode = enc

    if stopped():
        raise InterruptedError("Đã hủy.")
    durations = [assemble.probe_duration(f) for f in files]
    placed, chapters, starts = place(sources, durations)
    total = sum(durations)
    merged = merge_cues([(parse_srt(c.srt) if c.srt and c.srt.exists() else [], off, d)
                         for (off, c), d in zip(placed, durations, strict=True)])
    lst = out_dir / "join.txt"
    atomic_write_text(lst, "".join("file '" + f.resolve().as_posix().replace("'", "'\\''") + "'\n"
                           f"duration {d:.6f}\n" for f, d in zip(files, durations, strict=True)),
                   encoding="utf-8")
    atomic_write_text((out_dir / "chapters.ffmeta"), ffmetadata(title, chapters, total), encoding="utf-8")
    cmd = [ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", "join.txt",
           "-i", "chapters.ffmeta", "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "1",
           "-map_chapters", "1", "-c", "copy", "-movflags", "+faststart", TMP_OUT]
    say(f"Ghép {len(files)} video ({total / 60:.0f} phút, không mã hóa lại)…")
    base = 0.85 if reencoded else 0.02
    tmp = out_dir / TMP_OUT
    try:
        assemble._run_ffmpeg(cmd, out_dir, total=total, cancel=cancel,
                             progress=lambda f: tick(base + (1 - base) * f,
                                                     f"Ghép video · {int(f * 100)}%"))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(out_dir / "output.mp4")
    write_srt(merged, out_dir / "vi.srt")
    atomic_write_text((out_dir / CHAPTERS_TXT), chapters_text(chapters), encoding="utf-8")
    episodes = [e for s in sources for e in s.episodes]
    data = {"id": out_dir.name, "title": title, "created": int(time.time()),
            "duration": round(total, 3), "encode": encode, "reencoded": reencoded,
            "sources": [{"id": s.id, "kind": s.kind, "title": s.title, "start": round(st, 3)}
                        for s, st in zip(sources, starts, strict=True)],
            "chapters": [{"t": t, "name": n} for t, n in chapters],
            "episodes": episodes}
    atomic_write_text((out_dir / COMP_FILE), json.dumps(data, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    for name in ("join.txt", "chapters.ffmeta"):
        (out_dir / name).unlink(missing_ok=True)
    shutil.rmtree(out_dir / NORM_DIR, ignore_errors=True)
    say(f"✓ Ghép xong {len(episodes)} tập · {total / 60:.0f} phút · {len(chapters)} chương · "
        f"{len(merged)} câu phụ đề" + (f" · {reencoded} video phải mã hóa lại" if reencoded else ""))
    return out_dir / "output.mp4"
