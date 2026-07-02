"""OpenRouter — one API key, access to virtually every model. OpenAI-compatible.
The specific model is chosen in Settings (Fetch models → pick), stored in
settings.openrouter_model.
"""
from __future__ import annotations

from app.config import settings
from app.providers.translation._common import OpenAICompatTranslator

# Fallback when no model is picked in Settings. Web-verified 2026-07-02:
# openrouter.ai/deepseek/deepseek-v4-flash (the old deepseek/deepseek-chat-v3
# slug rides the legacy deepseek-chat API that deprecates 2026-07-24).
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


class OpenRouter(OpenAICompatTranslator):
    name = "openrouter"
    display_name = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    extra_headers = {"HTTP-Referer": "https://github.com/vizsup", "X-Title": "vizsup"}

    @property
    def api_key(self) -> str:  # type: ignore[override]
        return settings.openrouter_api_key

    @property
    def model(self) -> str:  # type: ignore[override]
        return settings.openrouter_model or DEFAULT_MODEL

    @property
    def cost_note(self) -> str:  # type: ignore[override]
        m = settings.openrouter_model
        return f"1 key · model: {m}" if m else "1 key · mọi model (chọn ở Cài đặt)"
