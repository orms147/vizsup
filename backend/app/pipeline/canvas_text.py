"""Text on the 9:16 CANVAS, in the bars above and below a 16:9 picture (Shorts).

The normal subtitle .ass is burned on the fitted picture BEFORE the bars are added (sharper text,
the same layout as a source render), so nothing it draws can land in the big blurred bars — a Short
then has tiny text over the picture and two empty bars. This is a SECOND .ass, PlayRes = the canvas
in pixels, burned on the composed frame (``[vt]``): the hook title and the episode chip in the top
bar, "Xem full tập N" / "Còn tiếp" in the bottom bar and, on request, the dialogue itself in the
bottom bar (larger, above the platform's caption strip).

Everything is placed from ``app.pipeline.safezones`` (the strips TikTok / Reels / Shorts cover) and
only where the bars are tall enough; otherwise :func:`layout` returns None for that band and the
caller keeps the text on the picture. Pure — no ffmpeg, no files except :func:`write`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.fsutil import atomic_write_text
from app.models import Cue
from app.pipeline import safezones
from app.pipeline.srt import _ass_colour, _fmt_ass_time

GAP = 0.02                 # fraction of the canvas kept between a text and a covered strip
EDGE = 0.012               # … and between a text and the picture's edge
MIN_BAND = 0.06            # a band shorter than this (fraction of the height) holds no text
CHAR_W = 0.46              # average glyph advance ÷ font size (Vietnamese, bold)
LINE_H = 1.22              # line height ÷ font size
HOOK_MAX, HOOK_MIN = 0.075, 0.042      # hook font size ÷ canvas width: largest tried … smallest
CHIP_SIZE = 0.058
END_MAX = 0.066
SUB_SIZE = 0.06
HOOK_SECONDS, END_SECONDS = 3.0, 3.0


@dataclass(frozen=True)
class Layout:
    cw: int
    ch: int
    pic_top: float
    pic_bottom: float
    top: tuple[float, float] | None        # (y0, y1) of the free band above the picture
    bottom: tuple[float, float] | None     # … below it, above the platform's caption strip
    margin_l: int                           # text box edges (px): clear of the button column
    margin_r: int


def layout(canvas: tuple[int, int], picture: tuple[int, int] | None,
           platform: str | None = "tiktok") -> Layout | None:
    """Where text may go on the canvas, or None when there are no bars (a picture that fills the
    frame) or no free band at all. ``picture`` is the burned picture's size on this canvas."""
    cw, ch = canvas
    if not picture or picture[1] >= ch * 0.98:
        return None
    plat = safezones.SAFE_ZONES.get(platform or "") or safezones.SAFE_ZONES["tiktok"]
    pt = (ch - picture[1]) / 2
    pb = pt + picture[1]
    top = (round((plat.top + GAP) * ch), round(pt - EDGE * ch))
    bottom = (round(pb + EDGE * ch), round((1 - plat.bottom - GAP) * ch))
    top = top if top[1] - top[0] >= MIN_BAND * ch else None
    bottom = bottom if bottom[1] - bottom[0] >= MIN_BAND * ch else None
    if top is None and bottom is None:
        return None
    return Layout(cw, ch, pt, pb, top, bottom, round(0.04 * cw),
                  round((plat.right + GAP) * cw))


def lines_needed(text: str, size: float, width: float) -> int:
    """How many lines ``text`` takes at ``size`` px in a box ``width`` px wide (a rough
    glyph-width model; explicit line breaks count). Pure."""
    cap = max(1, int(width / (CHAR_W * size)))
    return sum(max(1, math.ceil(len(part) / cap)) for part in (text or "").split("\n"))


def fit_size(text: str, width: float, height: float, *, largest: float, smallest: float,
             max_lines: int = 2) -> tuple[int, int]:
    """(font px, lines): the largest size in [smallest, largest] whose text fits ``max_lines``
    lines and ``height`` px; the smallest when none does. Pure."""
    s = int(largest)
    while s > smallest:
        n = lines_needed(text, s, width)
        if n <= max_lines and n * LINE_H * s <= height:
            return s, n
        s -= 2
    s = int(smallest)
    return s, lines_needed(text, s, width)


def _esc(text: str) -> str:
    """User text → ASS: no override block can be smuggled in, line breaks as \\N."""
    t = str(text or "").replace("{", "").replace("}", "").replace("\\", "")
    return "\\N".join(" ".join(p.split()) for p in t.split("\n")).strip()


def _style(name: str, font: str, size: int, *, primary: str = "#FFFFFF", outline: str = "#000000",
           outline_w: float = 3, box: bool = False, align: int = 8, ml: int = 0, mr: int = 0,
           mv: int = 0, bold: bool = True, box_alpha: int = 90) -> str:
    border = 4 if box else 1
    back = _ass_colour("#000000", box_alpha if box else 255)
    return (f"Style: {name},{font},{size},{_ass_colour(primary)},&H000000FF,"
            f"{_ass_colour(outline)},{back},{-1 if bold else 0},0,0,0,100,100,0,0,{border},"
            f"{max(0, round(outline_w))},0,{align},{ml},{mr},{mv},1")


HEADER = ("[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n"
          "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, "
          "PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
          "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
          "MarginL, MarginR, MarginV, Encoding\n")
EVENTS = ("\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, "
          "Text\n")


