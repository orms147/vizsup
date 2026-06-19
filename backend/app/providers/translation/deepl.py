"""DeepL — fast gist baseline (REST, no SDK needed). Note: Vietnamese target
support is limited/may be unavailable; treat as a quick baseline, not the main
engine. Translates line-by-line to preserve cue count.
"""
from __future__ import annotations

from app.config import settings
from app.providers.base import TranslationProvider, _installed


class DeepLTranslator(TranslationProvider):
    name = "deepl"
    display_name = "DeepL (gist)"
    cost_note = "fast baseline · VI support limited"

    def available(self) -> bool:
        return bool(settings.deepl_api_key) and _installed("httpx")

    def translate_texts(self, texts, *, source_lang="zh", target_lang="vi", context=None, glossary=None):
        if not texts:
            return []
        import httpx

        key = settings.deepl_api_key
        host = "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"
        resp = httpx.post(
            f"{host}/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"text": texts, "source_lang": "ZH", "target_lang": target_lang.upper()},
            timeout=60,
        )
        resp.raise_for_status()
        return [t["text"] for t in resp.json()["translations"]]
