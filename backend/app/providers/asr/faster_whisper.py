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
        self._key: tuple[str, str] | None = None
        self._force_cpu = False  # set once a CUDA run fails at inference (missing cuBLAS/cuDNN)

    def available(self) -> bool:
        return _installed("faster_whisper")

    def _load(self):
        from faster_whisper import WhisperModel

        size = settings.whisper_model or "large-v3"
        device = "cpu" if self._force_cpu else "cuda"
        if self._model is not None and self._key == (size, device):
            return self._model
        if self._force_cpu:
            model = WhisperModel(size, device="cpu", compute_type="int8")
        else:
            try:
                model = WhisperModel(size, device="cuda", compute_type="float16")
            except Exception:  # noqa: BLE001 - CUDA unavailable at construction → CPU
                model = WhisperModel(size, device="cpu", compute_type="int8")
                device = "cpu"
        self._model, self._key = model, (size, device)
        return model

    @staticmethod
    def _is_cuda_lib_error(exc: Exception) -> bool:
        s = str(exc).lower()
        return any(k in s for k in ("cublas", "cudnn", "cuda", "libcu", ".dll", "gpu"))

    def _run(self, media: Path, language: str) -> list[Cue]:
        model = self._load()
        segments, _info = model.transcribe(
            str(media), language=language, vad_filter=True, beam_size=5
        )
        cues: list[Cue] = []
        for seg in segments:  # iterating materializes the generator → CUDA errors surface HERE
            text = (seg.text or "").strip()
            if text:
                cues.append(Cue(index=len(cues) + 1, start=float(seg.start), end=float(seg.end), text=text))
        return cues

    def transcribe(self, media: Path, *, language: str = "zh"):
        try:
            return self._run(media, language)
        except Exception as exc:  # noqa: BLE001
            # CUDA model built fine but inference failed (e.g. cublas64_12.dll missing) →
            # rebuild on CPU and retry once, so an incomplete CUDA install never blocks ASR.
            if not self._force_cpu and self._is_cuda_lib_error(exc):
                self._force_cpu = True
                self._model = None
                return self._run(media, language)
            raise
