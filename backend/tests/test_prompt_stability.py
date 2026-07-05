"""API-3: byte-stable prompt prefixes so DeepSeek's automatic disk cache (needs a
byte-identical prefix from token 0) can hit on repeated/resumed runs. Glossary and
context render in SORTED order (not dict-insertion / file-history order), and
private plumbing keys (``_ocr_source``, ``_budgets``) never leak into prompt text.
"""
from __future__ import annotations

from app.providers.translation._common import (
    build_proofread_prompt,
    build_refine_prompt,
    build_user_prompt,
)

TEXTS = ["你好", "再见"]
CN = ["中文一", "中文二"]
VI = ["nháp một", "nháp hai"]


def test_glossary_order_independent():
    a = build_user_prompt(TEXTS, glossary={"b": "B", "a": "A", "c": "C"})
    b = build_user_prompt(TEXTS, glossary={"a": "A", "c": "C", "b": "B"})
    assert a == b
    ra = build_refine_prompt(CN, VI, glossary={"z": "Z", "a": "A"})
    rb = build_refine_prompt(CN, VI, glossary={"a": "A", "z": "Z"})
    assert ra == rb


def test_context_order_independent():
    a = build_user_prompt(TEXTS, context={"title": "T", "platform": "P"})
    b = build_user_prompt(TEXTS, context={"platform": "P", "title": "T"})
    assert a == b


def test_private_keys_never_render():
    # An arbitrary private (_-prefixed) key must not change the prompt at all —
    # keys are reserved plumbing, not meta text. (Keys that DO drive behaviour,
    # like _ocr_source, still never leak their key string; see test below.)
    base = build_user_prompt(TEXTS, context={"title": "T"})
    neutral = build_user_prompt(TEXTS, context={"title": "T", "_note": "x", "_internal": 1})
    assert base == neutral
    assert "_note" not in neutral and "_internal" not in neutral

    rbase = build_refine_prompt(CN, VI, context={"title": "T"})
    rneutral = build_refine_prompt(CN, VI, context={"title": "T", "_note": "x"})
    assert rbase == rneutral

    pbase = build_proofread_prompt(TEXTS, context={"title": "T"})
    pneutral = build_proofread_prompt(TEXTS, context={"title": "T", "_note": "x"})
    assert pbase == pneutral


def test_consumed_private_keys_do_not_leak_their_key_string():
    # _ocr_source drives the OCR hint (API-7) but its KEY must never appear as text.
    p = build_user_prompt(TEXTS, context={"title": "T", "_ocr_source": True, "_budgets": [5]})
    assert "_ocr_source" not in p and "_budgets" not in p


def test_repeated_calls_identical():
    args = dict(context={"title": "T"}, glossary={"a": "A", "b": "B"})
    assert build_user_prompt(TEXTS, **args) == build_user_prompt(TEXTS, **args)
