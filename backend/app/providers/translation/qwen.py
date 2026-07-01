"""Qwen via Alibaba DashScope (OpenAI-compatible endpoint).

`qwen-plus` is a rolling alias that always points at the current Qwen-Plus
snapshot (the generation churns fast — 3.5 → 3.6 → 3.7…), so we label it by the
alias, not a version number, to avoid the display going stale. Cheap, Chinese-native.
"""
from __future__ import annotations

from app.config import settings
from app.providers.translation._common import OpenAICompatTranslator


class Qwen(OpenAICompatTranslator):
    name = "qwen"
    display_name = "Qwen-Plus (DashScope)"
    cost_note = "cheap tiers · Chinese-native"
    base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    model = "qwen-plus"

    @property
    def api_key(self) -> str:
        return settings.dashscope_api_key
