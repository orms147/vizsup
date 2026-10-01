"""Cloud ASR (cloud_stt, audio_llm): chunking, sentence splitting, the two response
shapes, and the fallbacks — with a scripted fake client, no network, no ffmpeg.

Both providers UPLOAD audio and are opt-in by being chosen. The real-API numbers
(CER, timing) live in the commit message / provider docstrings; these pin behaviour.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import Cue
from app.providers.asr import _cloud, audio_llm, cloud_stt
from app.providers.asr.audio_llm import AudioLLM
from app.providers.asr.cloud_stt import CloudSTT
from app.providers.registry import ASR_PROVIDERS
from app.providers.translation.openrouter import REASONING_MIN, REASONING_OFF


class FakeErr(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


# --- endpoint -----------------------------------------------------------------

@pytest.fixture()
def cfg(monkeypatch):
    for k, v in {"stt_api_base_url": "", "stt_api_key": "", "stt_api_model": "",
                 "audio_llm_model": "", "openrouter_api_key": "sk-or"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(CloudSTT, "_text_only", set())
    monkeypatch.setattr(AudioLLM, "_mandatory_reasoning", set())


def test_defaults_to_openrouter_with_the_openrouter_key(cfg):
    assert _cloud.endpoint() == ("https://openrouter.ai/api/v1", "sk-or")


def test_a_direct_vendor_needs_its_own_key(cfg, monkeypatch):
    """The OpenRouter key must never be sent to a different host."""
    monkeypatch.setattr(settings, "stt_api_base_url", "https://api.groq.com/openai/v1/")
    assert _cloud.endpoint() == ("https://api.groq.com/openai/v1", "")
    monkeypatch.setattr(settings, "stt_api_key", "gsk-1")
    assert _cloud.endpoint() == ("https://api.groq.com/openai/v1", "gsk-1")


def test_unavailable_without_any_key(cfg, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert not CloudSTT().available() and not AudioLLM().available()


def test_both_are_registered(cfg):
    assert {"cloud_stt", "audio_llm"} <= set(ASR_PROVIDERS)
    assert ASR_PROVIDERS["cloud_stt"].local is False and ASR_PROVIDERS["audio_llm"].local is False


# --- chunk planning (pure) ----------------------------------------------------

def test_short_audio_is_one_chunk():
    assert _cloud.choose_cuts(300.0, [], target=360.0) == [(0.0, 300.0)]


def test_cuts_snap_to_the_nearest_silence():
    spans = _cloud.choose_cuts(1000.0, [355.0, 372.0, 700.0, 731.0], target=360.0)
    assert spans[0] == (0.0, 355.0)          # 355 is closer to 360 than 372
    assert spans[1][0] == 355.0 and spans[1][1] == 700.0   # ideal 715: 700 (-15) beats 731 (+16)
    assert spans[-1][1] == 1000.0


def test_no_silence_nearby_cuts_exactly_at_the_target():
    assert _cloud.choose_cuts(800.0, [10.0], target=360.0)[0] == (0.0, 360.0)


def test_a_short_tail_is_folded_into_the_last_chunk():
    spans = _cloud.choose_cuts(400.0, [], target=360.0)   # 40 s tail < 15 % of target
    assert spans == [(0.0, 400.0)]


def test_spans_are_contiguous_and_cover_everything():
    spans = _cloud.choose_cuts(3600.0, [i * 7.3 for i in range(500)], target=240.0)
    assert spans[0][0] == 0.0 and spans[-1][1] == 3600.0
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))
    assert all(e - s > 100 for s, e in spans)


# --- sentence splitting (pure) ------------------------------------------------

def test_splits_on_sentence_final_punctuation_and_newlines():
    out = _cloud.split_sentences("大家好，欢迎来到我的频道。今天做红烧肉！好吃吗？\n拜拜")
    assert out == ["大家好，欢迎来到我的频道。", "今天做红烧肉！", "好吃吗？", "拜拜"]


def test_long_sentences_are_halved_at_the_comma_nearest_the_middle():
    s = "首先我们把五花肉切成小块，然后冷水下锅加入姜片和料酒，最后大火烧开转小火慢慢炖。"
    out = _cloud.split_sentences(s)
    assert len(out) >= 2 and "".join(out) == s
    assert all(len(x) <= _cloud.MAX_SENTENCE_CHARS + 2 for x in out)


def test_a_long_sentence_without_commas_stays_whole():
    s = "一" * 40 + "。"
    assert _cloud.split_sentences(s) == [s]


def test_bare_punctuation_and_quotes_are_dropped():
    assert _cloud.split_sentences("「你好。」\n……\n**") == ["你好。"]


def test_proportional_cues_cover_the_span_by_character_count():
    cues = _cloud.proportional_cues(["一二", "三四五六"], 10.0, 16.0)
    assert (cues[0].start, cues[0].end) == (10.0, 12.0)
    assert (cues[1].start, cues[1].end) == (12.0, 16.0)


# --- retry --------------------------------------------------------------------

def test_retry_recovers_from_429_then_succeeds():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise FakeErr(429, "slow down")
        return "ok"

    assert _cloud.with_retry(fn, sleep=lambda s: None) == "ok" and len(calls) == 3


def test_retry_does_not_retry_a_4xx():
    calls = []

    def fn():
        calls.append(1)
        raise FakeErr(400, "bad")

    with pytest.raises(FakeErr):
        _cloud.with_retry(fn, sleep=lambda s: None)
    assert len(calls) == 1


# --- provider plumbing with a fake client ------------------------------------

@pytest.fixture()
def no_media(monkeypatch, tmp_path):
    """Replace ffmpeg-touching steps: two fixed chunks, dummy files, no word timing."""
    spans = [(0.0, 10.0), (10.0, 20.0)]
    monkeypatch.setattr(_cloud, "plan_chunks", lambda media, target: spans)

    from contextlib import contextmanager

    @contextmanager
    def fake_files(media, sp):
        paths = []
        for i in range(len(sp)):
            p = tmp_path / f"c{i}.mp3"
            p.write_bytes(b"ID3")
            paths.append(p)
        yield paths

    monkeypatch.setattr(_cloud, "chunk_files", fake_files)
    timed: list = []

    def fake_timing(media, cues, language):
        timed.append(len(cues))
        return cues

    monkeypatch.setattr(_cloud, "add_word_timing", fake_timing)
    return SimpleNamespace(spans=spans, timed=timed)


class FakeClient:
    def __init__(self, transcribe=None, chat=None):
        self.calls: list[dict] = []
        self._t, self._c = transcribe, chat
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._tr))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._ch))

    def _tr(self, **kw):
        kw = {k: v for k, v in kw.items() if k != "file"}
        self.calls.append(kw)
        return self._t(kw)

    def _ch(self, **kw):
        self.calls.append(kw)
        return self._c(kw)


def test_cloud_stt_uses_segments_with_chunk_offsets(cfg, no_media, monkeypatch):
    def tr(kw):
        assert kw["response_format"] == "verbose_json" and kw["prompt"] == cloud_stt.ZH_PROMPT
        return {"text": "x", "segments": [{"start": 1.0, "end": 2.5, "text": " 你好 "},
                                          {"start": 3.0, "end": 4.0, "text": ""}]}

    fc = FakeClient(transcribe=tr)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    cues = CloudSTT().transcribe(Path("a.wav"))
    assert [(c.index, c.start, c.end, c.text) for c in cues] == [
        (1, 1.0, 2.5, "你好"), (2, 11.0, 12.5, "你好")]      # second chunk offset by 10 s
    assert no_media.timed == []                           # had timestamps → no fusion


def test_cloud_stt_falls_back_to_json_and_fuses_timing(cfg, no_media, monkeypatch):
    """gpt-4o-transcribe answers 400 to verbose_json: retry as json, remember it."""
    def tr(kw):
        if kw["response_format"] == "verbose_json":
            raise FakeErr(400, 'model does not support response_format "verbose_json"')
        return {"text": "你好。再见。"}

    fc = FakeClient(transcribe=tr)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    cues = CloudSTT().transcribe(Path("a.wav"))
    assert [c.text for c in cues] == ["你好。", "再见。", "你好。", "再见。"]
    assert no_media.timed == [4]                              # text-only → word timing
    fmts = [c["response_format"] for c in fc.calls]
    assert fmts.count("verbose_json") == 1                    # memo: probed once, not per chunk


def test_cloud_stt_other_errors_propagate(cfg, no_media, monkeypatch):
    fc = FakeClient(transcribe=lambda kw: (_ for _ in ()).throw(FakeErr(401, "bad key")))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    with pytest.raises(FakeErr, match="bad key"):
        CloudSTT().transcribe(Path("a.wav"))


def _chat_reply(text, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish, message=SimpleNamespace(content=text))])


def test_audio_llm_sends_audio_and_switches_reasoning_off(cfg, no_media, monkeypatch):
    fc = FakeClient(chat=lambda kw: _chat_reply("大家好。今天做红烧肉！"))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    cues = AudioLLM().transcribe(Path("a.wav"))
    call = fc.calls[0]
    part = call["messages"][0]["content"][1]
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "mp3"
    assert call["extra_body"] == REASONING_OFF and call["model"] == audio_llm.DEFAULT_MODEL
    assert [c.text for c in cues] == ["大家好。", "今天做红烧肉！"] * 2
    assert no_media.timed == [4]                              # always fused


def test_audio_llm_degrades_to_minimal_reasoning_when_mandatory(cfg, no_media, monkeypatch):
    def chat(kw):
        if kw["extra_body"] == REASONING_OFF:
            raise FakeErr(400, "Reasoning is mandatory for this endpoint and cannot be disabled.")
        return _chat_reply("你好。")

    fc = FakeClient(chat=chat)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    AudioLLM().transcribe(Path("a.wav"))
    bodies = [c["extra_body"] for c in fc.calls]
    assert bodies.count(REASONING_OFF) == 1 and REASONING_MIN in bodies


def test_audio_llm_sends_no_openrouter_params_to_other_hosts(cfg, no_media, monkeypatch):
    monkeypatch.setattr(settings, "stt_api_base_url", "https://dashscope.example/v1")
    monkeypatch.setattr(settings, "stt_api_key", "k")
    fc = FakeClient(chat=lambda kw: _chat_reply("你好。"))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    AudioLLM().transcribe(Path("a.wav"))
    assert "extra_body" not in fc.calls[0]


def test_audio_llm_truncated_reply_fails_loudly(cfg, no_media, monkeypatch):
    """A cut-off transcript would silently drop the end of the chunk."""
    fc = FakeClient(chat=lambda kw: _chat_reply("你好", finish="length"))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    with pytest.raises(RuntimeError, match="truncated"):
        AudioLLM().transcribe(Path("a.wav"))


def test_add_word_timing_without_faster_whisper_keeps_proportional(monkeypatch):
    monkeypatch.setattr(_cloud, "_installed", lambda m: False)
    cues = [Cue(1, 0.0, 1.0, "你好")]
    assert _cloud.add_word_timing(Path("a.wav"), cues, "zh") is cues


# --- word timing survives an incomplete CUDA install --------------------------

def test_word_timing_falls_back_to_cpu_when_cuda_fails_at_inference(monkeypatch):
    """Model construction on CUDA can succeed and the FIRST inference still die with
    'cublas64_12.dll not found'. That crashed funasr_aligned and the cloud timing
    pass; it must retry on CPU like FasterWhisperASR does."""
    from app.providers.asr import funasr_aligned as fa

    fw = ASR_PROVIDERS["faster_whisper"]
    monkeypatch.setattr(fw, "_force_cpu", False)
    monkeypatch.setattr(fw, "_model", object())
    seen = []

    def fake_on(media, language, *, cpu):
        seen.append(cpu)
        if not cpu:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return [("你", 0.0, 0.2)]

    monkeypatch.setattr(fa, "_faster_whisper_words_on", fake_on)
    assert fa._faster_whisper_words(Path("a.wav"), "zh") == [("你", 0.0, 0.2)]
    assert seen == [False, True]

    # STICKY: a second CUDA attempt after a cuBLAS failure HANGS (not raises) in real
    # ctranslate2 — so the next call, and the faster_whisper provider, must go straight
    # to CPU, and the provider's cached CUDA model must be dropped.
    assert fw._force_cpu is True and fw._model is None
    fa._faster_whisper_words(Path("a.wav"), "zh")
    assert seen == [False, True, True]


def test_word_timing_does_not_mask_unrelated_errors(monkeypatch):
    from app.providers.asr import funasr_aligned as fa

    def fake_on(media, language, *, cpu):
        raise ValueError("bad audio file")

    monkeypatch.setattr(fa, "_faster_whisper_words_on", fake_on)
    with pytest.raises(ValueError, match="bad audio"):
        fa._faster_whisper_words(Path("a.wav"), "zh")