def build(lay: Layout, *, font: str = "Be Vietnam Pro", chip: str = "", hook: str = "",
          end: str = "", duration: float = 0.0, cues: list[Cue] | None = None,
          style: dict | None = None, end_seconds: float = END_SECONDS) -> str | None:
    """The canvas .ass text: ``chip`` + ``hook`` stacked in the top band for the first
    HOOK_SECONDS, ``end`` in the bottom band for the last ``end_seconds``, and — when ``cues`` is
    given — the dialogue in the bottom band (cut short where the end card starts). None when
    there is nothing to draw. Pure."""
    cw, ch = lay.cw, lay.ch
    st = style or {}
    primary = st.get("primary") if st.get("primary") and not st.get("glass") else "#FFFFFF"
    outline = st.get("outline") if st.get("outline") and not st.get("glass") else "#000000"
    bold = bool(st.get("bold", True))
    box_w = cw - lay.margin_l - lay.margin_r                   # clear of the button column
    full_w = cw - 2 * lay.margin_l
    styles: list[str] = []
    events: list[str] = []
    dur = float(duration or 0)

    # --- top band: chip over hook ----------------------------------------------------------
    if lay.top and (chip or hook):
        y0, y1 = lay.top
        band_h = y1 - y0
        chip_px = round(CHIP_SIZE * cw) if chip else 0
        chip_h = round(chip_px * LINE_H + 0.03 * cw) if chip else 0
        gap = round(0.012 * ch) if chip and hook else 0
        hook_px, hook_lines = (fit_size(_esc(hook).replace("\\N", "\n"), full_w,
                                        band_h - chip_h - gap, largest=HOOK_MAX * cw,
                                        smallest=HOOK_MIN * cw) if hook else (0, 0))
        hook_h = round(hook_lines * LINE_H * hook_px)
        total = chip_h + gap + hook_h
        top = y0 + max(0, (band_h - total) // 2)
        end_t = min(HOOK_SECONDS, dur) if dur > 1 else HOOK_SECONDS
        if chip:
            styles.append(_style("CanvasChip", font, chip_px, box=True, outline_w=0.012 * cw,
                                 align=8, mv=top, bold=True, box_alpha=70,
                                 primary="#FFFFFF", outline="#000000"))
            events.append(f"Dialogue: 2,{_fmt_ass_time(0.0)},{_fmt_ass_time(end_t)},CanvasChip,,"
                          f"0,0,{top},,{_esc(chip)}")
        if hook:
            styles.append(_style("CanvasHook", font, hook_px, outline_w=max(3, 0.1 * hook_px),
                                 align=8, ml=lay.margin_l, mr=lay.margin_l, mv=top + chip_h + gap))
            events.append(f"Dialogue: 2,{_fmt_ass_time(0.0)},{_fmt_ass_time(end_t)},CanvasHook,,"
                          f"{lay.margin_l},{lay.margin_l},{top + chip_h + gap},,{_esc(hook)}")

    # --- bottom band: the end card, and the dialogue ----------------------------------------
    end_start = dur - end_seconds if end and dur > 2 * end_seconds else None
    if lay.bottom and end and end_start is not None:
        y0, y1 = lay.bottom
        px, n = fit_size(_esc(end).replace("\\N", "\n"), full_w, y1 - y0, largest=END_MAX * cw,
                         smallest=0.04 * cw)
        mv = y0 + max(0, round(((y1 - y0) - n * LINE_H * px) / 2))
        styles.append(_style("CanvasEnd", font, px, outline_w=max(3, 0.1 * px), align=8,
                             ml=lay.margin_l, mr=lay.margin_l, mv=mv))
        events.append(f"Dialogue: 3,{_fmt_ass_time(end_start)},{_fmt_ass_time(dur)},CanvasEnd,,"
                      f"{lay.margin_l},{lay.margin_l},{mv},,{_esc(end)}")
    if lay.bottom and cues is not None:
        y0, y1 = lay.bottom
        avail = y1 - y0
        size = min(round(SUB_SIZE * cw), int(avail / (2 * LINE_H)))
        # bottom-aligned: the last line rests on the strip's edge, growing up into the bar
        styles.append(_style("CanvasSub", font, size, primary=primary, outline=outline,
                             outline_w=max(3, 0.09 * size), align=2, ml=lay.margin_l,
                             mr=lay.margin_r, mv=ch - y1, bold=bold))
        for c in cues:
            a, b = c.start, c.end
            if end_start is not None:
                if a >= end_start:
                    continue
                b = min(b, end_start)
            text = _esc(c.text)
            if not text or b <= a:
                continue
            n = lines_needed(c.text, size, box_w)
            pre = ""
            if n > 2:                                        # a long line: smaller, not taller
                pre = "{\\fs%d}" % max(round(size * 0.7), round(size * 2 / n))
            events.append(f"Dialogue: 1,{_fmt_ass_time(a)},{_fmt_ass_time(b)},CanvasSub,,0,0,0,,"
                          f"{pre}{text}")
    if not events:
        return None
    return HEADER.format(w=cw, h=ch) + "\n".join(styles) + EVENTS + "\n".join(events) + "\n"


def write(path: Path, lay: Layout, **kw) -> Path | None:
    """:func:`build` into ``path``; None (nothing written) when there is nothing to draw."""
    text = build(lay, **kw)
    if text is None:
        return None
    atomic_write_text(Path(path), text, encoding="utf-8")
    return Path(path)
