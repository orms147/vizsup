"""Claude (Anthropic) — best target-language Vietnamese fluency/register.
Ideal as the second-pass refiner in the two-pass design.
Model ids: claude-opus-4-8 (highest quality), claude-sonnet-4-6 (balanced).
"""
from __future__ import annotations

from app.config import settings
from app.providers.base import TranslationProvider, _installed
from app.providers.translation._common import SYSTEM_PROMPT, build_user_prompt, parse_numbered


class ClaudeTranslator(TranslationProvider):
    name = "claude"
    display_name = "Claude Sonnet 4.6"
    cost_note = "best Vietnamese register · great refine pass"
    model = "claude-sonnet-4-6"

    def available(self) -> bool:
        return bool(settings.anthropic_api_key) and _installed("anthropic")

    def translate_texts(self, texts, *, source_lang="zh", target_lang="vi", context=None, glossary=None):
        if not texts:
            return []
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(texts, context=context, glossary=glossary)}],
        )
        reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return parse_numbered(reply, len(texts), texts)

    def list_models(self) -> list[str]:
        import anthropic

        page = anthropic.Anthropic(api_key=settings.anthropic_api_key).models.list()
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
