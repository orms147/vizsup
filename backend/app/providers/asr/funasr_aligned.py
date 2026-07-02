"""E-2 (light): FunASR transcript + word-level timing, fused.

Two sequential phases on the 8 GB card (each unloads before the next starts;
the caller's heavy_task lock is re-entrant): (1) FunASR Paraformer for the
most-accurate Mandarin text, (2) faster-whisper ``word_timestamps=True`` for
the timing track. Pure fusion lives in pipeline/align.py — a sentence that
doesn't anchor well keeps FunASR's own times, so this provider can never be
worse than FunASR alone. whisperx zh forced alignment can slot in later as a
better phase-2 without touching the fusion.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.providers.base import ASRProvider, _installed


class FunASRAligned(ASRProvider):
    name = "funasr_aligned"
    display_name = "FunASR + timing từng từ (fused)"
    cost_note = "local · chữ chính xác nhất + mốc thời gian tốt · chạy 2 pha tuần tự"
    local = True

    def available(self) -> bool:
        return _installed("funasr") and _installed("faster_whisper")

    def transcribe(self, media: Path, *, language: str = "zh"):
        from app.pipeline.align import fuse_transcript_timing
        from app.providers.registry import get_asr

        fun = get_asr("whisperx_funasr")            # phase 1: the transcript
        sentences = fun.transcribe(media, language=language)
        fun.unload()                                # free VRAM before phase 2
        words = self._word_timing(media, language)  # phase 2: the timing track
        return fuse_transcript_timing(sentences, words)

    @staticmethod
    def _word_timing(media: Path, language: str) -> list[tuple[str, float, float]]:
        from faster_whisper import WhisperModel

        size = settings.whisper_model or "large-v3"
        try:
            model = WhisperModel(size, device="cuda", compute_type="float16")
        except Exception:  # noqa: BLE001 - CUDA unavailable → CPU
            model = WhisperModel(size, device="cpu", compute_type="int8")
        try:
            segments, _info = model.transcribe(
                str(media), language=language, vad_filter=True, word_timestamps=True)
            words: list[tuple[str, float, float]] = []
            for seg in segments:
                for w in seg.words or []:
                    words.append((w.word, float(w.start), float(w.end)))
            return words
        finally:
            del model
            import gc

            gc.collect()
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
