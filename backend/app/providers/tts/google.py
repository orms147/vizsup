"""Google Cloud TTS (vi-VN). STUB — implement in a later phase."""
from __future__ import annotations

from pathlib import Path

from app.config import settings  # noqa: F401 - used again when the stub flips to real
from app.providers.base import TTSProvider, _installed  # noqa: F401


class GoogleTTS(TTSProvider):
    name = "google"
    display_name = "Google Cloud TTS (vi-VN)"
    cost_note = "paid · neural vi-VN"
    unavailable_note = "sắp có"

    def available(self) -> bool:
        # STUB: synthesize() raises NotImplementedError — flip when implemented:
        # return bool(settings.google_application_credentials) and _installed("google.cloud.texttospeech")
        return False

    def synthesize(self, text, out_path, *, voice=None, rate=None) -> Path:
        raise NotImplementedError("Google Cloud TTS not implemented yet (P5).")
