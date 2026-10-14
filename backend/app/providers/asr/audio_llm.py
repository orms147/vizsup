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

def _voice(media: Path, say) -> list[tuple[float, float]] | None:
    """Local VAD spans for timing sentences Whisper heard no words for (music);
    None when unavailable — alignment then falls back to natural speaking length."""
    try:
        from app.pipeline import vad

        return vad.spans_for(media, media.parent, log=say)
    except Exception:  # noqa: BLE001 - timing aid only
        return None


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

    def _request(self, b64: str, instruction: str = INSTRUCTION,
                 max_tokens: int = MAX_TOKENS) -> dict:
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}},
            ]}],
        }

    def ask(self, path: Path, instruction: str, *, max_tokens: int = 600) -> str:
        """Ask the listening model a free-form question about an audio clip (E-4:
        who speaks / sings, gender, age, who they address). No transcript checks."""
        return self._call(path, 0.0, instruction=instruction, max_tokens=max_tokens,
                          transcript=False)

    def _call(self, path: Path, seconds: float, *, instruction: str = INSTRUCTION,
              max_tokens: int = MAX_TOKENS, transcript: bool = True) -> str:
        from app.providers.translation.openrouter import (
            REASONING_HEADROOM,
            REASONING_MIN,
            REASONING_OFF,
            _reasoning_is_mandatory,
            require_choices,
        )

        req = self._request(base64.b64encode(path.read_bytes()).decode(), instruction, max_tokens)
        openrouter = _cloud.is_openrouter(_cloud.endpoint()[0])

        def send(mandatory: bool):
            kw = dict(req)
            if openrouter:   # same reasoning-token trap as translation (see openrouter.py)
                kw["extra_body"] = REASONING_MIN if mandatory else REASONING_OFF
                if mandatory:
                    kw["max_tokens"] = max_tokens + REASONING_HEADROOM
            # validate INSIDE the retried call: an empty 200 must be retried, not crash
            # later at resp.choices[0]
            return require_choices(_cloud.client().chat.completions.create(**kw))

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
        from app import usage

        usage.record(self.model, getattr(resp, "usage", None))   # E-14 per-job cost
        if not transcript:
            return (resp.choices[0].message.content or "").strip()
        text = _cloud.clean_transcript(resp.choices[0].message.content or "")
        _cloud.check_transcript(text, seconds, model=self.model)
        return text

    def transcribe(self, media: Path, *, language: str = "zh", log=None) -> list[Cue]:
        say = log or (lambda _m: None)
        spans = _cloud.plan_chunks(media, CHUNK_SEC)
        n = len(spans)
        say(f"Gửi âm thanh lên {self.model} — {n} đoạn × ≤{int(CHUNK_SEC // 60)} phút…")
        done = _cloud.Counter()
        # each chunk's answer is kept beside the audio: a failed run resumes where it stopped
        cache, aid = _cloud.cache_for(media)
        instr = _cloud.text_tag(INSTRUCTION)
        sent: list[int] = []            # chunks actually uploaded; the rest came from cache

        def hear(it):
            path, span = it
            key = f"transcript|{self.model}|{_cloud.span_tag(span)}|{aid}|{language}|{instr}"

            def listen() -> str:
                pieces = _cloud.split_on_drop(
                    media, span, path, lambda p, sp: self._call(p, sp[1] - sp[0]), log=say)
                sent.append(1)
                return "\n".join(str(t) for _sp, t in pieces)
            text = _cloud.chunk_cache(cache, "transcript", key, listen)
            say(f"  ✓ AI đã nghe xong đoạn {done.next()}/{n}")
            return text

        with _cloud.chunk_files(media, spans) as paths:
            items = list(zip(paths, spans))
            texts = _cloud.run_chunks(hear, items)
            reused = n - len(sent)
            if reused:
                say(f"↷ Dùng lại {reused}/{n} đoạn đã nghe trước đó (không tốn thêm tiền)")
            # no local faster-whisper → the word-timing track comes from the cloud too,
            # fetched while the chunk files still exist
            if _cloud.local_words_available():
                words = None
                say("Lấy mốc thời gian từng từ (faster-whisper trên máy)…")
            else:
                say(f"Lấy mốc thời gian từng từ ({_cloud.timing_model()})…")
                words = _cloud.cloud_words(items, language, cache_dir=cache, audio_id=aid,
                                           media=media, log=say)
        groups = [(span, _cloud.split_sentences(t)) for span, t in zip(spans, texts)]
        voice = _voice(media, say)
        cues = _cloud.reindex(_cloud.time_groups(media, groups, language, words=words,
                                                 voice=voice))
        # kept for the ASR stage to persist (asr_words.json): re-timing must never
        # need another upload
        self.last_words, self.last_groups = words, groups
        guessed = sum(1 for c in cues if c.guess)
        if guessed:
            say(f"  ⚠ {guessed} câu không nghe rõ từng chữ — thời gian ước theo giọng nói, "
                f"đánh dấu “Timing đoán” để xem lại")
        say(f"  ✓ {len(cues)} câu tiếng Trung")
        return cues
