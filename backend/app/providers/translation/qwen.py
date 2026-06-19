"""Qwen3 via Alibaba DashScope (OpenAI-compatible endpoint)."""
from __future__ import annotations

from app.config import settings
from app.providers.translation._common import OpenAICompatTranslator


class Qwen3(OpenAICompatTranslator):
    name = "qwen"
    display_name = "Qwen3 (DashScope)"
    cost_note = "cheap tiers · Chinese-native"
    base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    model = "qwen-plus"

    @property
    def api_key(self) -> str:
        return settings.dashscope_api_key
