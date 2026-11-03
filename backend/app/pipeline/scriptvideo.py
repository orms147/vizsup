"""«Video từ kịch bản» (2026-11-03): the owner's own text → a voiced video, no source video.

Input: a script (.txt), and either a few clips (played in order, round and round until the
voice is done) or ONE picture (held for the whole voice). Output: ``output.mp4`` 1920×1080
(+ ``output.vertical.mp4`` 9:16 when asked) in ``<work_dir>``, the narration ``narration.wav``,
and ``script.srt`` (the captions, timed by the real length of each spoken line).

Stages (each cached in ``<work_dir>`` so a second run only redoes what changed):
split the script into lines → speak each line (the project's TTS providers, ``tts/<hash>.wav``)
→ narration = lines + a short gap → background (picture loop, or the clips scaled to one size
and cycled to the narration length) → one encode (stream copy for clips when nothing is
burned in). ``make_audio`` stops after the narration and writes ``narration.mp3``.
No approval gate here: the script is the owner's own words, nothing is translated.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from app import fsutil
from app.models import Cue

SIZE = (1920, 1080)
FPS = 30
GAP = 0.28                    # silence between two lines
TAIL = 0.6                    # the picture stays a moment after the last word
MAX_LINE = 170                # a line longer than this is cut at a comma / space
MIN_LINE = 12                 # shorter pieces are joined to their neighbour
IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VIDEOS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts")
_END = re.compile(r"(?<=[.!?…。！？])\s+")
_SOFT = re.compile(r"(?<=[,;:，；：])\s+")


# --- the script ---------------------------------------------------------------------------

def split_script(text: str, *, max_chars: int = MAX_LINE) -> list[str]:
    """The lines the voice reads: every paragraph is cut after . ! ? …; one longer than
    ``max_chars`` is cut at a comma, then at a space; a piece under ``MIN_LINE`` characters is
    joined to the one before it (a lone "Ừ." is not a line). Blank lines and #-comments are
    dropped. Pure."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        para = " ".join(raw.split())
        if not para or para.startswith("#"):
            continue
        merged: list[str] = []                     # a paragraph is a hard border for the merge
        for sent in _END.split(para):
            for s in _fit(sent.strip(), max_chars):
                if merged and len(s) < MIN_LINE and len(merged[-1]) + len(s) + 1 <= max_chars:
                    merged[-1] += " " + s
                else:
                    merged.append(s)
        out.extend(merged)
    return out


def _fit(s: str, max_chars: int) -> list[str]:
    if len(s) <= max_chars:
        return [s] if s else []
    for splitter in (_SOFT, re.compile(r"\s+")):
        parts, cur = [], ""
        for piece in splitter.split(s):
            if cur and len(cur) + 1 + len(piece) > max_chars:
                parts.append(cur)
                cur = piece
            else:
                cur = f"{cur} {piece}".strip()
        parts.append(cur)
        if all(len(p) <= max_chars for p in parts):
            return [p for p in parts if p]
    return [s[i:i + max_chars] for i in range(0, len(s), max_chars)]    # no space at all


def kind_of(paths: list[Path]) -> str:
    """"image" (one picture) | "video" (clips, in the given order). Raises ValueError for a
    mixed / empty / unknown list. Pure."""
    exts = [Path(p).suffix.lower() for p in paths]
    if not exts:
        raise ValueError("Chọn ít nhất một video hoặc một ảnh nền.")
    if all(e in IMAGES for e in exts):
        return "image"
    if all(e in VIDEOS for e in exts):
        return "video"
    raise ValueError("Chỉ chọn video HOẶC ảnh (không trộn), đúng định dạng thông dụng.")


def cycle(durations: list[float], need: float) -> list[int]:
    """Indexes of the clips, in order and round again, until their lengths add up to at least
    ``need`` seconds. Pure. A zero-length list gives nothing."""
    good = [d for d in durations if d > 0.05]
    if not good or need <= 0:
        return []
    seq: list[int] = []
    total, i = 0.0, 0
    while total < need - 1e-6 and len(seq) < 100_000:
        d = durations[i % len(durations)]
        if d > 0.05:
            seq.append(i % len(durations))
            total += d
        i += 1
    return seq


