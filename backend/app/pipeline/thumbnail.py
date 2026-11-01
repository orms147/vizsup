"""Ảnh bìa tự động — a review-channel thumbnail (1280×720 JPEG) for every finished video.

- **The frame**: ~20 moments spread over 8–92 % of the RENDERED video (output.mp4 — the
  Chinese band is already covered; a joined video's own output.mp4), each moved to the
  nearest moment with no subtitle on screen (burned lines under the big title look busy).
  Every frame is scored small (320 px): sharpness (Laplacian variance), contrast,
  colourfulness, mid-tone exposure; near-black (fades), washed-out (flashes) and flat
  frames are skipped. Pure scoring functions (``frame_stats`` / ``score`` / ``usable``).
- **The picture** (Pillow — installed with the OCR engine, declared in pyproject; it
  measures the text so the title is fitted to the width, which ffmpeg's drawtext cannot):
  the frame cropped to 16:9 — a vertical frame instead sits whole in the middle of a
  blurred, darkened copy of itself (the app's 9:16 bars turned sideways: a 16:9 crop of a
  9:16 picture keeps only the middle third of its height — heads cut off, the covered
  Chinese band in the middle), a dark gradient at the bottom, the BIG title in Bangers
  (Be Vietnam Pro Bold when a letter is missing) — white, the last line yellow, a thick
  black outline — and the episode chip above it ("TẬP 9", "FULL 1–9").
- **The words**: the series' Vietnamese name (Tự động đăng plan, ``series_name``) + the
  episode range from the title (compilation.episode_range); else the video title without
  hashtags, episode marks and Chinese characters (the fonts have no CJK glyphs).
- **Whose it is**: ``thumb.json`` says how thumb.jpg was made. ``auto: true`` = this module
  on its own (a later render may replace it); anything the owner did in the "Ảnh bìa"
  dialog (a picked frame, an edited title, an own picture) is ``auto: false`` and the
  automatic pass never touches it again. YouTube gets thumb.jpg (autopilot.post_meta).

The render (``render.render_job``, ``o["thumbnail"]``, default on, never for the quick
proof) and the joins (``vizsup compile`` / ``join``, the app's Ghép video) call
``make_auto``; it only logs a warning when something fails.
"""
from __future__ import annotations

import functools
import io
import json
import math
import re
import subprocess
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app import fsutil

THUMB = "thumb.jpg"
INFO = "thumb.json"
SIZE = (1280, 720)
SAMPLES = 20                      # frames looked at
CHOICES = 6                       # candidates the dialog offers
SPAN = (0.08, 0.92)               # skip the intro chip and the "Còn tiếp" end card
SCORE_W = 320                     # frames are scored at this width (sharpness is comparable)
DARK, BRIGHT, FLAT = 38.0, 212.0, 18.0   # mean luma below / above, luma std below → skipped
MIN_COVER_W = 640                 # an image of our name this small is the old library cache
TITLE_FONTS = ("Bangers-Regular.ttf", "BeVietnamPro-Bold.ttf")
CHIP_FONT = "BeVietnamPro-Bold.ttf"
WHITE, YELLOW, RED = (255, 255, 255), (255, 222, 0), (226, 30, 42)


# --- scoring (pure) -----------------------------------------------------------------------

@dataclass(frozen=True)
class FrameStats:
    brightness: float      # mean luma, 0..255
    contrast: float        # luma standard deviation
    sharpness: float       # variance of the Laplacian of the luma
    colorfulness: float    # Hasler & Süsstrunk (2003)


