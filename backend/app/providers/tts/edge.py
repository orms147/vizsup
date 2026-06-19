"""edge-tts — free Microsoft neural Vietnamese voices (vi-VN). The v1 default.

Voices: vi-VN-HoaiMyNeural (female), vi-VN-NamMinhNeural (male).
Note: occasionally returns HTTP 403; retry/fallback if it does.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.providers.base import TTSProvider, _installed

DEFAULT_VOICE = "vi-VN-HoaiMyNeural"
VOICES = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]


class EdgeTTS(TTSProvider):
    name = "edge"
    display_name = "edge-tts (vi-VN)"
    cost_note = "free · HoaiMy / NamMinh · no key"
    local = False  # uses Microsoft's online endpoint, but free + no key

    def available(self) -> bool:
        return _installed("edge_tts")

    def voices(self) -> list[str]:
        return VOICES

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        import edge_tts

        out_path = Path(out_path)
        voice = voice or DEFAULT_VOICE
        rate = rate or "+0%"

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
            await communicate.save(str(out_path))

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("EdgeTTS.synthesize() called inside a running event loop; use the async path.")
        asyncio.run(_run())
        return out_path