# --- the voice ----------------------------------------------------------------------------

def _hash(provider: str, voice: str | None, rate: str | None, text: str) -> str:
    return hashlib.sha1(f"{provider}|{voice}|{rate}|{text}".encode()).hexdigest()[:16]


def _to_wav(src: Path, dest: Path) -> None:
    from app.ffmpegutil import ffmpeg_bin

    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "48000",
                    "-c:a", "pcm_s16le", str(dest)], check=True, capture_output=True)


def speak_lines(work_dir: Path, lines: list[str], *, provider: str, voice: str | None,
                rate: str | None = None, log: Callable[[str], None] | None = None,
                progress: Callable[[float], None] | None = None,
                cancel: Callable[[], bool] | None = None,
                _sleep: Callable[[float], None] = None) -> list[Path | None]:
    """One 48 kHz mono wav per line (None = the voice could not say it). A line already spoken
    with the same voice / speed is reused; a local voice that speaks several lines at once
    (``tts.parallel``) does."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.pipeline import speak, tts as tts_mod
    from app.providers import registry

    provider, voice = registry.split_voice(provider, voice)
    engine = registry.get_tts(provider)
    sleep = _sleep or time.sleep
    folder = Path(work_dir) / "tts"
    folder.mkdir(parents=True, exist_ok=True)
    said = [speak.spoken(t) for t in lines]
    wavs: list[Path | None] = [None] * len(lines)
    todo: list[int] = []
    for i, s in enumerate(said):
        wav = folder / f"{_hash(provider, voice, rate, s)}.wav"
        if wav.exists() and wav.stat().st_size > 0:
            wavs[i] = wav
        elif s.strip():
            todo.append(i)
    done = len(lines) - len(todo) - sum(1 for s in said if not s.strip())
    total = max(1, len(lines))

    def one(i: int) -> tuple[int, Path | None]:
        raw = folder / f"_{_hash(provider, voice, rate, said[i])}.mp3"
        wav = folder / f"{_hash(provider, voice, rate, said[i])}.wav"
        try:
            tts_mod._synth_retry(engine, said[i], raw, voice=voice, rate=rate, sleep=sleep)
        except Exception as exc:  # noqa: BLE001
            if tts_mod._unvoiceable(exc):
                return i, None
            raise tts_mod._explain(exc) from exc
        tts_mod.trim_silence(raw)
        _to_wav(raw, wav)
        raw.unlink(missing_ok=True)
        return i, wav

    width = max(1, int(getattr(engine, "parallel", 1) or 1))
    if log and todo:
        log(f"… đọc {len(todo)} câu" + (f" ({width} câu cùng lúc)" if width > 1 else ""))
    with ThreadPoolExecutor(max_workers=width) as ex:
        futs = []
        for i in todo:
            futs.append(ex.submit(one, i))
        try:
            for f in futs:
                if cancel and cancel():
                    for g in futs:
                        g.cancel()
                    raise InterruptedError("đã hủy")
                i, wav = f.result()
                wavs[i] = wav
                if wav is None and log:
                    log(f"⚠ Câu {i + 1}: giọng đọc không phát âm được — bỏ qua câu này.")
                done += 1
                if progress:
                    progress(min(1.0, done / total))
        except BaseException:
            for g in futs:
                g.cancel()
            raise
    return wavs


def build_narration(work_dir: Path, lines: list[str], wavs: list[Path | None], *,
                    gap: float = GAP) -> tuple[Path, list[Cue]]:
    """``narration.wav`` = the spoken lines with ``gap`` s of silence between them, and the
    caption cues timed by each line's real length. Lines with no voice are left out."""
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline.assemble import probe_duration

    wd = Path(work_dir)
    gap_wav = wd / "tts" / "_gap.wav"
    if not gap_wav.exists():
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"anullsrc=r=48000:cl=mono", "-t", f"{gap:.3f}", "-c:a", "pcm_s16le",
                        str(gap_wav)], check=True, capture_output=True)
    items = [(t, w) for t, w in zip(lines, wavs, strict=True) if w is not None]
    if not items:
        raise RuntimeError("Không có câu nào đọc được — kiểm tra kịch bản và giọng đọc.")
    cues: list[Cue] = []
    listing: list[str] = []
    t = 0.0
    for n, (text, wav) in enumerate(items, 1):
        d = probe_duration(wav)
        cues.append(Cue(index=n, start=round(t, 3), end=round(t + d, 3), text=text))
        t += d + gap
        listing.append(f"file '{wav.resolve().as_posix()}'")
        listing.append(f"file '{gap_wav.resolve().as_posix()}'")
    lst = wd / "narration.txt"
    fsutil.atomic_write_text(lst, "\n".join(listing) + "\n")
    out = wd / "narration.wav"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c:a", "pcm_s16le", str(out)], check=True, capture_output=True)
    return out, cues


