"""Subtitle (de)serialization. Cue lists are the in-memory representation;
SRT and ASS are just on-disk formats.

ASS styling deliberately uses a Vietnamese-diacritic font and an opaque box
(BorderStyle=4) so burned subtitles cover any leftover Chinese hardsubs.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.models import Cue

_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _fmt_srt_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    cs = int(round((t - int(t)) * 100))
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def cues_to_dicts(cues: list[Cue]) -> list[dict]:
    return [{"index": c.index, "start": c.start, "end": c.end, "text": c.text} for c in cues]


def dicts_to_cues(rows: list[dict]) -> list[Cue]:
    return [
        Cue(index=int(r.get("index", i + 1)), start=float(r["start"]), end=float(r["end"]), text=str(r.get("text", "")))
        for i, r in enumerate(rows)
    ]


def parse_srt(path: Path) -> list[Cue]:
    text = Path(path).read_text(encoding="utf-8-sig")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        m = None
        body_start = 0
        for i, ln in enumerate(lines[:2]):
            m = _SRT_TIME.search(ln)
            if m:
                body_start = i + 1
                break
        if not m:
            continue
        start = _to_seconds(*m.group(1, 2, 3, 4))
        end = _to_seconds(*m.group(5, 6, 7, 8))
        body = "\n".join(lines[body_start:]).strip()
        cues.append(Cue(index=len(cues) + 1, start=start, end=end, text=body))
    return cues


def write_srt(cues: list[Cue], path: Path) -> Path:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_fmt_srt_time(c.start)} --> {_fmt_srt_time(c.end)}\n{c.text}\n")
    Path(path).write_text("\n".join(out), encoding="utf-8")
    return Path(path)


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,{border_style},2,0,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(
    cues: list[Cue],
    path: Path,
    *,
    font: str = "Be Vietnam Pro",
    size: int = 60,
    margin_v: int = 60,
    cover_hardsubs: bool = False,
) -> Path:
    """Write styled ASS. ``cover_hardsubs=True`` uses an opaque box (BorderStyle=4)
    to hide leftover burned-in Chinese subtitles behind the Vietnamese line."""
    header = _ASS_HEADER.format(
        font=font,
        size=size,
        margin_v=margin_v,
        border_style=4 if cover_hardsubs else 1,
    )
    lines = [header]
    for c in cues:
        text = c.text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(c.start)},{_fmt_ass_time(c.end)},Default,,0,0,0,,{text}"
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)
