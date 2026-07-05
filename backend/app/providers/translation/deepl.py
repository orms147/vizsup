"""DeepL — fast gist baseline (REST, no SDK needed). Note: Vietnamese target
support is limited/may be unavailable; treat as a quick baseline, not the main
engine. Translates line-by-line to preserve cue count.
"""
from __future__ import annotations

import threading

from app.config import settings
from app.providers.base import TranslationProvider, _installed

_CLIENT_LOCK = threading.Lock()


class DeepLTranslator(TranslationProvider):
    name = "deepl"
    display_name = "DeepL (gist)"
    cost_note = "fast baseline · VI support limited"
    _http = None  # cached httpx.Client (auth is per-request, so no need to key on the api_key)

    def available(self) -> bool:
        return bool(settings.deepl_api_key) and _installed("httpx")

    def _client(self):
        import httpx

        if self._http is None:
            with _CLIENT_LOCK:
                if self._http is None:
                    self._http = httpx.Client(timeout=60)
        return self._http

    def translate_texts(self, texts, *, source_lang="zh", target_lang="vi", context=None, glossary=None):
        if not texts:
            return []
        key = settings.deepl_api_key
        host = "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"
        resp = self._client().post(
            f"{host}/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"text": texts, "source_lang": "ZH", "target_lang": target_lang.upper()},
        )
        resp.raise_for_status()
        return [t["text"] for t in resp.json()["translations"]]