# --- the picture --------------------------------------------------------------------------

def _scale_chain(size: tuple[int, int] = SIZE) -> str:
    w, h = size
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS},format=yuv420p")


def _blur_fill(size: tuple[int, int] = SIZE) -> str:
    """Filter graph: the clip sits whole on a blurred copy of itself (no black bars)."""
    w, h = size
    return (f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=20:4,eq=brightness=-0.08[bg];[b]scale={w}:{h}:force_original_aspect_ratio="
            f"decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},format=yuv420p")


def normalize_clips(work_dir: Path, clips: list[Path], need: float, *,
                    log: Callable[[str], None] | None = None,
                    cancel: Callable[[], bool] | None = None) -> list[Path]:
    """Each clip → ``bg/<n>.mp4`` at 1920×1080, 30 fps, no sound (cached by file + size/mtime),
    at most ``need`` s of it (a 3 h clip is not encoded whole for a 4 min voice)."""
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline import assemble

    folder = Path(work_dir) / "bg"
    folder.mkdir(parents=True, exist_ok=True)
    encoder = assemble._video_encoder()
    out: list[Path] = []
    for n, src in enumerate(clips, 1):
        if cancel and cancel():
            raise InterruptedError("đã hủy")
        st = Path(src).stat()
        key = hashlib.sha1(f"{Path(src).resolve()}|{st.st_size}|{int(st.st_mtime)}|{need:.0f}"
                           .encode()).hexdigest()[:10]
        dest = folder / f"{n:02d}_{key}.mp4"
        if not (dest.exists() and dest.stat().st_size > 0):
            for old in folder.glob(f"{n:02d}_*.mp4"):
                old.unlink(missing_ok=True)
            if log:
                log(f"… chuẩn hóa video {n}/{len(clips)}: {Path(src).name}")
            part = dest.with_name(dest.stem + ".part.mp4")
            while True:
                cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(src), "-t", f"{need + 2:.2f}",
                       "-an", "-filter_complex", f"[0:v]{_blur_fill()}[v]", "-map", "[v]",
                       *assemble._video_args(encoder, False), "-movflags", "+faststart", str(part)]
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                    break
                except subprocess.CalledProcessError as exc:
                    part.unlink(missing_ok=True)
                    if encoder == "libx264":
                        tail = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-2:]
                        raise RuntimeError(f"ffmpeg không đọc được {Path(src).name}: "
                                           + " | ".join(tail)) from exc
                    encoder = "libx264"
            fsutil.replace(part, dest)
        out.append(dest)
    return out


def write_background(work_dir: Path, clips: list[Path], need: float) -> Path:
    """``bg/list.txt`` for the concat demuxer: the normalized clips, cycled to ``need`` s."""
    from app.pipeline.assemble import probe_duration

    durs = [probe_duration(c) for c in clips]
    seq = cycle(durs, need)
    if not seq:
        raise RuntimeError("Các video chọn không đọc được (độ dài 0).")
    lst = Path(work_dir) / "bg" / "list.txt"
    fsutil.atomic_write_text(lst, "".join(f"file '{clips[i].resolve().as_posix()}'\n" for i in seq))
    return lst


def new_dir(storage: Path | None = None) -> Path:
    """A fresh ``<storage>/_scriptvideos/<id>`` (the library lists only real projects, so
    these stay out of it; the dialog shows them)."""
    import time
    import uuid

    from app.config import settings

    base = Path(storage or settings.vizsup_storage_dir) / "_scriptvideos"
    d = base / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4])
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- the whole thing ----------------------------------------------------------------------

