"""Prompt caching (C2, 2026-10-30): DeepSeek / OpenAI cache the longest identical PREFIX of a
request. Everything that is constant for one video (rules, user style, brief, series sheet,
previous-episode lines, video meta, glossary) must come BEFORE the numbered lines, and nothing
that differs per batch may sit in front of them. Measured on ep 7 (88 lines, real calls): an
identical second run served 41 % of the input tokens from cache and cost 9.5 % less — the
dollars are tiny (a whole episode's translate+refine is ≈ $0.003) but a regression that put the
batch's own lines first would silently throw that away."""
from __future__ import annotations

import os

from app.providers.translation._common import (
    SYSTEM_PROMPT,
    build_refine_prompt,
    build_user_prompt,
    system_prompt_for,
)

CTX = {"title": "第5集 | 《末日校车》07", "platform": "douyin", "_brief": "Phim sinh tồn. Xưng hô: anh Cố.",
       "_series": "Nhân vật:\n- 顾哥 → anh Cố", "_prev": ["Anh ở lại nhé.", "Được."], "_style": "Ngắn gọn."}
GLOSS = {"顾哥": "anh Cố", "丧尸": "zombie"}


def _lcp(a: str, b: str) -> int:
    return len(os.path.commonprefix([a, b]))


def test_system_prompt_is_identical_for_every_batch_of_a_video():
    # per-batch data lives in the user turn only; the budgets key never reaches the system prompt
    a = system_prompt_for(SYSTEM_PROMPT, {**CTX, "_budgets": [None, 12]})
    b = system_prompt_for(SYSTEM_PROMPT, {**CTX, "_budgets": [7, None, None]})
    assert a == b


def test_draft_user_turn_shares_everything_up_to_the_lines():
    one = build_user_prompt(["你好", "再见"], context=CTX, glossary=GLOSS)
    two = build_user_prompt(["谢谢", "不客气", "走吧"], context=CTX, glossary=GLOSS)
    head = one.index("1| 你好")
    assert one[:head] == two[:head]                    # meta, glossary, instruction: byte-identical
    assert head <= _lcp(one, two) <= head + 4           # …and it ends where the lines begin ("1| " is shared)


def test_refine_user_turn_shares_everything_up_to_the_lines():
    one = build_refine_prompt(["你好"], ["Xin chào"], context={**CTX, "_budgets": [None]}, glossary=GLOSS)
    two = build_refine_prompt(["谢谢", "走吧"], ["Cảm ơn", "Đi thôi"],
                              context={**CTX, "_budgets": [9, None]}, glossary=GLOSS)
    head = one.index("[1] 中文")
    assert one[:head] == two[:head]
    assert head <= _lcp(one, two) <= head + 12          # "[1] 中文: " is shared too


def test_glossary_order_does_not_depend_on_dict_insertion_order():
    a = build_user_prompt(["x"], glossary={"b": "2", "a": "1"})
    b = build_user_prompt(["x"], glossary={"a": "1", "b": "2"})
    assert a == b                                      # sorted → same bytes → cache hit
