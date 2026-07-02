"""Azure Neural TTS (vi-VN). STUB — implement in a later phase.

Voices: vi-VN-HoaiMyNeural, vi-VN-NamMinhNeural (same voices as edge, but via the
paid Speech SDK with SSML rate/pitch control and reliable uptime).
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings  # noqa: F401 - used again when the stub flips to real
from app.providers.base import TTSProvider, _installed  # noqa: F401


class AzureTTS(TTSProvider):
    name = "azure"
    display_name = "Azure Neural (vi-VN)"
    cost_note = "paid · reliable · SSML rate control"

    unavailable_note = "sắp có"

    def available(self) -> bool:
        # STUB: synthesize() raises NotImplementedError — advertising available()
        # let a keyed user pass preflight and die mid-render. Flip when implemented:
        # return bool(settings.azure_speech_key) and _installed("azure.cognitiveservices.speech")
        return False

    def synthesize(self, text, out_path, *, voice=None, rate=None) -> Path:
        raise NotImplementedError("Azure TTS not implemented yet (P5).")
