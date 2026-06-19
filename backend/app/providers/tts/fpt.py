"""FPT.AI Vietnamese TTS (cloud, paid). STUB — implement in a later phase.

API: POST https://api.fpt.ai/hmi/tts/v5 with header `api-key`; returns an async
URL to the synthesized audio. Voices: banmai, lannhi, leminh, myan, thuminh, ...
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.providers.base import TTSProvider, _installed


class FptTTS(TTSProvider):
    name = "fpt"
    display_name = "FPT.AI (vi)"
    cost_note = "paid · native Vietnamese voices"

    def available(self) -> bool:
        return bool(settings.fpt_api_key) and _installed("httpx")

    def synthesize(self, text, out_path, *, voice=None, rate=None) -> Path:
        raise NotImplementedError("FPT.AI TTS not implemented yet (P5).")
