"""Which OpenRouter model does each job (E-2, docs/plan/E-roadmap.md).

Every AI call names its ROLE; the OpenRouter provider resolves it here:
``MODEL_<ROLE>`` in .env (Settings → AI và mô hình) → the benchmarked default below.
The promo model (OPENROUTER_PROMO_*) only ever replaces the ``draft`` role.

Defaults from the 2026-10-13 benchmarks (the app's own prompts, real jobs: a sung
cover, an ASR'd talking vlog, an OCR'd vlog): a blind judge (z-ai/glm-5.3-prime) put
moonshotai/kimi-k3 on top (8.42 avg; best on speech, consistent xưng hô) with the rest
within 0.3; Qwen3.7 Plus rendered a name in Hán-Việt, V4 Pro is slow (53 s / 40 lines).
So: the strongest model where it fixes the final text (refine / brief / shorten), the
fastest solid one for the first draft, a different vendor for the QA reviewer. QA was
compared separately on the same 39 pairs: V4 Pro (reasoning off) found nothing,
Kimi / GLM-prime found 1 real omission, qwen3.8-flash 3 incl. a real meaning error.
Re-run backend/scripts/translation_bench.py when a new model ships and update this
table — never pick ids from memory.
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
    Role("refine", "model_refine", "moonshotai/kimi-k3", "Tinh chỉnh bản dịch",
         "Lượt sửa thứ hai: đúng nghĩa, xưng hô, vừa thời lượng. Điểm cao nhất khi chấm mù."),
    Role("proofread", "model_proofread", "deepseek/deepseek-v4.1-flash", "Sửa lỗi OCR",
         "Sửa chữ Trung đọc sai từ phụ đề cháy sẵn."),
    Role("brief", "model_brief", "moonshotai/kimi-k3", "Đọc ngữ cảnh video",
         "Viết brief: thể loại, nhân vật, xưng hô, tên riêng."),
    Role("terms", "model_terms", "deepseek/deepseek-v4.1-flash", "Trích thuật ngữ",
         "Gợi ý tên riêng / thương hiệu lặp lại."),
    Role("shorten", "model_shorten", "moonshotai/kimi-k3", "Rút gọn / dịch lại câu",
         "Rút gọn câu quá dài, phương án dịch lại trong editor."),
    Role("qa", "model_qa", "qwen/qwen3.8-flash", "Soát bản dịch",
         "Đọc lại cặp Trung–Việt, đánh dấu câu nghi sai. Khác hãng với nháp và tinh chỉnh."),
    Role("vision", "model_vision", "qwen/qwen3.7-flash", "Xem khung hình (tùy chọn)",
         "Chỉ dùng khi bật “AI xem khung hình” — gửi vài ảnh của video."),
)}


def explicit(role: str) -> str:
    """The user's own choice for this role ('' when they kept the default)."""
    r = ROLES[role]
    return (getattr(settings, r.attr, "") or "").strip()


def model_for(role: str) -> str:
    return explicit(role) or ROLES[role].default
