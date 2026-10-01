"""Cloud STT over the OpenAI-compatible ``/audio/transcriptions`` endpoint —
Whisper-family APIs (OpenRouter, OpenAI, Groq, SiliconFlow, …). UPLOADS the audio.

Asks for ``verbose_json`` segments, so text and timing come back in one call.
Models that only return plain text (gpt-4o-transcribe answers 400 to
verbose_json) are detected once, re-asked for ``json``, and get their timing
from local faster-whisper words (see _cloud.time_groups). So do "segments" longer
than MAX_SEGMENT_SEC, which some routes return as 30 s windows.

Default model web-verified 2026-10-01 on OpenRouter: openai/whisper-large-v3
returns segments for zh (CER 0.015 on a 12-line Mandarin reference, same as local
faster-whisper). Point STT_API_BASE_URL / STT_API_MODEL elsewhere to use a vendor
directly (e.g. Groq's id is plain "whisper-large-v3").
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.models import Cue
from app.providers.asr import _cloud
from app.providers.base import ASRProvider, _installed

DEFAULT_MODEL = "openai/whisper-large-v3"
CHUNK_SEC = 360.0
# A real subtitle segment is a sentence or two. OpenRouter's whisper-large-v3 returned
# 30 s windows labelled as segments on a real vlog (cue starts off by a median 10 s), so
# anything longer is treated as untimed text and re-timed from the local word track.
MAX_SEGMENT_SEC = 12.0
# The classic Whisper trick: a Simplified-Chinese, punctuated prompt pulls the output
# toward Simplified hanzi WITH punctuation (it otherwise drifts to Traditional / none).
ZH_PROMPT = "以下是普通话的句子，使用简体中文，并带有标点符号。"


def _field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


class CloudSTT(ASRProvider):
    name = "cloud_stt"
    display_name = "STT qua API (Whisper…) — gửi âm thanh lên mạng"
    cost_note = "cloud · có mốc thời gian · tính tiền theo phút âm thanh · cần key"
    local = False

    _text_only: set[str] = set()   # models that rejected verbose_json (memo)

    @property
    def model(self) -> str:
        return settings.stt_api_model.strip() or DEFAULT_MODEL

    def available(self) -> bool:
        return _installed("openai") and bool(_cloud.endpoint()[1])

    def test_connection(self) -> tuple[bool, str]:
        return _cloud.test_models_endpoint()

    def _call(self, path: Path, language: str):
        model = self.model
        text_only = model in self._text_only

        def send(fmt: str):
            kw = dict(model=model, language=language, response_format=fmt)
            if language == "zh":
                kw["prompt"] = ZH_PROMPT
            if fmt == "verbose_json":
                # ask for WORDS too: they time text-only fallbacks without a second
                # request (and with them OpenRouter's whisper-large-v3 also returns real
                # per-sentence segments instead of 30 s windows)
                kw["timestamp_granularities"] = ["word", "segment"]
            with path.open("rb") as fh:
                return _cloud.client().audio.transcriptions.create(file=fh, **kw)

        if not text_only:
            try:
                return _cloud.with_retry(lambda: send("verbose_json"))
            except Exception as exc:
                if not (_cloud.status(exc) == 400 and "response_format" in str(exc).lower()):
                    raise
                self._text_only.add(model)
        return _cloud.with_retry(lambda: send("json"))

    def transcribe(self, media: Path, *, language: str = "zh") -> list[Cue]:
        spans = _cloud.plan_chunks(media, CHUNK_SEC)
        with _cloud.chunk_files(media, spans) as paths:
            items = list(zip(paths, spans))
            replies = _cloud.run_chunks(lambda it: self._call(it[0], language), items)

            timed: list[Cue] = []
            groups: list[_cloud.Group] = []
            words: list[_cloud.Word] = []
            missing_words = False
            for (start, end), resp in zip(spans, replies):
                segs = [s for s in (_field(resp, "segments") or [])
                        if (_field(s, "text") or "").strip()]
                if segs and all(float(_field(s, "end")) - float(_field(s, "start"))
                                <= MAX_SEGMENT_SEC for s in segs):
                    for s in segs:
                        timed.append(Cue(0, round(start + float(_field(s, "start")), 3),
                                         round(start + float(_field(s, "end")), 3),
                                         _field(s, "text").strip()))
                    continue
                # no segments, or "segments" that are really 30 s windows: keep the TEXT,
                # take the timing from a word track
                text = (" ".join(_field(s, "text").strip() for s in segs) if segs
                        else _field(resp, "text") or "")
                groups.append(((start, end), _cloud.split_sentences(text)))
                own = _cloud._words_of(resp, start)
                words.extend(own)
                missing_words = missing_words or not own
            track: list[_cloud.Word] | None = words
            if groups and missing_words:     # a text-only model (e.g. gpt-4o-transcribe)
                track = (None if _cloud.local_words_available()
                         else _cloud.cloud_words(items, language))
        cues = timed + _cloud.time_groups(media, groups, language, words=track)
        cues.sort(key=lambda c: c.start)
        return _cloud.reindex(cues)
