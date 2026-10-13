"""Which OpenRouter model does each job (E-2, docs/plan/E-roadmap.md).

Every AI call names its ROLE; the OpenRouter provider resolves it here:
``MODEL_<ROLE>`` in .env (Settings → AI và mô hình) → the benchmarked default below.
The promo model (OPENROUTER_PROMO_*) only ever replaces the ``draft`` role.

Defaults come from the 2026-10-13 benchmark (8 Chinese models, the app's own prompts,
two real jobs): DeepSeek V4 Pro and Qwen3.7 Plus translate best; V4.1 Flash (the old
single default) made meaning errors. Re-run backend/scripts/translation_bench.py when a
new model ships and update this table — never pick ids from memory.
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
    Role("draft", "model_draft", "qwen/qwen3.7-plus", "Dịch bản nháp",
         "Lượt dịch đầu, đọc tiếng Trung. ≈ ngang V4 Pro, nhanh gấp đôi."),
    Role("refine", "model_refine", "deepseek/deepseek-v4-pro-0813", "Tinh chỉnh bản dịch",
         "Lượt sửa thứ hai: đúng nghĩa, xưng hô, vừa thời lượng. Tốt nhất khi thử."),
    Role("proofread", "model_proofread", "qwen/qwen3.7-plus", "Sửa lỗi OCR",
         "Sửa chữ Trung đọc sai từ phụ đề cháy sẵn."),
    Role("brief", "model_brief", "qwen/qwen3.7-plus", "Đọc ngữ cảnh video",
         "Viết brief: thể loại, nhân vật, xưng hô, tên riêng."),
    Role("terms", "model_terms", "qwen/qwen3.7-plus", "Trích thuật ngữ",
         "Gợi ý tên riêng / thương hiệu lặp lại."),
    Role("shorten", "model_shorten", "qwen/qwen3.7-plus", "Rút gọn / dịch lại câu",
         "Rút gọn câu quá dài, phương án dịch lại trong editor."),
    Role("qa", "model_qa", "deepseek/deepseek-v4-pro-0813", "Soát bản dịch",
         "Đọc lại cặp Trung–Việt, đánh dấu câu nghi sai để bạn xem."),
    Role("vision", "model_vision", "qwen/qwen3.7-flash", "Xem khung hình (tùy chọn)",
         "Chỉ dùng khi bật “AI xem khung hình” — gửi vài ảnh của video."),
)}


def explicit(role: str) -> str:
    """The user's own choice for this role ('' when they kept the default)."""
    r = ROLES[role]
    return (getattr(settings, r.attr, "") or "").strip()


def model_for(role: str) -> str:
    return explicit(role) or ROLES[role].default
