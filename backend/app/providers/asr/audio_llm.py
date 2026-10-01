"""An audio-capable LLM transcribes the Mandarin (chat completion with an
``input_audio`` part); local faster-whisper supplies the word timing. UPLOADS the audio.

Why: Whisper is weaker on Mandarin than Chinese-native models (CLAUDE.md: "timing ≠
accuracy"). Omni models like Qwen hear Mandarin well but return text only, so this
is the funasr_aligned recipe with a cloud model in FunASR's seat: their text, split
into subtitle lines, snapped onto Whisper's word timestamps.

Default model web-verified 2026-10-01 on OpenRouter: qwen/qwen3.8-omni-flash —
CER 0.000 on a 12-line Mandarin reference (Whisper family: 0.015), mp3 input, ~5 s.
Also verified working: xiaomi/mimo-v2.6-flash, openai/gpt-audio-mini.
"""
from __future__ import annotations

import base64
from pathlib import Path

from app.config import settings
from app.models import Cue
from app.providers.asr import _cloud
from app.providers.base import ASRProvider, _installed

DEFAULT_MODEL = "qwen/qwen3.8-omni-flash"
CHUNK_SEC = 240.0       # shorter than cloud_stt: long audio invites an LLM to skip/summarize
MAX_TOKENS = 4096       # ~240 s of speech is ~1.1k hanzi

INSTRUCTION = (
    "Transcribe the Mandarin Chinese speech in this audio VERBATIM, in Simplified Chinese "
    "with full punctuation (，。！？). Rules: do not translate, summarize, explain or add "
    "anything; keep filler words as spoken; if there is no speech, output nothing. "
    "Output only the transcript."
)


class AudioLLM(ASRProvider):
    name = "audio_llm"
    display_name = "AI nghe audio (Qwen-Omni…) + timing local — gửi âm thanh lên mạng"
    cost_note = ("cloud · chữ Trung chính xác nhất · mốc thời gian từ faster-whisper local "
                 "· tính tiền theo token âm thanh")
    local = False

    _mandatory_reasoning: set[str] = set()

    @property
    def model(self) -> str:
        return settings.audio_llm_model.strip() or DEFAULT_MODEL

    def available(self) -> bool:
        return _installed("openai") and bool(_cloud.endpoint()[1])

    def test_connection(self) -> tuple[bool, str]:
        return _cloud.test_models_endpoint()

    def _request(self, b64: str) -> dict:
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": INSTRUCTION},
                {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}},
            ]}],
        }

    def _call(self, path: Path) -> str:
        from app.providers.translation.openrouter import (
            REASONING_HEADROOM,
            REASONING_MIN,
            REASONING_OFF,
            _reasoning_is_mandatory,
        )

        req = self._request(base64.b64encode(path.read_bytes()).decode())
        openrouter = _cloud.is_openrouter(_cloud.endpoint()[0])

        def send(mandatory: bool):
            kw = dict(req)
            if openrouter:   # same reasoning-token trap as translation (see openrouter.py)
                kw["extra_body"] = REASONING_MIN if mandatory else REASONING_OFF
                if mandatory:
                    kw["max_tokens"] = MAX_TOKENS + REASONING_HEADROOM
            return _cloud.client().chat.completions.create(**kw)

        mandatory = self.model in self._mandatory_reasoning
        try:
            resp = _cloud.with_retry(lambda: send(mandatory))
        except Exception as exc:
            if mandatory or not (openrouter and _reasoning_is_mandatory(exc)):
                raise
            self._mandatory_reasoning.add(self.model)
            resp = _cloud.with_retry(lambda: send(True))
        if resp.choices[0].finish_reason == "length":
            raise RuntimeError(f"audio LLM reply truncated at max_tokens — chunk {path.name}")
        return resp.choices[0].message.content or ""

    def transcribe(self, media: Path, *, language: str = "zh") -> list[Cue]:
        spans = _cloud.plan_chunks(media, CHUNK_SEC)
        with _cloud.chunk_files(media, spans) as paths:
            texts = _cloud.run_chunks(self._call, paths)
        cues: list[Cue] = []
        for (start, end), text in zip(spans, texts):
            cues.extend(_cloud.proportional_cues(_cloud.split_sentences(text), start, end))
        return _cloud.reindex(_cloud.add_word_timing(media, cues, language))
