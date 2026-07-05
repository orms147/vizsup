"""E-2 (light) + ENG-3: FunASR transcript + word-level timing, fused.

Two sequential phases on the 8 GB card (each unloads before the next; the caller's
heavy_task lock is re-entrant): (1) FunASR Paraformer for the most-accurate Mandarin
TEXT, (2) a word-level TIMING track. Pure fusion lives in pipeline/align.py — a
sentence that doesn't anchor well keeps FunASR's own times, so this provider can
never be worse than FunASR alone.

Phase 2 prefers whisperX forced alignment (a Chinese wav2vec2 CTC model) when it's
importable — aligning FunASR's own sentences straight onto the audio is the tightest
timing — and falls back to faster-whisper word timestamps otherwise (the guaranteed
baseline; the provider must never regress below FunASR-only).
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.providers.base import ASRProvider, _installed


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - torch absent / CPU-only
        return False


def _free_vram() -> None:
    import gc

    gc.collect()
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def _faster_whisper_words(media: Path, language: str) -> list[tuple[str, float, float]]:
    """Baseline phase-2: faster-whisper word timestamps. Always available when the
    provider is (available() requires faster_whisper)."""
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
        _free_vram()


def _whisperx_words(media: Path, language: str, sentences) -> list[tuple[str, float, float]]:
    """Preferred phase-2 (when whisperx is installed): forced-align FunASR's own
    sentences to the audio with a language-specific wav2vec2 CTC model. WhisperX
    auto-selects the default zh model (jonatasgrosman/wav2vec2-large-xlsr-53-chinese-
    zh-cn) — web-verify the id if it drifts."""
    import whisperx

    device = "cuda" if _cuda_available() else "cpu"
    audio = whisperx.load_audio(str(media))
    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)
    try:
        transcript = [{"start": float(s.start), "end": float(s.end), "text": s.text}
                      for s in sentences if (s.text or "").strip()]
        result = whisperx.align(transcript, model_a, metadata, audio, device,
                                return_char_alignments=False)
        words: list[tuple[str, float, float]] = []
        for w in result.get("word_segments", []):
            st, en = w.get("start"), w.get("end")
            if st is not None and en is not None:
                words.append((str(w.get("word", "")), float(st), float(en)))
        return words
    finally:
        del model_a
        _free_vram()


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
        words = self._word_timing(media, language, sentences)  # phase 2: the timing track
        return fuse_transcript_timing(sentences, words)

    @staticmethod
    def _word_timing(media: Path, language: str,
                     sentences=None) -> list[tuple[str, float, float]]:
        """whisperX forced alignment when available (tighter), else the faster-whisper
        baseline. whisperX failure degrades to the baseline — never regresses below it."""
        if sentences and _installed("whisperx"):
            try:
                return _whisperx_words(media, language, sentences)
            except Exception:  # noqa: BLE001 - baseline is the guarantee
                pass
        return _faster_whisper_words(media, language)
