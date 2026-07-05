"""FunASR Paraformer-Large — CN-native transcript with sentence timestamps.
The accuracy option (CER ~1.7% on Mandarin, well below Whisper). CPU-viable;
faster on GPU. Needs `funasr` + torch (install: pip install -e ".[asr-accurate]").

Note: the id is `whisperx_funasr` for config compatibility, but this provider is
FunASR-ONLY (sentence timestamps from its VAD+punc), good enough ahead of the human
edit gate. For word-level timing, use the fused `funasr_aligned` provider, which
snaps this transcript onto faster-whisper word timestamps.
"""
from __future__ import annotations

from pathlib import Path

from app.models import Cue
from app.providers.base import ASRProvider, _installed


class WhisperXFunASR(ASRProvider):
    name = "whisperx_funasr"
    display_name = "FunASR Paraformer (CN-native)"
    cost_note = "local · chính xác tiếng Trung nhất · CPU/GPU"
    local = True

    def __init__(self) -> None:
        self._model = None

    def available(self) -> bool:
        return _installed("funasr")

    def unload(self) -> None:
        self._model = None
        import gc

        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 - torch absent / CPU-only
            pass

    def _load(self):
        from funasr import AutoModel

        if self._model is None:
            self._model = AutoModel(
                model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc",
                disable_update=True,
            )
        return self._model

    def transcribe(self, media: Path, *, language: str = "zh"):
        model = self._load()
        res = model.generate(input=str(media), sentence_timestamp=True)
        cues: list[Cue] = []
        if res:
            for s in res[0].get("sentence_info") or []:
                text = (s.get("text") or "").strip()
                if not text:
                    continue
                cues.append(Cue(
                    index=len(cues) + 1,
                    start=float(s.get("start", 0)) / 1000.0,
                    end=float(s.get("end", 0)) / 1000.0,
                    text=text,
                ))
            if not cues and res[0].get("text"):
                cues.append(Cue(index=1, start=0.0, end=0.0, text=res[0]["text"].strip()))
        return cues