def frame_stats(rgb: np.ndarray) -> FrameStats:
    """Stats of an H×W×3 uint8 frame. Pure."""
    f = np.asarray(rgb, dtype=np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    lap = (4 * y[1:-1, 1:-1] - y[:-2, 1:-1] - y[2:, 1:-1] - y[1:-1, :-2] - y[1:-1, 2:]) \
        if y.shape[0] > 2 and y.shape[1] > 2 else np.zeros(1, np.float32)
    rg, yb = r - g, 0.5 * (r + g) - b
    color = math.hypot(float(rg.std()), float(yb.std())) \
        + 0.3 * math.hypot(float(rg.mean()), float(yb.mean()))
    return FrameStats(float(y.mean()), float(y.std()), float(lap.var()), color)


def usable(s: FrameStats) -> bool:
    """Not a fade to black, a white flash or a flat (title card / blank) frame. Pure."""
    return DARK <= s.brightness <= BRIGHT and s.contrast >= FLAT


def score(s: FrameStats) -> float:
    """0..1, higher = a crisper, punchier frame. Sharpness weighs most (a blurred motion
    frame makes a poor cover); mid-tones read best under the dark title band. Pure."""
    sharp = min(1.0, math.log1p(max(0.0, s.sharpness)) / math.log1p(1500.0))
    contrast = min(1.0, s.contrast / 64.0)
    color = min(1.0, s.colorfulness / 90.0)
    exposure = max(0.0, 1.0 - abs(s.brightness - 120.0) / 120.0)
    return round(0.45 * sharp + 0.2 * contrast + 0.2 * color + 0.15 * exposure, 4)


def rank(stats: list[tuple[float, FrameStats]], k: int = CHOICES,
         min_gap: float = 0.0) -> list[tuple[float, float]]:
    """The ``k`` best (time, score), best first, at least ``min_gap`` s apart (two samples
    moved to the same pause between lines show the same picture). Only usable frames —
    unless none is (a dark video still gets a cover: then the best of all). Pure."""
    good = [(t, s) for t, s in stats if usable(s)] or list(stats)
    out: list[tuple[float, float]] = []
    for t, sc in sorted(((t, score(s)) for t, s in good), key=lambda x: (-x[1], x[0])):
        if all(abs(t - u) >= min_gap for u, _ in out):
            out.append((t, sc))
        if len(out) == k:
            break
    return out


def sample_times(duration: float, n: int = SAMPLES, busy: list[tuple[float, float]] = (),
                 *, span: tuple[float, float] = SPAN, reach: float = 2.5) -> list[float]:
    """``n`` moments evenly over ``span`` of the video, each moved to the nearest moment with
    no subtitle on screen (``busy`` = cue (start, end)) within ``reach`` s — kept where it
    is when there is none. Sorted, no duplicates. Pure."""
    if duration <= 0 or n <= 0:
        return []
    lo, hi = duration * span[0], duration * span[1]
    times = [(lo + hi) / 2] if n == 1 else [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    gaps, cur = [], 0.0
    for a, b in sorted((float(a), float(b)) for a, b in busy if b > a):
        if a > cur:
            gaps.append((cur, a))
        cur = max(cur, b)
    gaps.append((cur, duration))
    out = []
    for t in times:
        best = None
        for a, b in gaps:
            if b - a < 0.2:
                continue
            p = min(max(t, a + 0.1), b - 0.1)       # a breath inside the gap
            if abs(p - t) <= reach and (best is None or abs(p - t) < abs(best - t)):
                best = p
        out.append(round(best if best is not None else t, 2))
    return sorted(set(out))


# --- the words (pure, apart from reading the folder's own files) ---------------------------

_CJK = re.compile(r"[⺀-〿぀-ヿ㐀-䶿一-鿿豈-﫿"
                  r"＀-￯]+")
_MARKS = (     # episode marks — the chip shows the number (cf. compilation.episode_range)
    re.compile(r"第\s*[0-9零〇一二两三四五六七八九十百千]+\s*[集话話期回章节節部]"),
    re.compile(r"》\s*[:：]?\s*(?:[Ee][Pp]|[Pp])?\s*\d{1,4}(?:\s*[-–~～至到]\s*\d{1,4})?"),
    re.compile(r"(?i)\b(?:tập|tap|ep|episode|part|full)\s*[.#:]?\s*\d{1,4}"
               r"(?:\s*[-–~]\s*\d{1,4})?(?!\d)"),
    re.compile(r"^\s*\d{1,4}\s*(?:[.、:：\-_)）\]|]|\s|$)"),
)


def clean_title(raw: str | None, limit: int = 48) -> str:
    """A video title fit for the cover: no hashtags / @mentions, episode marks (the chip
    shows them) or Chinese characters (no CJK glyph in the fonts). "" when nothing
    readable is left. Pure."""
    t = unicodedata.normalize("NFKC", raw or "")
    t = re.sub(r"[#＃@][^\s#＃@]+", " ", t)
    for pat in _MARKS:
        t = pat.sub(" ", t)
    t = _CJK.sub(" ", t)
    t = re.sub(r"[《》【】「」\[\]()|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–|·:,.!?")
    if sum(ch.isalpha() for ch in t) < 2:
        return ""
    t = unicodedata.normalize("NFC", t)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def chip_text(first: int | None, last: int | None = None, *, joined: bool = False) -> str:
    """"TẬP 9" · "TẬP 1–5" (one post holding five) · "FULL 1–9" (a joined video). Pure."""
    if first is None:
        return ""
    last = last if last is not None and last > first else first
    if last == first:
        return f"TẬP {first}"
    return f"{'FULL' if joined else 'TẬP'} {first}–{last}"


def _series_name(work_dir: Path) -> str:
    """The series' Vietnamese name from its Tự động đăng plan, "" when not set."""
    from app.pipeline import autopilot, series

    try:
        lk = series.link_of(work_dir)
        return (autopilot.load_plan(lk["slug"]).get("series_name") or "").strip() if lk else ""
    except Exception:  # noqa: BLE001 - a label: the title is fine
        return ""


def default_text(work_dir: Path) -> tuple[str, str]:
    """(title, chip) for a project folder or a joined video's folder."""
    from app.pipeline import compilation

    d = Path(work_dir)
    comp = compilation._read_json(d / compilation.COMP_FILE)
    if comp:
        nums = sorted(set(compilation._numbers(comp.get("episodes") or [])))
        chip = chip_text(nums[0], nums[-1], joined=True) if nums else ""
        name = next((n for n in (_series_name(d.parent / str(e.get("id") or ""))
                                 for e in comp.get("episodes") or [] if e.get("id")) if n), "")
        return name or clean_title(comp.get("title")), chip
    raw = compilation._job_title(d)
    r = compilation.episode_range(raw)
    chip = chip_text(*r) if r else ""
    hook = (compilation._read_json(d / "metadata.json").get("short") or {}).get("hook") or ""
    return _series_name(d) or clean_title(raw) or clean_title(hook), chip


def busy_spans(work_dir: Path) -> list[tuple[float, float]]:
    """(start, end) of every subtitle as rendered (output.srt, else vi.srt)."""
    from app.pipeline import compilation
    from app.pipeline.srt import parse_srt

    p = compilation._rendered_srt(Path(work_dir))
    try:
        return [(c.start, c.end) for c in parse_srt(p) if (c.text or "").strip()] if p else []
    except Exception:  # noqa: BLE001 - only a preference
        return []


# --- frames --------------------------------------------------------------------------------

def _scaled(size: tuple[int, int] | None, width: int) -> tuple[int, int]:
    w, h = size or (16, 9)
    return width, max(2, int(round(width * h / max(1, w) / 2)) * 2)


def read_frame(video: Path, t: float, size: tuple[int, int]) -> np.ndarray | None:
    """One frame at ``t`` s as an H×W×3 uint8 array at ``size`` (w, h), or None."""
    from app.ffmpegutil import ffmpeg_bin

    w, h = size
    cmd = [ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
           "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=bicubic,setsar=1",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    need = w * h * 3
    if r.returncode != 0 or len(r.stdout) < need:
        return None
    return np.frombuffer(r.stdout[:need], dtype=np.uint8).reshape(h, w, 3)


@dataclass
class Candidate:
    t: float
    score: float
    frame: np.ndarray          # small (SCORE_W wide) — the dialog's picture


def candidates(video: Path, *, busy: list[tuple[float, float]] = (), samples: int = SAMPLES,
               k: int = CHOICES, cancel: Callable[[], bool] | None = None) -> list[Candidate]:
    """The ``k`` best frames of ``video``, best first (one ffmpeg seek per sample)."""
    from app.pipeline.assemble import probe_duration, probe_size

    small = _scaled(probe_size(video), SCORE_W)
    duration = probe_duration(video)
    frames: dict[float, np.ndarray] = {}
    stats: list[tuple[float, FrameStats]] = []
    for t in sample_times(duration, samples, busy):
        if cancel is not None and cancel():
            raise InterruptedError("Đã hủy.")
        f = read_frame(video, t, small)
        if f is not None:
            frames[t] = f
            stats.append((t, frame_stats(f)))
    spacing = duration * (SPAN[1] - SPAN[0]) / max(1, samples - 1)
    return [Candidate(t, s, frames[t]) for t, s in rank(stats, k, min_gap=spacing / 2)]


# --- the picture (Pillow) ------------------------------------------------------------------

def _font_path(name: str) -> Path:
    from app.pipeline.assemble import _BUNDLED_FONTS

    return _BUNDLED_FONTS / name


@functools.lru_cache(maxsize=48)
def _font(name: str, px: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(_font_path(name)), px)


def _covers(name: str, text: str) -> bool:
    """Every letter of ``text`` has a glyph in the font (Pillow draws .notdef boxes)."""
    f = _font(name, 40)
    missing = f.getmask("￿")
    blank = (missing.size, bytes(missing))
    for ch in set(text) - set("  "):
        m = f.getmask(ch)
        if (m.size, bytes(m)) == blank:
            return False
    return True


def title_font(text: str) -> str:
    return next((n for n in TITLE_FONTS if _covers(n, text)), TITLE_FONTS[-1])


def fit_16x9(img, size: tuple[int, int] = SIZE):
    """``img`` (PIL) as a ``size`` picture: cropped to 16:9 around the centre; a vertical or
    square picture instead sits whole between blurred, darkened copies of itself."""
    from PIL import Image, ImageEnhance, ImageFilter

    W, H = size
    img = img.convert("RGB")
    w, h = img.size

    def cover(im):
        s = max(W / im.width, H / im.height)
        im = im.resize((max(W, round(im.width * s)), max(H, round(im.height * s))),
                       Image.LANCZOS)
        x, y = (im.width - W) // 2, (im.height - H) // 2
        return im.crop((x, y, x + W, y + H))

    if w / max(1, h) >= 1.4:
        return cover(img)
    bg = cover(img.resize((max(1, w // 4), max(1, h // 4)), Image.BILINEAR))
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(10))).enhance(0.62)
    fw = max(1, round(w * H / h))
    bg.paste(img.resize((fw, H), Image.LANCZOS), ((W - fw) // 2, 0))
    return bg


def wrap(words: list[str], measure: Callable[[str], float], max_w: float,
         max_lines: int) -> list[str] | None:
    """Greedy word wrap; None when it needs more than ``max_lines`` lines or one word alone
    is wider than ``max_w``. Pure."""
    lines: list[str] = []
    for w in words:
        if measure(w) > max_w:
            return None
        if lines and measure(lines[-1] + " " + w) <= max_w:
            lines[-1] += " " + w
        else:
            lines.append(w)
    return lines if len(lines) <= max_lines else None


def layout_title(text: str, measure: Callable[[str, int], float], *, max_w: float,
                 sizes: tuple[int, ...] = tuple(range(150, 59, -6)),
                 one_line_min: int = 96) -> tuple[int, list[str]]:
    """Size + lines of the title: one line while that stays big (≥ ``one_line_min`` px — no
    lone word on a second line), else the biggest size it fits at in two lines split where
    they are most even, else three lines at the smallest; words cut with "…" when even that
    fails. Pure (``measure(text, px)`` = width)."""
    words = text.split()
    if not words:
        return sizes[-1], []
    line = " ".join(words)
    for px in sizes:
        if px < one_line_min and len(words) > 1:
            break
        if measure(line, px) <= max_w:
            return px, [line]
    for px in sizes:
        best: tuple[float, list[str]] | None = None
        for i in range(1, len(words)):
            pair = [" ".join(words[:i]), " ".join(words[i:])]
            w = max(measure(s, px) for s in pair)
            if w <= max_w and (best is None or w < best[0]):
                best = (w, pair)
        if best:
            return px, best[1]
    px = sizes[-1]
    lines = wrap(words, lambda s: measure(s, px), max_w, 3)
    if lines:
        return px, lines
    out, line = [], ""
    for w in words:                          # last resort: fill 3 lines, then "…"
        cand = (line + " " + w).strip()
        if measure(cand, px) <= max_w:
            line = cand
            continue
        out.append(line or w)
        line = w if line else ""
        if len(out) == 3:
            break
    if line and len(out) < 3:
        out.append(line)
    while out and measure(out[-1] + "…", px) > max_w and len(out[-1]) > 1:
        out[-1] = out[-1][:-1]
    out[-1] = out[-1].rstrip() + "…"
    return px, out[:3]


def _stroke(px: int) -> int:
    """The black outline: thick, like the review channels' covers."""
    return max(4, px // 11)


def _bare(text: str) -> str:
    """The letters without their accents ("ĐẸP" → "ĐEP"). Pure."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def compose(frame, dest: Path, *, title: str = "", chip: str = "") -> Path:
    """Write the cover: ``frame`` (H×W×3 array or PIL image) at 1280×720, the dark bottom
    gradient, the title (upper case, ≤ 2 lines) and the chip above it. JPEG, atomic."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    W, H = SIZE
    img = frame if isinstance(frame, Image.Image) else Image.fromarray(np.asarray(frame))
    base = fit_16x9(img)
    base = ImageEnhance.Contrast(ImageEnhance.Color(base).enhance(1.15)).enhance(1.06)
    # the bottom 55 % darkens towards the edge — the title reads on any frame
    y = np.linspace(0.0, 1.0, H, dtype=np.float32)
    a = np.clip((y - 0.45) / 0.55, 0.0, 1.0) ** 1.4 * 0.9
    shade = Image.fromarray((np.repeat(a[:, None], W, axis=1) * 255).astype(np.uint8), "L")
    base = Image.composite(Image.new("RGB", SIZE, (0, 0, 0)), base, shade)

    text = " ".join(unicodedata.normalize("NFC", title or "").upper().split())
    chip = " ".join(unicodedata.normalize("NFC", chip or "").upper().split())
    margin, bottom, right = 56, 46, 150         # bottom right: YouTube's duration badge
    lines, px, fname = [], 0, title_font(text) if text else TITLE_FONTS[0]
    if text:
        def measure(s: str, size: int) -> float:
            return _font(fname, size).getlength(s) + 2 * _stroke(size)
        px, lines = layout_title(text, measure, max_w=W - margin - right)
    layer = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    shadow = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    draw, sdraw = ImageDraw.Draw(layer), ImageDraw.Draw(shadow)
    cur = H - bottom                              # the bottom of what is drawn next
    if lines:
        f, stroke = _font(fname, px), _stroke(px)
        boxes = [f.getbbox(s, stroke_width=stroke) for s in lines]
        # one step for every line, the smallest that keeps a line's stacked accents (Ầ, Ễ)
        # clear of the letters above (their dot below — Ẹ — seldom sits right over one)
        feet = [f.getbbox(_bare(s), stroke_width=stroke)[3] for s in lines]
        step = max([int(sum(f.getmetrics()) * 0.8)]
                   + [feet[i] - boxes[i + 1][1] + px // 25 for i in range(len(lines) - 1)])
        # the last line's ink ends ``bottom`` px above the edge; the others stack above it
        y_last = cur - boxes[-1][3]
        ys = [y_last - step * (len(lines) - 1 - i) for i in range(len(lines))]
        for i, (line, yy) in enumerate(zip(lines, ys, strict=True)):
            color = YELLOW if i == len(lines) - 1 else WHITE
            sdraw.text((margin + 7, yy + 9), line, font=f, fill=(0, 0, 0, 170),
                       stroke_width=stroke + 3, stroke_fill=(0, 0, 0, 170))
            draw.text((margin, yy), line, font=f, fill=color, stroke_width=stroke,
                      stroke_fill=(0, 0, 0))
        cur = ys[0] + boxes[0][1] - 10            # the chip right above the title's ink
    if chip:
        cpx = 46 if lines else 72
        f = _font(CHIP_FONT, cpx)
        x0, y0, x1, y1 = draw.textbbox((0, 0), chip, font=f)
        pad_x, pad_y = int(cpx * 0.45), int(cpx * 0.28)
        box = (margin, cur - (y1 - y0) - 2 * pad_y, margin + (x1 - x0) + 2 * pad_x, cur)
        sdraw.rounded_rectangle((box[0] + 5, box[1] + 7, box[2] + 5, box[3] + 7),
                                radius=cpx // 4, fill=(0, 0, 0, 140))
        draw.rounded_rectangle(box, radius=cpx // 4, fill=RED, outline=(0, 0, 0), width=3)
        draw.text((box[0] + pad_x - x0, box[1] + pad_y - y0), chip, font=f, fill=WHITE)
    base = base.convert("RGBA")
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(7)))
    base.alpha_composite(layer)
    return _save_jpeg(base, dest)


def _save_jpeg(img, dest: Path) -> Path:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True, progressive=True)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fsutil.atomic_write_bytes(dest, buf.getvalue())     # well under YouTube's 2 MB
    return dest


# --- whose thumbnail ----------------------------------------------------------------------

def load_info(work_dir: Path) -> dict:
    try:
        d = json.loads((Path(work_dir) / INFO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_info(work_dir: Path, info: dict) -> None:
    fsutil.atomic_write_text(Path(work_dir) / INFO,
                             json.dumps({**info, "made": int(time.time())},
                                        ensure_ascii=False, indent=1))


def _width(path: Path) -> int:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return int(im.size[0])
    except Exception:  # noqa: BLE001 - unreadable: not a cover
        return 0


def user_chosen(work_dir: Path) -> bool:
    """thumb.jpg is the owner's choice: the dialog marked it so — or, with no thumb.json, a
    full-size picture put there by hand (the library's old 120-px cache is not)."""
    d = Path(work_dir)
    if not (d / THUMB).exists():
        return False
    info = load_info(d)
    if info:
        return not info.get("auto", True)
    return _width(d / THUMB) >= MIN_COVER_W


def cover(work_dir: Path) -> Path | None:
    """thumb.jpg when it is a cover (made here, or a full-size picture) — what a post sends."""
    p = Path(work_dir) / THUMB
    if not p.exists():
        return None
    return p if load_info(work_dir) or _width(p) >= MIN_COVER_W else None


def wanted(o: dict) -> bool:
    """A render with these opts makes the cover: on unless turned off, and a real render
    (never the quick 640p proof). Pure."""
    return bool(o.get("thumbnail", True)) and not o.get("preview")


def make(work_dir: Path, *, t: float | None = None, title: str | None = None,
         chip: str | None = None, auto: bool = True,
         cancel: Callable[[], bool] | None = None) -> Path:
    """Compose ``work_dir``/thumb.jpg from its output.mp4 at ``t`` (None = the best frame),
    with ``title`` / ``chip`` (None = the defaults), and record how (thumb.json)."""
    from app.pipeline.assemble import probe_size

    d = Path(work_dir)
    video = d / "output.mp4"
    if not video.exists():
        raise FileNotFoundError("Chưa có video dựng xong (output.mp4).")
    base_title, base_chip = default_text(d)
    cands: list[float] = load_info(d).get("candidates") or []
    if t is None:
        found = candidates(video, busy=busy_spans(d), cancel=cancel)
        if not found:
            raise RuntimeError("Không lấy được khung hình nào từ video.")
        t, cands = found[0].t, [c.t for c in found]
    size = probe_size(video) or SIZE
    frame = read_frame(video, t, size if size[0] <= 1920 else _scaled(size, 1920))
    if frame is None:
        raise RuntimeError(f"Không lấy được khung hình ở giây {t:.1f}.")
    title = base_title if title is None else title
    chip = base_chip if chip is None else chip
    out = compose(frame, d / THUMB, title=title, chip=chip)
    _save_info(d, {"auto": auto, "source": "frame", "time": round(float(t), 2),
                   "title": title, "chip": chip, "candidates": cands})
    return out


def use_image(work_dir: Path, image: Path) -> Path:
    """The owner's own picture becomes thumb.jpg — fitted to 1280×720 as it is, no text on
    it (a cover made elsewhere is finished) — and is never replaced automatically."""
    from PIL import Image

    d = Path(work_dir)
    with Image.open(image) as im:
        out = _save_jpeg(fit_16x9(im), d / THUMB)
    _save_info(d, {"auto": False, "source": "image", "image": Path(image).name})
    return out


def make_auto(work_dir: Path, *, log: Callable[[str], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> Path | None:
    """After a render / a join: thumb.jpg unless the owner chose one. Never raises — a
    failure is a warning in the log (the video itself is done)."""
    say = log or (lambda _m: None)
    d = Path(work_dir)
    if user_chosen(d):
        say("ℹ Giữ ảnh bìa bạn đã chọn (thumb.jpg).")
        return None
    try:
        out = make(d, cancel=cancel)
    except InterruptedError:             # Hủy while looking for frames: the video stays done
        say("Bỏ qua ảnh bìa (đã hủy).")
        return None
    except Exception as exc:  # noqa: BLE001 - the cover is a bonus, the render is done
        say(f"⚠ Chưa tạo được ảnh bìa: {exc}")
        return None
    say(f"✓ Ảnh bìa 1280×720: {out.name} (đổi ở nút Ảnh bìa)")
    return out
