"""Zhipu GLM-5.2 — current latest GLM (744B MoE, MIT open weights, ~$1.4/$4.4,
1M context). Strong Chinese-native comprehension; self-hostable open weights.
Uses Zhipu's OpenAI-compatible v4 endpoint.
"""
from __future__ import annotations

from app.config import settings
from app.providers.translation._common import OpenAICompatTranslator


class GLM(OpenAICompatTranslator):
    name = "glm"
    display_name = "GLM-5.2 (Zhipu)"
    cost_note = "~$1.4/$4.4 per M · 1M ctx · open weights"
    base_url = "https://open.bigmodel.cn/api/paas/v4"
    # TODO: confirm exact API model id for GLM-5.2 at open.bigmodel.cn; override via config if needed.
    model = "glm-5.2"

    @property
    def api_key(self) -> str:
        return settings.zhipu_api_key
