"""Recommended ASR: WhisperX (VAD + word-level forced alignment for timing) with
FunASR Paraformer-Large as the Chinese transcript engine.

STUB — implement in P2. Rationale (see docs/research.md): Whisper Mandarin CER is
3-5x worse than CN-native models, so use a CN-native transcript + WhisperX timing.
On the RTX 4070 (8GB) run sequentially. FunASR Paraformer is CPU-viable as a
fallback when no GPU is free.
"""
from __future__ import annotations

from pathlib import Path

from app.providers.base import ASRProvider, _installed


class WhisperXFunASR(ASRProvider):
    name = "whisperx_funasr"
    display_name = "WhisperX timing + FunASR Paraformer"
    cost_note = "local · best CN accuracy + word timestamps · GPU"
    local = True

    def available(self) -> bool:
        return _installed("whisperx") and _installed("funasr")

    def transcribe(self, media: Path, *, language: str = "zh"):
        raise NotImplementedError(
            "WhisperX+FunASR ASR not implemented yet (P2). "
            "Plan: FunASR Paraformer-Large for the transcript, WhisperX wav2vec2 "
            "forced alignment for word-level timing, VAD to reduce hallucination."
        )
