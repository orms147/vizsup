"""DeepSeek-V4 (OpenAI-compatible). Use the v4 ids; deepseek-chat/deepseek-reasoner
deprecate 2026-07-24. v4-flash for bulk drafting, v4-pro for hard lines.
"""
from __future__ import annotations

from app.config import settings
from app.providers.translation._common import OpenAICompatTranslator


class DeepSeekFlash(OpenAICompatTranslator):
    name = "deepseek"
    display_name = "DeepSeek-V4 Flash"
    cost_note = "~$0.14/$0.28 per M · strong Chinese comprehension"
    base_url = "https://api.deepseek.com"
    model = "deepseek-v4-flash"

    @property
    def api_key(self) -> str:
        return settings.deepseek_api_key


class DeepSeekPro(DeepSeekFlash):
    name = "deepseek-pro"
    display_name = "DeepSeek-V4 Pro"
    cost_note = "~$0.435/$0.87 per M · for hard lines"
    model = "deepseek-v4-pro"
