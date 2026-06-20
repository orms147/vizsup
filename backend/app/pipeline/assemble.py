"""Stage: assemble. FFmpeg builds the Vietnamese dub track on an absolute
timeline and burns styled subtitles into the final MP4.

Dub-sync approach (see docs/research.md):
- Each segment is placed at its cue's absolute start (``adelay``) so there is no
  cumulative drift; gaps are silence by construction.
- If a segment is longer than its slot, speed it up with ``atempo`` capped at
  ~1.3x (atempo skips samples above 2x, so we never go near that).
- Mix segments with ``amix``; optionally keep the original audio underneath.

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

ATEMPO_CAP = 1.3


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


def assemble(
    job: Job,
    segments: list[tuple[Cue, Path]],
    *,
    burn_cues: list[Cue] | None = None,
    replace_audio: bool = True,
    cover_hardsubs: bool = False,
    font: str = "Be Vietnam Pro",
    size: int = 60,
    log: Callable[[str], None] | None = None,
) -> Path:
    ffmpeg = ffmpeg_bin()

    cues_for_subs = burn_cues if burn_cues is not None else parse_srt(job.vi_srt)
    write_ass(cues_for_subs, job.subs_ass, font=font, size=size, cover_hardsubs=cover_hardsubs)

    wd = job.work_dir
    cmd = [ffmpeg, "-y", "-i", "source.mp4"]
    for _, seg in segments:
        cmd += ["-i", str(Path(seg).relative_to(wd).as_posix())]

    parts: list[str] = []
    labels: list[str] = []
    sped = 0
    clipped: list[tuple[Cue, float, float]] = []
    for i, (cue, seg) in enumerate(segments, start=1):
        seg_dur = probe_duration(Path(seg))
        slot = cue.duration
        tempo = min(seg_dur / slot, ATEMPO_CAP) if slot > 0 and seg_dur > slot else 1.0
        if abs(tempo - 1.0) > 1e-3:
            sped += 1
            # even at the cap the VI audio is longer than the slot → ffmpeg will cut
            # it off mid-word. Flag it so the user can shorten that line in the editor.
            if slot > 0 and seg_dur > ATEMPO_CAP * slot + 0.05:
                clipped.append((cue, seg_dur, slot))
        delay = max(0, int(round(cue.start * 1000)))
        af = []
        if abs(tempo - 1.0) > 1e-3:
            af.append(f"atempo={tempo:.4f}")
        af.append(f"adelay={delay}|{delay}")
        parts.append(f"[{i}:a]{','.join(af)}[a{i}]")
        labels.append(f"[a{i}]")

    if log:
        if sped:
            log(f"… {sped} câu phải tăng tốc đọc để khớp khe thời gian")
        if clipped:
            log(f"⚠ {len(clipped)} câu QUÁ DÀI: tăng tốc tối đa {ATEMPO_CAP:g}× vẫn không vừa "
                f"→ lời sẽ bị cắt cụt. Hãy rút gọn các câu này ở bước sửa phụ đề:")
            for cue, sd, sl in clipped[:6]:
                log(f"   • [{cue.start:6.1f}s] cần ~{sd:.1f}s nhưng khe chỉ {sl:.1f}s")

    if labels:
        if replace_audio:
            parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[voa]")
        else:
            parts.append(f"[0:a]{''.join(labels)}amix=inputs={len(labels) + 1}:normalize=0:dropout_transition=0[voa]")
        audio_map = "[voa]"
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
