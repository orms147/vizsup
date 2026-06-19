"""Fallback ASR: faster-whisper large-v3-turbo. STUB — implement in P2.

Simpler/lighter than WhisperX+FunASR but lower Mandarin accuracy; use only when
the CN-native model can't be installed.
"""
from __future__ import annotations

from pathlib import Path

from app.providers.base import ASRProvider, _installed


class FasterWhisperASR(ASRProvider):
    name = "faster_whisper"
    display_name = "faster-whisper (large-v3-turbo)"
    cost_note = "local · simpler · lower CN accuracy"
    local = True

    def available(self) -> bool:
        return _installed("faster_whisper")

    def transcribe(self, media: Path, *, language: str = "zh"):
        raise NotImplementedError("faster-whisper ASR not implemented yet (P2).")
