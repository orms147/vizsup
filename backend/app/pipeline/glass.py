"""“Kính mờ trắng” — a frosted white glass behind the Vietnamese subtitle.

The picture under each subtitle is blurred hard (the burned-in Chinese becomes an
unreadable smudge), lightened with a translucent white tint and cut to a rounded
rectangle; the Vietnamese line is then burned on top in near-black. Unlike the
opaque black box it lets the footage show through, and it only appears while a
subtitle is on screen.

How (one ffmpeg graph, :func:`filter_parts`): only a band of the frame is touched —
the union of every glass rectangle — so the cost stays small on long videos:

    split → [band] crop → gblur → saturation → white tint ─┐
          → [band] crop → black → mask.ass → gray ─────────┴ alphamerge → overlay

The rounded rectangles are ASS vector drawings in a mask .ass (white on black,
anti-aliased and timed by libass), written by :func:`write_mask` from the per-cue
geometry of :func:`plan`. Geometry is computed here, not by libass, because each
glass must cover two things: the Vietnamese text (measured with the bundled font,
the way libass lays it out) and the Chinese hardsub band (the detected region, else
the usual Douyin/Bilibili caption band) — merged into one pill when they touch.

Everything is normalized (0..1 of the burned picture) so one plan serves any output
size; pure functions except the optional Pillow text measurement.
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

from app.models import Cue

# Text look on the glass (forced when the cover is "glass" but the style isn't).
GLASS_TEXT = {"primary": "#1C1C1E", "outline": "#FFFFFF", "outline_w": 0, "shadow": 0,
              "border_style": 1, "glass": True}
# The look is adjustable in Kiểu chữ (style.json, global only — one band filter):
#   "glass_tint" = white over the blurred picture, in % (dark footage → light grey glass)
#   "glass_blur" = gblur sigma at 1080 lines (scaled with the frame height)
# Defaults picked on real frames (dark presenter, black-on-white phone grid, bright
# product shots): 60 % read "hơi đậm" (milky); 40 % is a light grey glass on dark
# footage and keeps the dark text readable over high-contrast stripes (35 % and below
# starts losing letters there). Blur 22 stays: it is what hides the Chinese.
TINT = 0.4            # default white tint (fraction)
TINT_RANGE = (0, 90)  # % — the slider; 100% would be an opaque white box
BLUR_SIGMA = 22       # default blur
BLUR_RANGE = (4, 40)  # the slider
# below this a Chinese reader can still make out large (6 %-of-height) burned-in
# captions: ~14 at 1080p, ~16 at 720p (judged on real frames, no tint — the worst case)
BLUR_SAFE = 16
SATURATION = 1.3      # a little "vibrancy", like iOS glass
GAP_BRIDGE = 0.5      # s: a glass stays up across a shorter gap (no flicker, no CN flash)
# no detected region: where Douyin/Bilibili captions usually sit (fraction of height)
DEFAULT_CN_BAND = (0.865, 0.955)
CN_GLYPH = 0.06       # a burned-in Chinese character ≈ 6% of the frame height, square
PAD_X, PAD_Y = 0.65, 0.22   # glass padding around the text, × font size
RADIUS = 0.5          # corner radius × font size (capped at half the glass height)
MASK_FILE = "glass_mask.ass"

_FONTS = Path(__file__).resolve().parents[3] / "desktop" / "assets" / "fonts"
_FONT_FILES = {
    "be vietnam pro": ("BeVietnamPro-Regular.ttf", "BeVietnamPro-Bold.ttf"),
}
_SYSTEM = {
    "arial": ("arial.ttf", "arialbd.ttf"),
    "noto sans": ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"),
}


def is_glass(style: dict | None) -> bool:
    return bool((style or {}).get("glass"))


def look(style: dict | None) -> dict:
    """``{"tint": 0..0.9, "blur": sigma at 1080}`` from a style's ``glass_tint`` (%)
    and ``glass_blur``, clamped to the slider ranges; defaults when unset/garbled."""
    s = style or {}

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(s.get(key, default))
        except (TypeError, ValueError):
            v = default
        return min(hi, max(lo, v)) if math.isfinite(v) else default

    return {"tint": round(num("glass_tint", TINT * 100, *TINT_RANGE) / 100, 2),
            "blur": round(num("glass_blur", BLUR_SIGMA, *BLUR_RANGE), 1)}


def text_style(style: dict | None) -> dict:
    """The style to burn over the glass: the user's own when it already is a glass
    style (they may have recoloured the text), else the dark glass text."""
    s = dict(style or {})
    return s if is_glass(s) else {**s, **GLASS_TEXT}


# --- text measurement (how wide libass will lay the line out) -------------------------

@functools.lru_cache(maxsize=16)
def _font(family: str, bold: bool):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    key = (family or "").strip().lower()
    cands = []
    if key in _FONT_FILES:
        cands.append(_FONTS / _FONT_FILES[key][int(bold)])
    if key in _SYSTEM:
        cands.append(Path("C:/Windows/Fonts") / _SYSTEM[key][int(bold)])
    cands.append(_FONTS / _FONT_FILES["be vietnam pro"][int(bold)])   # close enough
    for p in cands:
        try:
            if p.exists():
                return ImageFont.truetype(str(p), 100)
        except OSError:
            continue
    return None


def text_width(text: str, *, font: str, px: float, bold: bool = False,
               spacing: float = 0.0) -> float:
    """Width in px of one line at ASS font size ``px`` (libass sizes a font so its
    ascent+descent = the size, like VSFilter). Falls back to ~0.55 em per character."""
    f = _font(font, bool(bold))
    n = len(text)
    if f is None:
        return n * px * (0.58 if bold else 0.55) + n * spacing
    asc, desc = f.getmetrics()
    w = f.getlength(text) * px / max(1, asc + desc)
    return w + n * spacing


def text_block(text: str, *, font: str, px: float, wrap_w: float, bold: bool = False,
               spacing: float = 0.0) -> tuple[float, int]:
    """(width, line count) of a cue's text wrapped to ``wrap_w`` like libass'
    WrapStyle 0 (lines balanced to about equal width). Honours explicit breaks."""
    widest, lines = 0.0, 0
    for para in text.replace("\\N", "\n").split("\n"):
        w = text_width(para.strip() or " ", font=font, px=px, bold=bold, spacing=spacing)
        n = max(1, math.ceil(w / max(1.0, wrap_w)))
        widest = max(widest, min(wrap_w, w / n * (1.06 if n > 1 else 1.0)))
        lines += n
    return widest, lines


# --- geometry -------------------------------------------------------------------------

def _union(a: tuple, b: tuple) -> tuple:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def _clamp(r: tuple, W: float, H: float) -> tuple:
    x0, y0 = max(2.0, r[0]), max(2.0, r[1])
    x1, y1 = min(W - 2.0, r[0] + r[2]), min(H - 2.0, r[1] + r[3])
    return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))


def text_rect(text: str, style: dict, *, W: float, H: float, font: str, size: int) -> tuple:
    """The text block's (x, y, w, h) in px on a W×H frame, positioned like libass
    does for this style (alignment + margins, or a free ``pos``)."""
    px = size * H / 1080.0
    ml = float(style.get("margin_l", 40)) * W / 1920.0
    mr = float(style.get("margin_r", 40)) * W / 1920.0
    mv = float(style.get("margin_v", 60)) * H / 1080.0
    spacing = float(style.get("letter_spacing") or 0) * H / 1080.0
    tw, n = text_block(text, font=font, px=px, wrap_w=max(px, W - ml - mr),
                       bold=bool(style.get("bold")), spacing=spacing)
    th = n * px
    pos = style.get("pos")
    if pos:
        try:
            cx, cy = float(pos[0]) * W, float(pos[1]) * H
            return (cx - tw / 2, cy - th / 2, tw, th)
        except (TypeError, ValueError, IndexError):
            pass
    an = int(style.get("alignment", 2))
    col = an % 3                    # 1 left, 2 centre, 0 right
    if col == 1:
        x = ml
    elif col == 0:
        x = W - mr - tw
    else:
        x = (ml + W - mr) / 2 - tw / 2
    if an <= 3:
        y = H - mv - th
    elif an <= 6:
        y = (H - th) / 2
    else:
        y = mv
    return (x, y, tw, th)


def cn_rect(cn_chars: int, *, W: float, H: float, region: dict | None) -> tuple:
    """Where the burned-in Chinese line sits, in px: the detected region (narrowed
    to the line's estimated width when the overlapping CN text is known), else the
    usual bottom caption band, centred."""
    if region:
        rx, ry, rw, rh = (region["x"] * W, region["y"] * H, region["w"] * W, region["h"] * H)
        w = rw if cn_chars <= 0 else min(rw, cn_chars * min(rh, CN_GLYPH * 1.6 * H) * 1.08)
        return (rx + (rw - w) / 2, ry, w, rh)
    y0, y1 = DEFAULT_CN_BAND
    w = 0.6 * W if cn_chars <= 0 else min(0.96 * W, cn_chars * CN_GLYPH * H)
    return ((W - w) / 2, y0 * H, w, (y1 - y0) * H)


def _cn_chars(cn: list[Cue], start: float, end: float) -> int:
    """Characters of the longest Chinese cue overlapping [start, end)."""
    best = 0
    for c in cn:
        if c.end > start and c.start < end:
            best = max(best, len("".join((c.text or "").split())))
    return best


def plan(cues: list[Cue], *, W: float, H: float, font: str, size: int, style: dict | None,
         cue_styles: dict[int, dict] | None = None, cn_cues: list[Cue] | None = None,
         region: dict | None = None, hardsubs: bool = True) -> list[dict]:
    """One glass event per subtitle: ``{start, end, rects: [[x, y, w, h, r], …]}``
    normalized to the W×H picture (r = corner radius / H). The Vietnamese pill and
    the Chinese band merge into one rounded rect when they touch; a subtitle moved
    away from the hardsubs gets two. Short gaps are bridged. Pure."""
    base = {**(style or {})}
    px = size * H / 1080.0
    pad_x, pad_y = PAD_X * px, PAD_Y * px
    cn = cn_cues or []
    events: list[dict] = []
    for i, c in enumerate(cues):
        text = (c.text or "").strip()
        if not text:
            continue
        ov = (cue_styles or {}).get(i + 1)
        st = {**base, **ov} if ov else base
        tx, ty, tw, th = text_rect(text, st, W=W, H=H, font=font, size=size)
        vi = (tx - pad_x, ty - pad_y, tw + 2 * pad_x, th + 2 * pad_y)
        rects = [vi]
        if hardsubs or region:
            cr = cn_rect(_cn_chars(cn, c.start, c.end), W=W, H=H, region=region)
            cr = (cr[0] - pad_x / 2, cr[1] - pad_y / 2, cr[2] + pad_x, cr[3] + pad_y)
            gap = max(cr[1] - (vi[1] + vi[3]), vi[1] - (cr[1] + cr[3]))
            if gap < 0.35 * px:
                rects = [_union(vi, cr)]
            else:
                rects = [vi, cr]
        out = []
        for r in rects:
            x, y, w, h = _clamp(r, W, H)
            rad = min(h / 2, RADIUS * px)
            out.append([round(x / W, 5), round(y / H, 5), round(w / W, 5), round(h / H, 5),
                        round(rad / H, 5)])
        events.append({"start": float(c.start), "end": float(c.end), "rects": out})
    events.sort(key=lambda e: e["start"])
    for a, b in zip(events, events[1:]):
        if 0 < b["start"] - a["end"] < GAP_BRIDGE:
            a["end"] = b["start"]
    return events


def band(events: list[dict]) -> tuple[float, float, float, float] | None:
    """Normalized (x, y, w, h) covering every glass — the only part of the frame the
    graph blurs. Snapped outward to 1/200 so the crop is stable; None = no glass."""
    rs = [r for e in events for r in e["rects"]]
    if not rs:
        return None
    x0 = math.floor(min(r[0] for r in rs) * 200) / 200
    y0 = math.floor(min(r[1] for r in rs) * 200) / 200
    x1 = math.ceil(max(r[0] + r[2] for r in rs) * 200) / 200
    y1 = math.ceil(max(r[1] + r[3] for r in rs) * 200) / 200
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)
    return (x0, y0, round(x1 - x0, 4), round(y1 - y0, 4))


# --- the mask .ass + the filter graph ------------------------------------------------------

def rounded_rect_path(x: float, y: float, w: float, h: float, r: float) -> str:
    """ASS drawing (\\p1) commands for a rounded rectangle (cubic-bezier corners)."""
    r = max(0.0, min(r, w / 2, h / 2))
    k = 0.5523 * r                      # bezier handle for a quarter circle

    def f(v: float) -> str:
        return f"{v:.1f}".rstrip("0").rstrip(".")

    x1, y1 = x + w, y + h
    return (f"m {f(x + r)} {f(y)} l {f(x1 - r)} {f(y)} "
            f"b {f(x1 - r + k)} {f(y)} {f(x1)} {f(y + r - k)} {f(x1)} {f(y + r)} "
            f"l {f(x1)} {f(y1 - r)} "
            f"b {f(x1)} {f(y1 - r + k)} {f(x1 - r + k)} {f(y1)} {f(x1 - r)} {f(y1)} "
            f"l {f(x + r)} {f(y1)} "
            f"b {f(x + r - k)} {f(y1)} {f(x)} {f(y1 - r + k)} {f(x)} {f(y1 - r)} "
            f"l {f(x)} {f(y + r)} "
            f"b {f(x)} {f(y + r - k)} {f(x + r - k)} {f(y)} {f(x + r)} {f(y)}")


def _ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_mask(events: list[dict], path: Path, bnd: tuple, *, W: float, H: float) -> Path:
    """The mask .ass: white rounded rects on the band (PlayRes = the band in px of a
    W×H frame — libass rescales it to whatever size the band really is)."""
    bx, by, bw, bh = bnd
    pw, ph = max(2, round(bw * W)), max(2, round(bh * H))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {pw}", f"PlayResY: {ph}",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: M,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,"
        "1,0,0,7,0,0,0,1", "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for e in events:
        shapes = " ".join(
            rounded_rect_path((x - bx) * W, (y - by) * H, w * W, h * H, r * H)
            for x, y, w, h, r in e["rects"])
        lines.append(f"Dialogue: 0,{_ass_time(e['start'])},{_ass_time(e['end'])},M,,0,0,0,,"
                     f"{{\\an7\\pos(0,0)\\bord0\\shad0\\p1}}{shapes}{{\\p0}}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)


def filter_parts(src: str, bnd: tuple, mask: str, out: str, *, height: int = 1080,
                 tint: float = TINT, blur: float = BLUR_SIGMA) -> list[str]:
    """Filter-graph chains: ``src`` → glass over the band → ``out``. The mask comes
    from the frame itself (cropped, painted black, the .ass drawn on it) so it shares
    every timestamp with the picture — F-4 pauses and VFR sources included. The
    blur sigma follows the picture height (720p blurs as much as 1080p, relatively).
    ``tint`` / ``blur`` = :func:`look` of the style (white fraction, sigma at 1080)."""
    bx, by, bw, bh = bnd
    crop = f"crop=iw*{bw:.4f}:ih*{bh:.4f}:iw*{bx:.4f}:ih*{by:.4f}"
    # not rounded to whole px: 720p must blur as much as 1080p (14 → 9.3, not 9)
    sigma = max(0.5, round(float(blur) * height / 1080, 1))
    tint = min(1.0, max(0.0, float(tint)))
    return [
        f"{src}split=3[gmain][gblur][gmask]",
        f"[gblur]{crop},gblur=sigma={sigma:g}:steps=2,eq=saturation={SATURATION},"
        f"drawbox=c=white@{tint:g}:t=fill[gtint]",
        # rgb → gray lands white at 235 (limited range): stretch to a full 255 alpha,
        # else 8% of the sharp Chinese would show through the glass
        f"[gmask]{crop},format=rgb24,drawbox=c=black:t=fill,ass={mask},format=gray,"
        f"lut=c0=val*255/235[galpha]",
        "[gtint][galpha]alphamerge[gglass]",
        f"[gmain][gglass]overlay=main_w*{bx:.4f}:main_h*{by:.4f}{out}",
    ]


def job_context(job) -> tuple[dict | None, bool, list[Cue]]:
    """(hardsub region, hardsubs present?, Chinese cues) for a job. Unknown hardsubs
    count as present — the user picked a style meant to cover them."""
    from app.pipeline.assemble import _load_region
    from app.pipeline.srt import parse_srt

    region = _load_region(job)
    try:
        md = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
        hardsubs = md.get("has_hardsubs") is not False
    except (OSError, ValueError, AttributeError):
        hardsubs = True
    try:
        cn = parse_srt(job.cn_srt) if job.cn_srt.exists() else []
    except Exception:  # noqa: BLE001 - the CN line widths are only a hint
        cn = []
    return region, hardsubs, cn
