"""Google Gemini (2.x). Good Vietnamese fluency; viable refine pass."""
from __future__ import annotations

import threading

from app.config import settings
from app.providers.base import TranslationProvider, _installed
from app.providers.translation._common import (
    REFINE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_refine_prompt,
    build_user_prompt,
    parse_numbered,
)

_CLIENT_LOCK = threading.Lock()


class GeminiTranslator(TranslationProvider):
    name = "gemini"
    display_name = "Gemini 2.5 Flash"
    cost_note = "cheap · good Vietnamese fluency"
    model = "gemini-2.5-flash"
    can_refine = True   # refine_texts below; proofread_texts NOT overridden → stays False
    _client_cache: dict | None = None  # {api_key: genai.Client} — per instance

    def available(self) -> bool:
        return bool(settings.gemini_api_key) and _installed("google.genai")

    def _client(self):
        """Cached genai.Client keyed on api_key. google-genai has no default
        request timeout, so set one explicitly (http_options.timeout is in ms)."""
        from google import genai

        key = settings.gemini_api_key
        cache = self._client_cache
        if cache is not None and key in cache:
            return cache[key]
        with _CLIENT_LOCK:
            if self._client_cache is None:
                self._client_cache = {}
            client = self._client_cache.get(key)
            if client is None:
                client = genai.Client(api_key=key, http_options={"timeout": 120_000})
                self._client_cache[key] = client
            return client

    def translate_texts(self, texts, *, source_lang="zh", target_lang="vi", context=None, glossary=None):
        if not texts:
            return []
        prompt = SYSTEM_PROMPT + "\n\n" + build_user_prompt(texts, context=context, glossary=glossary)
        resp = self._client().models.generate_content(model=self.model, contents=prompt)
        return parse_numbered(resp.text or "", len(texts), texts)

    def refine_texts(self, cn_texts, vi_texts, *, context=None, glossary=None):
        if not vi_texts:
            return []
        prompt = (REFINE_SYSTEM_PROMPT + "\n\n"
                  + build_refine_prompt(cn_texts, vi_texts, context=context, glossary=glossary))
        resp = self._client().models.generate_content(model=self.model, contents=prompt)
        return parse_numbered(resp.text or "", len(vi_texts), vi_texts)

    def list_models(self) -> list[str]:
        return sorted(m.name.split("/")[-1] for m in self._client().models.list())

    def test_connection(self) -> tuple[bool, str]:
        if not settings.gemini_api_key:
            return False, "Chưa nhập API key."
        try:
            return True, f"OK · {len(self.list_models())} model"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
