"""Claude (Anthropic) — best target-language Vietnamese fluency/register.
Ideal as the second-pass refiner in the two-pass design.
Model ids: claude-opus-4-8 (highest quality), claude-sonnet-4-6 (balanced).
"""
from __future__ import annotations

import threading

from app.config import settings
from app.providers.base import TranslationProvider, _installed
from app.providers.translation._common import (
    REFINE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_refine_prompt,
    build_user_prompt,
    system_prompt_for,
    parse_numbered,
)

_CLIENT_LOCK = threading.Lock()


class ClaudeTranslator(TranslationProvider):
    name = "claude"
    display_name = "Claude Sonnet 4.6"
    cost_note = "best Vietnamese register · great refine pass"
    model = "claude-sonnet-4-6"
    can_refine = True   # refine_texts below; proofread_texts NOT overridden → stays False
    _client_cache: dict | None = None  # {api_key: Anthropic} — per instance

    def available(self) -> bool:
        return bool(settings.anthropic_api_key) and _installed("anthropic")

    def _client(self):
        """Cached Anthropic client keyed on api_key. max_retries=0 keeps the
        app's translate._retry the single retry authority; explicit 120 s timeout
        replaces the SDK default."""
        import anthropic

        key = settings.anthropic_api_key
        cache = self._client_cache
        if cache is not None and key in cache:
            return cache[key]
        with _CLIENT_LOCK:
            if self._client_cache is None:
                self._client_cache = {}
            client = self._client_cache.get(key)
            if client is None:
                client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=120.0)
                self._client_cache[key] = client
            return client

    def translate_texts(self, texts, *, source_lang="zh", target_lang="vi", context=None, glossary=None):
        if not texts:
            return []
        msg = self._client().messages.create(
            model=self.model,
            max_tokens=8192,
            system=system_prompt_for(SYSTEM_PROMPT, context),
            messages=[{"role": "user", "content": build_user_prompt(texts, context=context, glossary=glossary)}],
        )
        reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return parse_numbered(reply, len(texts), texts)

    def refine_texts(self, cn_texts, vi_texts, *, context=None, glossary=None):
        if not vi_texts:
            return []
        msg = self._client().messages.create(
            model=self.model,
            max_tokens=8192,
            system=system_prompt_for(REFINE_SYSTEM_PROMPT, context),
            messages=[{"role": "user",
                       "content": build_refine_prompt(cn_texts, vi_texts, context=context, glossary=glossary)}],
        )
        reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return parse_numbered(reply, len(vi_texts), vi_texts)

    def list_models(self) -> list[str]:
        page = self._client().models.list()
        return sorted(m.id for m in page.data)

    def test_connection(self) -> tuple[bool, str]:
        if not settings.anthropic_api_key:
            return False, "Chưa nhập API key."
        try:
            return True, f"OK · {len(self.list_models())} model"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"


class ClaudeOpus(ClaudeTranslator):
    name = "claude-opus"
    display_name = "Claude Opus 4.8"
    cost_note = "highest quality · premium"
    model = "claude-opus-4-8"
