"""Vertical short-form (TikTok / Reels / YouTube Shorts): where the subtitles go, and the series'
remembered "Short" settings.

Platform UI that covers the picture (top tabs, the bottom caption + username strip, the right
button column) is ``app.pipeline.safezones``. A subtitle burned at the usual 60/1080 margin sits
UNDER TikTok's caption strip on a vertical picture; :func:`place` lifts it above the strip and keeps
the wrap width clear of the button column. Pure; ``assemble.assemble(safe_caption=…)`` calls it.

Length limits (checked 2026-10-29 — the platforms' own help pages were not reachable by the search
tool, so these are third-party summaries that agree on the figures; limits differ by account /
region / rollout, and a limit is not a recommended length):
  * YouTube Shorts: up to 3 minutes since Oct 2024 (60 s before) —
    https://www.argil.ai/blog/how-long-can-youtube-shorts-be ,
    https://reap.video/blog/best-video-length-youtube-shorts-reels-tiktok
  * TikTok: 10 minutes for most accounts (60 min by upload for some) —
    https://learn.headliner.app/hc/en-us/articles/34164986965271-Social-Media-Video-Length-Limits-2025
  * Instagram Reels: 3 minutes (Reels over 3 min are not recommended to new audiences; the app
    records up to 20 min) — https://wearesculpt.com/blog/reel-length/
A cut-out Short of 30–60 s is inside every one of them.
"""
from __future__ import annotations

from pathlib import Path

from app.pipeline import safezones

LIMITS_S = {"tiktok": 600, "reels": 180, "shorts": 180}      # max seconds, see above
SHORT_MIN_S, SHORT_MAX_S = 30.0, 60.0                         # what "Cắt Short" proposes
GAP = 0.02                      # clear air between the subtitle and a covered strip (fraction)
ASS_H, ASS_W = 1080, 1920       # PlayRes the .ass layout is written in
LETTERBOX_SCALE = 1.3           # a 16:9 picture on a 9:16 phone is small: bigger text
LETTERBOX_BELOW = 0.6           # picture height / canvas height under which the picture is "small"
SIZE_MAX = 120                  # .ass font units (1080 canvas)

DEFAULT_SHORT = {"platform": "tiktok", "frame": "tiktok", "tiktok_fill": "blur",
                 "episode_card": {"on": True}, "canvas_text": True, "bar_subs": True}


def _canvas(frame: str, pic: tuple[int, int] | None, res: str) -> tuple[int, int] | None:
    from app.pipeline import assemble

    if frame == "tiktok":
        return assemble.tiktok_canvas(res)
    return pic


def place(style: dict | None, size: int, platform: str | None, src_size: tuple[int, int] | None,
          frame: str = "source", res: str = "source") -> tuple[dict | None, int]:
    """(style, size) with the subtitle moved above the platform's bottom strip and clear of its
    right button column, for a VERTICAL composition (a vertical source, or any source in the 9:16
    frame). A landscape render, an unknown platform or a landscape-only table entry changes
    nothing. ``size`` is in .ass units; a small letterboxed picture gets bigger text."""
    from app.pipeline import assemble

    plat = safezones.SAFE_ZONES.get(platform or "")
    if plat is None or not plat.portrait:
        return style, size
    pic = assemble.burn_size(src_size, frame, res) if src_size else None
    canvas = _canvas(frame, pic, res)
    vertical = frame == "tiktok" or bool(canvas and canvas[0] < canvas[1])
    if not vertical:
        return style, size
    if pic and canvas:
        pf = min(1.0, pic[1] / canvas[1])
    else:
        pf = 1.0                                           # unknown size: assume it fills
    pb = 0.5 + pf / 2                                      # where the picture's bottom edge is
    limit = 1.0 - plat.bottom - GAP                        # the text must end above this
    st = dict(style or {})
    base_v = int(st.get("margin_v", 60) or 60)
    lift = max(0.0, pb - limit) / pf * ASS_H               # in picture lines (the .ass unit)
    st["margin_v"] = max(base_v, int(round(lift)))
    side = int(round((plat.right + GAP) * ASS_W))          # keep the wrap clear of the buttons
    st["margin_l"] = max(int(st.get("margin_l", 40) or 40), side)
    st["margin_r"] = max(int(st.get("margin_r", 40) or 40), side)
    scale = LETTERBOX_SCALE if pf < LETTERBOX_BELOW else 1.0
    return st, min(SIZE_MAX, max(size, int(round(size * scale))))


def is_vertical_source(src_size: tuple[int, int] | None) -> bool:
    return bool(src_size and src_size[0] < src_size[1])


# --- the series' "Short" settings -----------------------------------------------------------

def settings_for(work_dir: Path) -> dict:
    """The Short settings for a project: its series' saved ones, else the defaults."""
    from app.pipeline import series

    lk = series.link_of(Path(work_dir))
    p = series.load(lk["slug"]) if lk else None
    saved = p.short if p is not None and p.short else {}
    return {**DEFAULT_SHORT, **saved}


def learn(work_dir: Path, opts: dict) -> bool:
    """A real render made with the Short preset becomes the series' Short settings (the latest
    one wins: the owner tunes it once and every next episode starts from there)."""
    if not opts.get("short") or opts.get("preview"):
        return False
    from app.pipeline import series

    lk = series.link_of(Path(work_dir))
    p = series.load(lk["slug"]) if lk else None
    if p is None:
        return False
    keep = ("platform", "frame", "tiktok_fill", "res", "preset", "episode_card", "cover_mode",
            "credit_overlay", "ai_label", "audio_mode", "orig_volume", "dub_volume", "voice", "rate",
            "watermark", "canvas_text", "bar_subs")
    p.short = {"platform": opts.get("safe_caption") or "tiktok",
               **{k: opts[k] for k in keep if k in opts and k != "platform"}}
    series.save(p)
    return True
