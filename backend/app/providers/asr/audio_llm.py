"""An audio-capable LLM transcribes the Mandarin (chat completion with an
``input_audio`` part); local faster-whisper supplies the word timing. UPLOADS the audio.

Why: Whisper is weaker on Mandarin than Chinese-native models (CLAUDE.md: "timing ≠
accuracy"). Omni models like Qwen hear Mandarin well but return text only, so this
is the funasr_aligned recipe with a cloud model in FunASR's seat: their text, split
into subtitle lines, snapped onto Whisper's word timestamps.

Default model benchmarked 2026-10-01 on OpenRouter (docs/providers.md): qwen/qwen3.8-
omni-flash — CER 0.128 on a real 137 s Douyin vlog vs 0.185 for local faster-whisper,
0.016 vs 0.074 on accented speech, cue starts within 0.2 s (median), and consistent on all
4 runs. The MiMo models sometimes hear better but loop, go empty, or answer in another
language on some runs — the guards in _cloud.py turn those into loud errors / fallbacks.
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

    def _call(self, path: Path, seconds: float) -> str:
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
            raise RuntimeError(f"{self.model}: reply truncated at max_tokens (chunk {path.name}) "
                               f"— usually a repetition loop; pick another AUDIO_LLM_MODEL")
        text = _cloud.clean_transcript(resp.choices[0].message.content or "")
        _cloud.check_transcript(text, seconds, model=self.model)
        return text

    def transcribe(self, media: Path, *, language: str = "zh") -> list[Cue]:
        spans = _cloud.plan_chunks(media, CHUNK_SEC)
        with _cloud.chunk_files(media, spans) as paths:
            items = list(zip(paths, spans))
            texts = _cloud.run_chunks(lambda it: self._call(it[0], it[1][1] - it[1][0]), items)
            # no local faster-whisper → the word-timing track comes from the cloud too,
            # fetched while the chunk files still exist
            words = (None if _cloud.local_words_available()
                     else _cloud.cloud_words(items, language))
        groups = [(span, _cloud.split_sentences(t)) for span, t in zip(spans, texts)]
        return _cloud.reindex(_cloud.time_groups(media, groups, language, words=words))
