"""No-op translator: returns source text unchanged. Always available, needs no
key. Used as the default so the smoke path runs offline and as a safe fallback.
"""
from __future__ import annotations

from app.providers.base import TranslationProvider


class PassthroughTranslator(TranslationProvider):
    name = "passthrough"
    display_name = "Passthrough (no translation)"
    cost_note = "free · offline · keeps Chinese text (smoke-test only)"
    local = True

    def translate_texts(self, texts, *, source_lang="zh", target_lang="vi", context=None, glossary=None):
        return list(texts)
