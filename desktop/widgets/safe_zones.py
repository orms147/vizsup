"""Platform safe zones: the strips of a frame the app UI of TikTok / Reels / Shorts / YouTube
covers (top tabs, bottom caption + username, the right-hand button column). The video panel
draws them translucent on the picture, and warns when the subtitle box sits in one.

All numbers live in ``SAFE_ZONES`` — fractions of the frame, one place to retune.

Sources (checked 2026-10-29; the platforms publish no pixel-exact organic spec, so these
are the measured, conservative-but-not-extreme values of third-party guides):
  * TikTok ad template via Socialync: 1080×1920, keep text out of the top 240 px (12.5 %),
    bottom 660 px (34 %) and 120 px on each side; the button column is ~180 px wide, from
    y = 840 px down.  https://www.socialync.io/tools/safe-zones/tiktok
  * Meta's Reels guidance ≈ top 14 %, bottom 35 %, sides 6 %; Google's Shorts guidance
    ≈ top 15 %, bottom 35 %, right 17.8 %  (summaries: https://www.upload-post.com/tools/safe-zone-checker/,
    https://www.clipspeed.ai/tools/social-media-video-dimensions.html)
  * Organic TikTok is tighter than the ad templates: top 120–200 px, bottom 250–370 px,
    right 120–164 px  (https://argil.ai/blog/tiktok-aspect-ratio-cec4c).
A subtitle is not an ad, so the table follows the ORGANIC figures (a subtitle in the ad
bottom 35 % would be pushed up to the middle of the picture); the ad numbers are the
reason the bottom strip is the tallest one. TikTok and Reels change their layout with the
caption length — treat the strips as "likely covered", not exact.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    name: str                 # "top" | "bottom" | "right"
    x0: float
    y0: float
    x1: float
    y1: float
    what: str                 # what covers it, for the hint ("nút", "mô tả", …)


@dataclass(frozen=True)
class Platform:
    key: str
    label: str                # menu / hint name
    portrait: bool
    top: float = 0.0          # fraction of the frame height
    bottom: float = 0.0
    right: float = 0.0        # fraction of the frame width …
    right_from: float = 0.0   # … from this fraction of the height down
    top_what: str = "thanh tab"
    bottom_what: str = "tên và mô tả"
    right_what: str = "nút"


SAFE_ZONES: dict[str, Platform] = {
    "tiktok": Platform("tiktok", "TikTok", True, top=0.08, bottom=0.22, right=0.14,
                       right_from=0.45),
    "reels": Platform("reels", "Reels", True, top=0.10, bottom=0.22, right=0.12,
                      right_from=0.50),
    "shorts": Platform("shorts", "YouTube Shorts", True, top=0.08, bottom=0.20, right=0.15,
                       right_from=0.50),
    "youtube": Platform("youtube", "YouTube", False, bottom=0.10, bottom_what="thanh điều khiển"),
}
ORDER = ("tiktok", "reels", "shorts", "youtube")


def zones(key: str | None) -> list[Zone]:
    """The covered strips of platform ``key`` as fractions of the frame; [] for none /
    unknown."""
    p = SAFE_ZONES.get(key or "")
    if p is None:
        return []
    out: list[Zone] = []
    if p.top > 0:
        out.append(Zone("top", 0.0, 0.0, 1.0, p.top, p.top_what))
    if p.bottom > 0:
        out.append(Zone("bottom", 0.0, 1.0 - p.bottom, 1.0, 1.0, p.bottom_what))
    if p.right > 0:
        # the button column stops above the bottom strip (they overlap there anyway)
        out.append(Zone("right", 1.0 - p.right, p.right_from, 1.0, 1.0 - p.bottom, p.right_what))
    return out


def hint_for(key: str | None, box: tuple[float, float, float, float]) -> str:
    """"Phụ đề nằm dưới nút của TikTok" when the subtitle box (x0, y0, x1, y1 as fractions
    of the frame) intersects a covered strip; "" when it is clear. The most specific strip
    wins: buttons, then the bottom caption area, then the top tabs."""
    p = SAFE_ZONES.get(key or "")
    if p is None:
        return ""
    bx0, by0, bx1, by1 = box
    hit: dict[str, Zone] = {}
    for z in zones(key):
        if bx0 < z.x1 and bx1 > z.x0 and by0 < z.y1 and by1 > z.y0:
            hit[z.name] = z
    for name in ("right", "bottom", "top"):
        if name in hit:
            return f"Phụ đề nằm dưới {hit[name].what} của {p.label}"
    return ""
