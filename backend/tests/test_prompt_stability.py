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
    base = build_user_prompt(TEXTS, context={"title": "T"})
    with_priv = build_user_prompt(
        TEXTS, context={"title": "T", "_ocr_source": True, "_budgets": [5, 9]})
    assert base == with_priv
    assert "_ocr_source" not in with_priv and "_budgets" not in with_priv

    rbase = build_refine_prompt(CN, VI, context={"title": "T"})
    rpriv = build_refine_prompt(CN, VI, context={"title": "T", "_ocr_source": True})
    assert rbase == rpriv

    pbase = build_proofread_prompt(TEXTS, context={"title": "T"})
    ppriv = build_proofread_prompt(TEXTS, context={"title": "T", "_ocr_source": True})
    assert pbase == ppriv


def test_repeated_calls_identical():
    args = dict(context={"title": "T"}, glossary={"a": "A", "b": "B"})
    assert build_user_prompt(TEXTS, **args) == build_user_prompt(TEXTS, **args)
