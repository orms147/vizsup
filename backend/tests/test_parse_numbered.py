"""N-5: lock providers/translation/_common.parse_numbered — the single choke
point between LLM replies and the cue list — plus the translate_cues count guard
(a short provider reply must be a loud error, never a silent cue drop).
"""
from __future__ import annotations

import pytest

from app.models import Cue
from app.pipeline import translate
from app.providers.translation._common import build_refine_prompt, parse_numbered

FB = ["fb1", "fb2", "fb3"]


def test_full_reply_in_order():
    assert parse_numbered("1| a\n2| b\n3| c", 3, FB) == ["a", "b", "c"]


def test_missing_line_keeps_fallback():
    assert parse_numbered("1| a\n3| c", 3, FB) == ["a", "fb2", "c"]


def test_out_of_range_and_zero_ignored():
    assert parse_numbered("0| x\n5| y", 3, FB) == FB


def test_duplicate_index_last_wins():
    assert parse_numbered("1| x\n1| y", 3, FB)[0] == "y"


def test_spacing_variants_and_inner_pipes():
    assert parse_numbered("2 | giữ nguyên | cả dấu này", 3, FB)[1] == "giữ nguyên | cả dấu này"


def test_echoed_word_budget_is_dropped():
    out = parse_numbered("1| Chết tiệt, im hết! *(6 từ)*\n2| Đi thôi (~4 từ)\n3| Mua 6 từ điển", 3, FB)
    assert out == ["Chết tiệt, im hết!", "Đi thôi", "Mua 6 từ điển"]


def test_empty_reply_returns_fallback_copy():
    out = parse_numbered("", 3, FB)
    assert out == FB and out is not FB


def test_commentary_lines_skipped():
    reply = "Đây là bản dịch:\n1| a\ncảm ơn!\n2| b"
    assert parse_numbered(reply, 3, FB) == ["a", "b", "fb3"]


def test_refine_prompt_echo_lines_never_parse_back():
    # build_refine_prompt echoes sources as '[n] 中文: …' / '[n] nháp: …' — if a
    # model parrots the prompt, those lines must not overwrite the answers
    prompt = build_refine_prompt(["中文一", "中文二"], ["nháp một", "nháp hai"])
    out = parse_numbered(prompt, 2, ["giữ 1", "giữ 2"])
    assert out == ["giữ 1", "giữ 2"]


# --- translate_cues count guard --------------------------------------------------

class _ShortProvider:
    """DeepL-style: returns the API list verbatim — one item short."""
    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        return ["vi"] * (len(texts) - 1)


class _ExactProvider:
    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        return [f"vi:{t}" for t in texts]

    def refine_texts(self, cn, vi, **kw):
        return list(vi)


def _cues(n):
    return [Cue(index=i + 1, start=float(i), end=i + 0.9, text=f"cn{i}") for i in range(n)]


def test_short_reply_raises_instead_of_silent_truncation(monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _ShortProvider())
    with pytest.raises(RuntimeError, match="lệch hàng"):
        translate.translate_cues(_cues(3), draft="fake")


def test_exact_reply_passes(monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _ExactProvider())
    out = translate.translate_cues(_cues(3), draft="fake", refine="fake")
    assert [c.text for c in out] == ["vi:cn0", "vi:cn1", "vi:cn2"]
    assert [c.start for c in out] == [0.0, 1.0, 2.0]   # timing re-attached untouched
