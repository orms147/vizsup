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
Style: Default,{font},{size},{primary},&H000000FF,{outline_c},{back},{bold},{italic},0,0,100,100,0,0,{border_style},{outline_w},{shadow},{alignment},{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
"""

# Full subtitle style. Colours are "#RRGGBB"; alpha is 0=opaque..255=transparent.
# border_style: 1=outline+shadow, 3=opaque box (tight), 4=opaque box. alignment is
# the numpad layout (1-3 bottom, 4-6 middle, 7-9 top; 2=bottom-centre default).
DEFAULT_STYLE = {
    "primary": "#FFFFFF",
    "outline": "#000000",
    "box": "#000000",
    "box_alpha": 128,
    "border_style": 1,
    "outline_w": 2,
    "shadow": 0,
    "bold": False,
    "italic": False,
    "alignment": 2,
    "margin_v": 60,
    "margin_l": 40,
    "margin_r": 40,
}


def _ass_colour(hex_str: str, alpha: int = 0) -> str:
    """'#RRGGBB' + alpha → ASS '&HAABBGGRR' (note BGR order, alpha 0=opaque)."""
    h = (hex_str or "").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"&H{max(0, min(255, alpha)):02X}{bb}{gg}{rr}".upper()


def write_ass(
    cues: list[Cue],
    path: Path,
    *,
    font: str = "Be Vietnam Pro",
    size: int = 60,
    margin_v: int = 60,
    cover_hardsubs: bool = False,
    style: dict | None = None,
) -> Path:
    """Write styled ASS. ``style`` overrides any of DEFAULT_STYLE (colour/box/
    position/bold/outline…). ``cover_hardsubs=True`` forces an opaque box
    (BorderStyle=4) to hide leftover burned-in Chinese subtitles."""
    s = {**DEFAULT_STYLE, **(style or {})}
    if cover_hardsubs:
        border, box_alpha = 4, 0           # fully opaque box to hide hardsubs
    else:
        border, box_alpha = int(s["border_style"]), int(s["box_alpha"])
    header = _ASS_HEADER.format(
        font=font,
        size=size,
        primary=_ass_colour(s["primary"], 0),
        outline_c=_ass_colour(s["outline"], 0),
        back=_ass_colour(s["box"], box_alpha),
        bold=-1 if s["bold"] else 0,
        italic=-1 if s["italic"] else 0,
        outline_w=s["outline_w"],
        shadow=s["shadow"],
        border_style=border,
        alignment=int(s["alignment"]),
        margin_l=int(s["margin_l"]),
        margin_r=int(s["margin_r"]),
        margin_v=int(s.get("margin_v", margin_v)),
    )
    lines = [header]
    for c in cues:
        text = c.text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(c.start)},{_fmt_ass_time(c.end)},Default,,0,0,0,,{text}"
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)