def _narrate(wd: Path, script: str, *, provider: str, voice: str | None, rate: str | None,
             log: Callable[[str], None], tick: Callable[[str, float], None],
             cancel: Callable[[], bool] | None) -> tuple[Path, list[Cue], int]:
    """script → (narration.wav, caption cues, lines): speak every line (cached), join them."""
    from app.pipeline.srt import write_srt

    lines = split_script(script)
    if not lines:
        raise ValueError("Kịch bản trống.")
    log(f"Kịch bản: {len(lines)} câu")
    wavs = speak_lines(wd, lines, provider=provider, voice=voice, rate=rate, log=log,
                       progress=lambda f: tick("Đọc kịch bản", f), cancel=cancel)
    narration, cues = build_narration(wd, lines, wavs)
    write_srt(cues, wd / "script.srt")
    return narration, cues, len(lines)


def make_audio(work_dir: Path, script: str, *, provider: str = "edge", voice: str | None = None,
               rate: str | None = None, log: Callable[[str], None] | None = None,
               progress: Callable[[str, float], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> Path:
    """Script → ``<work_dir>/narration.mp3`` (loudness −14 LUFS, like the videos) + the line
    timings ``script.srt``. No picture, no video encode: seconds, not minutes."""
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline import assemble

    say = log or (lambda _m: None)
    tick = progress or (lambda _s, _f: None)
    wd = Path(work_dir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    narration, _cues, _n = _narrate(wd, script, provider=provider, voice=voice, rate=rate,
                                    log=say, tick=tick, cancel=cancel)
    dur = assemble.probe_duration(narration)
    tick("Lưu mp3", 0.0)
    out = wd / "narration.mp3"
    part = wd / "narration.part.mp3"
    try:
        assemble._run_ffmpeg([ffmpeg_bin(), "-y", "-i", str(narration), "-af",
                              "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "44100", "-c:a", "libmp3lame",
                              "-b:a", "192k", "-f", "mp3", str(part)], wd, total=dur,
                             progress=lambda f: tick("Lưu mp3", f), cancel=cancel)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    fsutil.replace(part, out)
    say(f"✓ Âm thanh: {out.name} ({dur / 60:.1f} phút)")
    tick("Xong", 1.0)
    return out


def build(work_dir: Path, script: str | None, backgrounds: list[Path], *, provider: str = "edge",
          voice: str | None = None, rate: str | None = None, audio: Path | None = None,
          watermark: dict | None = None, also_vertical: bool = False, preset: str | None = None,
          log: Callable[[str], None] | None = None,
          progress: Callable[[str, float], None] | None = None,
          cancel: Callable[[], bool] | None = None) -> Path:
    """Script (or an existing ``audio`` file) + background → ``<work_dir>/output.mp4``.
    ``watermark``: the translator's mark (disclosure.WM_DEFAULT), the only thing burned in —
    without it the clips are joined by stream copy, no second encode. ``progress(stage, 0..1)``."""
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline import assemble, disclosure
    from app.pipeline.srt import write_ass

    say = log or (lambda _m: None)
    tick = progress or (lambda _s, _f: None)
    wd = Path(work_dir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    backgrounds = [Path(b) for b in backgrounds]
    kind = kind_of(backgrounds)
    for b in backgrounds:
        if not b.exists():
            raise FileNotFoundError(f"Không thấy tệp: {b}")
    lines = 0
    if audio is not None:
        audio = Path(audio)
        if not audio.exists():
            raise FileNotFoundError(f"Không thấy tệp âm thanh: {audio}")
        narration = audio
        dur = assemble.probe_duration(audio)
        if dur <= 0:
            raise RuntimeError("Không đọc được độ dài của tệp âm thanh.")
        say(f"Âm thanh có sẵn: {audio.name} ({dur / 60:.1f} phút)")
    else:
        narration, _cues, lines = _narrate(wd, script or "", provider=provider, voice=voice,
                                           rate=rate, log=say, tick=tick, cancel=cancel)
        dur = assemble.probe_duration(narration) + TAIL
        say(f"✓ Giọng đọc: {dur / 60:.1f} phút")

    marks = disclosure.watermark_events(watermark, dur)
    ass_arg = ""
    if marks:
        write_ass([], wd / "mark.ass", mark=marks, mark_look=watermark)
        ass_arg = f"ass=mark.ass{assemble._ass_fonts(wd)}"

    tick("Dựng video", 0.0)
    out = wd / "output.mp4"
    part = wd / ("output" + assemble.RENDERING_SUFFIX)
    encoder = assemble._video_encoder()
    if kind == "image":
        # a held picture: a few frames a second are enough (and an encode that takes seconds)
        inputs = ["-loop", "1", "-framerate", "10", "-i", str(backgrounds[0])]
        vf = f"[0:v]{_blur_fill()}" + (f",{ass_arg}" if ass_arg else "") + "[v]"
        copy_video = False
    else:
        clips = normalize_clips(wd, backgrounds, dur, log=say, cancel=cancel)
        lst = write_background(wd, clips, dur)
        inputs = ["-f", "concat", "-safe", "0", "-i", str(lst)]
        vf = f"[0:v]{ass_arg or 'null'}[v]"
        copy_video = not ass_arg                  # the clips are already 1920×1080 / 30 fps
    while True:
        if copy_video:
            cmd = [ffmpeg_bin(), "-y", *inputs, "-i", str(narration), "-map", "0:v", "-c:v", "copy",
                   "-map", "1:a"]
        else:
            cmd = [ffmpeg_bin(), "-y", *inputs, "-i", str(narration), "-filter_complex", vf,
                   "-map", "[v]", "-map", "1:a", *assemble._video_args(encoder, False, preset)]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                "-t", f"{dur:.3f}", "-movflags", "+faststart", str(part)]
        try:
            assemble._run_ffmpeg(cmd, wd, total=dur, progress=lambda f: tick("Dựng video", f),
                                 cancel=cancel)
            break
        except RuntimeError:
            part.unlink(missing_ok=True)
            if encoder == "libx264" or copy_video:
                raise
            encoder = "libx264"
            say("⚠ Mã hóa bằng GPU (NVENC) lỗi — dựng bằng CPU.")
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    fsutil.replace(part, out)
    say(f"✓ Video: {out.name}")
    if also_vertical:
        from app.pipeline import vertical

        tick("Bản dọc 9:16", 0.0)
        vertical.make_vertical(out, preset=preset, log=say,
                               progress=lambda f: tick("Bản dọc 9:16", f), cancel=cancel)
    fsutil.atomic_write_text(wd / "scriptvideo.json", json.dumps(
        {"kind": kind, "lines": lines, "seconds": round(dur, 1), "provider": provider,
         "voice": voice, "rate": rate, "audio": str(audio) if audio else None,
         "watermark": bool(marks), "backgrounds": [str(b) for b in backgrounds]},
        ensure_ascii=False, indent=1))
    tick("Xong", 1.0)
    return out


def still(work_dir: Path, background: Path, watermark: dict | None = None) -> Path:
    """The first frame of the finished video — the background on its blurred fill with the
    watermark burned the way ``build`` burns it → ``<work_dir>/still.png`` (960 wide). A second
    of work, so the page can show the owner the picture BEFORE the voice is made."""
    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline import assemble, disclosure
    from app.pipeline.srt import write_ass

    wd = Path(work_dir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    chain = f"[0:v]{_blur_fill()}"
    marks = disclosure.watermark_events(watermark, 10.0)
    if marks:
        write_ass([], wd / "mark_still.ass", mark=marks, mark_look=watermark)
        chain += f",ass=mark_still.ass{assemble._ass_fonts(wd)}"
    out = wd / "still.png"
    out.unlink(missing_ok=True)
    done = subprocess.run(
        [ffmpeg_bin(), "-y", "-v", "error", "-i", str(Path(background).resolve()),
         "-filter_complex", chain + ",scale=960:-2[v]", "-map", "[v]", "-frames:v", "1", "still.png"],
        cwd=str(wd), capture_output=True)
    if done.returncode != 0 or not out.exists():
        raise RuntimeError("Không đọc được khung hình: " + done.stderr.decode("utf-8", "replace")[-200:])
    return out
