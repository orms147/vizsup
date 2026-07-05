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

    @property
    def model(self) -> str:  # type: ignore[override]
        # "glm-5.2" web-verified at open.bigmodel.cn 2026-07-02 ($1.40/$4.40 per M);
        # ZHIPU_MODEL in .env overrides when Zhipu rolls the id.
        return settings.zhipu_model or "glm-5.2"

    @property
    def api_key(self) -> str:
        return settings.zhipu_api_key

    def test_connection(self) -> tuple[bool, str]:
        # Zhipu's paas/v4 endpoint may not expose OpenAI-style GET /models —
        # fall back to a 1-token chat ping so "Test kết nối" can't false-fail.
        if not self.api_key:
            return False, "Chưa nhập API key."
        try:
            return True, f"OK · {len(self.list_models())} model khả dụng"
        except Exception:  # noqa: BLE001 - /models unsupported → ping the chat API
            try:
                # 15 s cap so a dead endpoint fails "Test kết nối" fast, not at
                # the client's 120 s read timeout.
                self._client().with_options(timeout=15.0).chat.completions.create(
                    model=self.model, max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )
                return True, f"OK · {self.model} phản hồi"
            except Exception as exc:  # noqa: BLE001
                return False, f"{type(exc).__name__}: {exc}"
