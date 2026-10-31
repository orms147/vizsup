"""Model packages ("gói") — one choice in Settings that sets every text role at once.

Measured 2026-10-27 (docs/plan/G-cost-log.md, "Cost pass"): the app's own translate pass on
the first 120 lines of the 27:42 tech review (job 703e11aadf05), three blind judges. The
cheap refiners scored as well as Kimi K3 (3-judge mean: luna 8.86, deepseek-1017 8.84,
kimi 8.66) and fit the dub slot better, at 1/10 of the price; Kimi's brief is the richest
(term table + ASR-error notes), so the top package keeps it for that ONE call per video.

How a role's model is chosen (``roles.model_for``): the user's own ``MODEL_<ROLE>`` →
the chosen package's model for that role → ``roles.ROLES[role].default``. The default
package ("balanced") adds nothing on top of the role defaults, so ROLES stays the single
table of defaults. Vision / OCR are opt-in features with their own benchmark and are not
part of any package.

Prices: https://openrouter.ai/api/v1/models, 2026-10-27, $/M tokens in/out —
gpt-6-luna 0.10/0.50 · deepseek-v4.1-flash 0.14/0.42 · deepseek-v4.1-flash-1017 0.30/1.20 ·
kimi-k3 0.20/20.00 · qwen3.8-flash 0.15/0.47. Re-measure before changing a package.
2026-10-31: deepseek-v4.1-flash 0.045/0.50, -1017 0.15/0.60 (×2 on weekdays 01–04 and
06–10 UTC), luna / qwen unchanged; the DeepSeek roles are now served by the cheapest
non-4-bit host (~0.022/0.19, providers/translation/openrouter.py ROUTE_PRICE), so the
usd_per_min figures below are upper bounds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings

LUNA = "openai/gpt-6-luna"
DS_FLASH = "deepseek/deepseek-v4.1-flash"
DS_1017 = "deepseek/deepseek-v4.1-flash-1017"
KIMI = "moonshotai/kimi-k3"

# the text roles a package sets (roles.ROLES also has vision / ocr — opt-in, separate)
TEXT_ROLES = ("draft", "refine", "proofread", "brief", "terms", "shorten", "qa")
DEFAULT = "balanced"
CUSTOM = "custom"           # shown when the user picked a model for any single role


@dataclass(frozen=True)
class Preset:
    key: str
    label: str              # Vietnamese, for Settings
    hint: str
    overrides: dict[str, str] = field(default_factory=dict)   # on top of roles.ROLES defaults
    # measured, to the edit gate, cloud ASR + AI review, talking vlog (~27 lines/min):
    usd_per_min: float = 0.0
    usd_per_video: float = 0.0   # the context brief (one call per video)

    def cost_label(self) -> str:
        thirty = self.usd_per_min * 30 + self.usd_per_video
        return f"≈ ${self.usd_per_min:.4f}/phút video · video 30 phút ≈ ${thirty:.2f}"


PRESETS: dict[str, Preset] = {p.key: p for p in (
    Preset("economy", "Tiết kiệm",
           "Một model rẻ (GPT-6 Luna) cho nháp, tinh chỉnh, rút gọn; brief bằng DeepSeek Flash. "
           "Chấm mù 8.83/10. Tắt “Tinh chỉnh” ở màn Bắt đầu để rẻ thêm (dịch 1 lượt: 8.80).",
           {"draft": LUNA, "refine": LUNA, "shorten": LUNA, "brief": DS_FLASH},
           usd_per_min=0.0014, usd_per_video=0.0005),
    Preset("balanced", "Cân bằng (khuyên dùng)",
           "Nháp DeepSeek V4.1 Flash, tinh chỉnh + rút gọn GPT-6 Luna, brief DeepSeek 1017. "
           "Điểm chấm mù cao nhất (8.86/10), câu vừa khe lồng tiếng nhất (ít câu quá dài nhất).",
           {}, usd_per_min=0.0016, usd_per_video=0.0007),
    Preset("quality", "Chất lượng cao",
           "Tinh chỉnh + rút gọn DeepSeek V4.1 Flash 1017 (đúng nghĩa nhất khi chấm mù: 9.20), "
           "brief bằng Kimi K3 (đủ thuật ngữ nhất — 1 lần gọi mỗi video).",
           {"refine": DS_1017, "shorten": DS_1017, "brief": KIMI},
           usd_per_min=0.0018, usd_per_video=0.013),
    Preset("kimi", "Kimi K3 (như trước 27/10)",
           "Cấu hình cũ: Kimi K3 cho tinh chỉnh, brief, rút gọn. Đắt gấp ~6 lần mà chấm mù "
           "không hơn (8.66/10) — giữ để so sánh.",
           {"refine": KIMI, "brief": KIMI, "shorten": KIMI},
           usd_per_min=0.0099, usd_per_video=0.013),
)}


def effective(key: str) -> Preset:
    """Package ``key`` with the downloaded model catalog's changes (app/catalog.py)."""
    from dataclasses import replace

    from app import catalog

    p = PRESETS[key]
    patch = catalog.preset_patch(key)
    if not patch:
        return p
    return replace(p, overrides={**p.overrides, **(patch.get("overrides") or {})},
                   **{f: patch[f] for f in ("label", "hint", "usd_per_min", "usd_per_video")
                      if f in patch})


def active() -> Preset:
    """The package in settings (``MODEL_PRESET``); blank or unknown → the default."""
    key = (getattr(settings, "model_preset", "") or "").strip()
    return effective(key if key in PRESETS else DEFAULT)


def models(key: str) -> dict[str, str]:
    """Every text role's model under package ``key`` (role defaults + its overrides)."""
    from app.providers.roles import default_for

    p = effective(key)
    return {r: p.overrides.get(r) or default_for(r) for r in TEXT_ROLES}


def is_custom() -> bool:
    """True when the user picked a model for any text role by hand."""
    from app.providers import roles

    return any(roles.explicit(r) for r in TEXT_ROLES)


def known_models() -> list[str]:
    """Every id a package or a role default uses — offered in the role lists even before
    the live model list is fetched."""
    from app.providers.roles import ROLES, default_for

    ids = {r.default for r in ROLES.values()} | {default_for(k) for k in ROLES}
    for key in PRESETS:
        ids.update(effective(key).overrides.values())
    return sorted(ids)
