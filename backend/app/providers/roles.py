"""Which OpenRouter model does each job (E-2, docs/plan/E-roadmap.md).

Every AI call names its ROLE; the OpenRouter provider resolves it here:
``MODEL_<ROLE>`` in .env (Settings → AI và mô hình) → the chosen model package
(``MODEL_PRESET``, app/providers/presets.py) → the benchmarked default below.
The promo model (OPENROUTER_PROMO_*) only ever replaces the ``draft`` role.

Defaults = the "Cân bằng" package, from the 2026-10-27 cost pass (docs/plan/G-cost-log.md):
the app's own two-pass translate on 120 lines of a real 27:42 review, three blind judges
(qwen3.7-plus, glm-5.3, gemini-3.8-flash). GPT-6 Luna as the refiner scored highest
(8.86 vs Kimi K3 8.66) with the fewest lines too long for their dub slot, at ~1/12 of
Kimi's cost; DeepSeek V4.1 Flash 1017 was the most accurate (9.20) and writes a solid
brief for $0.0007. The 2026-10-13 benchmark had put Kimi K3 on top by 0.1–0.3 — within
the judges' noise — at 13.5 then 20 $/M output tokens. The draft stays the fastest solid
CN-native model and the QA reviewer a different vendor (qwen3.8-flash found 3 real issues
on 39 pairs vs 0–1 for V4 Pro / Kimi / GLM-prime). Re-run backend/scripts/cost_bench.py
when a new model ships and update this table — never pick ids from memory.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class Role:
    key: str
    attr: str          # settings attribute / .env MODEL_<KEY>
    default: str
    label: str         # Vietnamese, for Settings
    hint: str


ROLES: dict[str, Role] = {r.key: r for r in (
    Role("draft", "model_draft", "deepseek/deepseek-v4.1-flash", "Dịch bản nháp",
         "Lượt dịch đầu: nhanh nhất (4–6 s / 40 câu); lượt tinh chỉnh sửa chỗ sót."),
    Role("refine", "model_refine", "openai/gpt-6-luna", "Tinh chỉnh bản dịch",
         "Lượt sửa thứ hai: đúng nghĩa, xưng hô, vừa thời lượng. 27/10: chấm mù cao nhất, "
         "ít câu quá dài nhất, rẻ ~12 lần so với Kimi K3."),
    Role("proofread", "model_proofread", "deepseek/deepseek-v4.1-flash", "Sửa lỗi OCR",
         "Sửa chữ Trung đọc sai từ phụ đề cháy sẵn."),
    Role("brief", "model_brief", "deepseek/deepseek-v4.1-flash-1017", "Đọc ngữ cảnh video",
         "Viết brief: thể loại, nhân vật, xưng hô, tên riêng. 1 lần gọi mỗi video."),
    Role("terms", "model_terms", "deepseek/deepseek-v4.1-flash", "Trích thuật ngữ",
         "Gợi ý tên riêng / thương hiệu lặp lại."),
    Role("shorten", "model_shorten", "openai/gpt-6-luna", "Rút gọn / dịch lại câu",
         "Rút gọn câu quá dài (trước cổng duyệt và trong editor), phương án “Dịch lại”."),
    Role("qa", "model_qa", "qwen/qwen3.8-flash", "Soát bản dịch",
         "Đọc lại cặp Trung–Việt, đánh dấu câu nghi sai. Khác hãng với nháp và tinh chỉnh."),
    Role("vision", "model_vision", "qwen/qwen3.7-flash", "Xem khung hình (tùy chọn)",
         "Chỉ dùng khi bật “AI xem khung hình” — gửi vài ảnh của video."),
    Role("ocr", "model_ocr", "qwen/qwen3.7-flash", "Đọc phụ đề cháy (OCR qua mạng)",
         "Chỉ dùng khi chọn “AI đọc chữ qua mạng”. 2026-10-14, 441 khung: flash sai 4, "
         "qwen/qwen3.7-plus sai 0 (đắt ~10×), OCR trên máy sai ~31."),
)}


def explicit(role: str) -> str:
    """The user's own choice for this role ('' when they kept the default)."""
    r = ROLES[role]
    return (getattr(settings, r.attr, "") or "").strip()


def default_for(role: str) -> str:
    """The role default: the downloaded model catalog's (app/catalog.py), else the table."""
    from app import catalog

    return catalog.role_default(role) or ROLES[role].default


def package_default(role: str) -> str:
    """What this role uses when the user did not pick a model for it: the chosen
    package's model, else the role default."""
    from app.providers import presets

    return presets.active().overrides.get(role) or default_for(role)


def model_for(role: str) -> str:
    return explicit(role) or package_default(role)
