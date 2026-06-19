"""Google Gemini (2.x). Good Vietnamese fluency; viable refine pass."""
from __future__ import annotations

from app.config import settings
from app.providers.base import TranslationProvider, _installed
from app.providers.translation._common import SYSTEM_PROMPT, build_user_prompt, parse_numbered


class GeminiTranslator(TranslationProvider):
    name = "gemini"
    display_name = "Gemini 2.5 Flash"
    cost_note = "cheap · good Vietnamese fluency"
    model = "gemini-2.5-flash"

    def available(self) -> bool:
        return bool(settings.gemini_api_key) and _installed("google.genai")

    def translate_texts(self, texts, *, source_lang="zh", target_lang="vi", context=None, glossary=None):
        if not texts:
            return []
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        prompt = SYSTEM_PROMPT + "\n\n" + build_user_prompt(texts, context=context, glossary=glossary)
        resp = client.models.generate_content(model=self.model, contents=prompt)
        return parse_numbered(resp.text or "", len(texts), texts)

    def list_models(self) -> list[str]:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        return sorted(m.name.split("/")[-1] for m in client.models.list())

    def test_connection(self) -> tuple[bool, str]:
        if not settings.gemini_api_key:
            return False, "Chưa nhập API key."
        try:
            return True, f"OK · {len(self.list_models())} model"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
