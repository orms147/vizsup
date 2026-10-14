"""Subtitle (de)serialization. Cue lists are the in-memory representation;
SRT and ASS are just on-disk formats.

ASS styling deliberately uses a Vietnamese-diacritic font and an opaque box
(BorderStyle=4) so burned subtitles cover any leftover Chinese hardsubs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.models import Cue

_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _fmt_srt_time(t: float) -> str:
    # Integer divmod chain over total milliseconds. The old form rounded the
    # fraction separately, so 1.9995 became "00:00:01,1000" (4-digit ms) which
    # parses back as 1.1s — a silent cue shrink on every file round-trip.
    total_ms = max(0, round(t * 1000))
    s, ms = divmod(total_ms, 1000)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass_time(t: float) -> str:
    total_cs = max(0, round(t * 100))  # same carry bug existed here at cs == 100
    s, cs = divmod(total_cs, 100)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
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


_HEADER_PRE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"""

_HEADER_POST = """[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""

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
    "pos": None,             # (nx, ny) in 0..1 → free \pos() placement; overrides alignment/margins
    "letter_spacing": 0,     # \fsp: +stretch / -compress letters (to fit 1–2 lines)
}


def _ass_colour(hex_str: str, alpha: int = 0) -> str:
    """'#RRGGBB' + alpha → ASS '&HAABBGGRR' (note BGR order, alpha 0=opaque)."""
    h = (hex_str or "").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"&H{max(0, min(255, alpha)):02X}{bb}{gg}{rr}".upper()


def _style_line(name: str, s: dict, font: str, size: int, cover_hardsubs: bool) -> str:
    if cover_hardsubs:
        border, box_alpha = 4, 0           # opaque box to hide hardsubs (wins over style)
    else:
        border, box_alpha = int(s["border_style"]), int(s["box_alpha"])
    return (
        f"Style: {name},{font},{size},{_ass_colour(s['primary'], 0)},&H000000FF,"
        f"{_ass_colour(s['outline'], 0)},{_ass_colour(s['box'], box_alpha)},"
        f"{-1 if s['bold'] else 0},{-1 if s['italic'] else 0},0,0,100,100,0,0,"
        f"{border},{s['outline_w']},{s['shadow']},{int(s['alignment'])},"
        f"{int(s['margin_l'])},{int(s['margin_r'])},{int(s['margin_v'])},1"
    )


def _inline_prefix(s: dict) -> str:
    """Per-line \\pos / \\fsp override tags drawn from a (merged) style dict."""
    prefix = ""
    pos = s.get("pos")
    if pos:
        try:
            prefix = f"{{\\an5\\pos({round(float(pos[0]) * 1920)},{round(float(pos[1]) * 1080)})}}"
        except (TypeError, ValueError, IndexError):
            prefix = ""
    fsp = s.get("letter_spacing")
    if fsp:
        prefix += f"{{\\fsp{int(fsp)}}}"
    return prefix


def write_ass(
    cues: list[Cue],
    path: Path,
    *,
    font: str = "Be Vietnam Pro",
    size: int = 60,
    margin_v: int = 60,
    cover_hardsubs: bool = False,
    style: dict | None = None,
    cue_styles: dict[int, dict] | None = None,
    extra: list[dict] | None = None,
) -> Path:
    """Write styled ASS. ``style`` is the global style (overrides DEFAULT_STYLE).
    ``cue_styles`` maps a 1-based cue position → a sparse style override applied on
    top of the global one; identical merged styles are de-duplicated and the cues
    are routed to ``Default``/``Sub1``/``Sub2``… With ``cue_styles=None`` the output
    is byte-identical to the single-style path (locked by tests/test_write_ass.py).
    ``cover_hardsubs=True`` forces an opaque box on every style. ``extra``: corner notes
    ``{start, end, text, align}`` (credit, "Lồng tiếng AI") in a small boxed "Tag" style."""
    base = {**DEFAULT_STYLE, **(style or {})}
    ordered: list[tuple[str, dict]] = []   # (name, merged style) — Default first
    seen: dict[str, str] = {}              # json(merged) → name

    def name_for(merged: dict) -> str:
        key = json.dumps(merged, sort_keys=True)
        if key in seen:
            return seen[key]
        nm = "Default" if not ordered else f"Sub{len(ordered)}"
        seen[key] = nm
        ordered.append((nm, merged))
        return nm

    default_name = name_for(base)
    cue_names: list[str] = []
    cue_prefixes: list[str] = []
    for i, c in enumerate(cues):
        ov = (cue_styles or {}).get(i + 1)
        merged = {**base, **ov} if ov else base
        cue_names.append(name_for(merged) if ov else default_name)
        cue_prefixes.append(_inline_prefix(merged))

    style_block = "\n".join(_style_line(nm, st, font, size, cover_hardsubs) for nm, st in ordered)
    if extra:
        # margin_v ~9% of the height: below TikTok's / Douyin's top bar ("Following | For
        # You", search) on a vertical video, which covers roughly the top 7-8%
        tag = {**DEFAULT_STYLE, "border_style": 4, "box": "#000000", "box_alpha": 96,
               "outline_w": 6, "bold": True, "alignment": 7, "margin_l": 36, "margin_r": 36,
               "margin_v": 100}
        style_block += "\n" + _style_line("Tag", tag, font, max(22, round(size * 0.42)), False)
    header = _HEADER_PRE + "\n" + style_block + "\n\n" + _HEADER_POST + "\n"
    lines = [header]
    for c, nm, pfx in zip(cues, cue_names, cue_prefixes):
        text = pfx + c.text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(c.start)},{_fmt_ass_time(c.end)},{nm},,0,0,0,,{text}"
        )
    for e in extra or []:
        text = "{\\an%d}" % int(e.get("align", 7)) + str(e["text"]).replace("\n", "\\N")
        lines.append(f"Dialogue: 1,{_fmt_ass_time(e['start'])},{_fmt_ass_time(e['end'])},Tag,,"
                     f"0,0,0,,{text}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)
