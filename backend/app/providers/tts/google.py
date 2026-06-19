"""Google Cloud TTS (vi-VN). STUB — implement in a later phase."""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.providers.base import TTSProvider, _installed


class GoogleTTS(TTSProvider):
    name = "google"
    display_name = "Google Cloud TTS (vi-VN)"
    cost_note = "paid · neural vi-VN"

    def available(self) -> bool:
        return bool(settings.google_application_credentials) and _installed("google.cloud.texttospeech")

    def synthesize(self, text, out_path, *, voice=None, rate=None) -> Path:
        raise NotImplementedError("Google Cloud TTS not implemented yet (P5).")
