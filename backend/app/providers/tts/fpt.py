"""FPT.AI Vietnamese TTS (cloud, paid). STUB — implement in a later phase.

API: POST https://api.fpt.ai/hmi/tts/v5 with header `api-key`; returns an async
URL to the synthesized audio. Voices: banmai, lannhi, leminh, myan, thuminh, ...
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings  # noqa: F401 - used again when the stub flips to real
from app.providers.base import TTSProvider, _installed  # noqa: F401


class FptTTS(TTSProvider):
    name = "fpt"
    display_name = "FPT.AI (vi)"
    cost_note = "paid · native Vietnamese voices"
    unavailable_note = "sắp có"

    def available(self) -> bool:
        # STUB: synthesize() raises NotImplementedError — flip when implemented:
        # return bool(settings.fpt_api_key) and _installed("httpx")
        return False

    def synthesize(self, text, out_path, *, voice=None, rate=None) -> Path:
        raise NotImplementedError("FPT.AI TTS not implemented yet (P5).")
