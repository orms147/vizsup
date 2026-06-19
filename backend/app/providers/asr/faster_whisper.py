"""faster-whisper ASR (CTranslate2). The v1 default: a single pip package, runs
on GPU (cuda/float16) or CPU (int8), gives segment timestamps and handles
Chinese acceptably. CER is higher than a CN-native model, but the human edit
gate catches errors; use the FunASR provider for max accuracy.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.models import Cue
from app.providers.base import ASRProvider, _installed


class FasterWhisperASR(ASRProvider):
    name = "faster_whisper"
    display_name = "faster-whisper (large-v3)"
    cost_note = "local · 1 gói pip · CER tiếng Trung ~5% (có cổng sửa)"
    local = True

    def __init__(self) -> None:
        self._model = None
        self._key: str | None = None

    def available(self) -> bool:
        return _installed("faster_whisper")

    def _load(self):
        from faster_whisper import WhisperModel

        size = settings.whisper_model or "large-v3"
        if self._model is not None and self._key == size:
            return self._model
        try:
            model = WhisperModel(size, device="cuda", compute_type="float16")
        except Exception:  # noqa: BLE001 - no CUDA libs → CPU
            model = WhisperModel(size, device="cpu", compute_type="int8")
        self._model, self._key = model, size
        return model

    def transcribe(self, media: Path, *, language: str = "zh"):
        model = self._load()
        segments, _info = model.transcribe(
            str(media), language=language, vad_filter=True, beam_size=5
        )
        cues: list[Cue] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if text:
                cues.append(Cue(index=len(cues) + 1, start=float(seg.start), end=float(seg.end), text=text))
        return cues
