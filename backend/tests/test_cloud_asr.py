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


def test_unpunctuated_whisper_text_is_cut_into_subtitle_sized_pieces():
    """Whisper APIs often return Chinese with no punctuation; one 30 s run left whole
    became one 30 s cue (median 47 s timing error on a real vlog)."""
    s = "我们再把校服换上因为最近天气真的太热了直接把这个夏季校服给掏出来穿看到这个阳光应该能看出来有多热吧"
    out = _cloud.split_sentences(s)
    assert len(out) > 2 and "".join(out) == s
    assert all(len(x) <= _cloud.MAX_SENTENCE_CHARS for x in out)


def test_spaces_are_preferred_over_blind_halving():
    s = "今天天气真的特别特别好啊 我们一起去公园散步然后吃个午饭吧好不好"
    out = _cloud.split_sentences(s)
    assert out[0] == "今天天气真的特别特别好啊"


def test_bare_punctuation_and_quotes_are_dropped():
    assert _cloud.split_sentences("「你好。」\n……\n**") == ["你好。"]


def test_time_groups_without_faster_whisper_spreads_each_chunk(monkeypatch):
    monkeypatch.setattr(_cloud, "_installed", lambda m: False)
    cues = _cloud.time_groups(Path("a.wav"), [((10.0, 16.0), ["一二", "三四五六"]),
                                              ((16.0, 20.0), [])], "zh")
    assert [(c.start, c.end) for c in cues] == [(10.0, 12.0), (12.0, 16.0)]


def test_time_groups_aligns_each_chunk_on_its_own_words(monkeypatch):
    """One whisper pass; each chunk sees only words inside its window (± margin)."""
    from app.providers.asr import funasr_aligned as fa

    words = [("你好", 1.0, 1.5), ("再见", 31.0, 31.6)]
    monkeypatch.setattr(_cloud, "_installed", lambda m: True)
    monkeypatch.setattr(fa, "_faster_whisper_words", lambda media, lang: words)
    cues = _cloud.time_groups(Path("a.wav"), [((0.0, 30.0), ["你好。"]),
                                              ((30.0, 60.0), ["再见。"])], "zh")
    assert (cues[0].start, cues[0].end) == (1.0, 1.5)
    assert (cues[1].start, cues[1].end) == (31.0, 31.6)


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

    words_seen: list = []

    def fake_timing(media, groups, language, words=None, voice=None):
        from app.pipeline.align import _spread

        timed.append(sum(len(sents) for _span, sents in groups))
        words_seen.append(words)
        return [c for (lo, hi), sents in groups for c in _spread(sents, lo, hi)]

    monkeypatch.setattr(_cloud, "time_groups", fake_timing)
    # default: pretend local faster-whisper exists so no cloud word call is made;
    # the cloud-word tests below flip this
    monkeypatch.setattr(_cloud, "local_words_available", lambda: True)
    return SimpleNamespace(spans=spans, timed=timed, words_seen=words_seen)


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
    assert no_media.timed == [0]                          # had timestamps → nothing to align


def test_cloud_stt_treats_30s_segments_as_untimed_text(cfg, no_media, monkeypatch):
    """OpenRouter's whisper-large-v3 returned 30 s windows labelled as segments on a
    real vlog (cue starts off by a median 10 s). Those must be re-timed from words."""
    def tr2(kw):
        return {"segments": [{"start": 0.0, "end": 30.0, "text": "大家好。今天做红烧肉。"}]}

    fc = FakeClient(transcribe=tr2)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    cues = CloudSTT().transcribe(Path("a.wav"))
    assert [c.text for c in cues] == ["大家好。", "今天做红烧肉。"] * 2
    assert no_media.timed == [4]                          # went through word alignment


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


# --- output sanity: garbage fails loudly instead of reaching the edit gate -----

def test_a_translated_reply_is_rejected(cfg, no_media, monkeypatch):
    """mimo-v2.5 answered a Chinese vlog with a KOREAN translation (2026-10-01)."""
    fc = FakeClient(chat=lambda kw: _chat_reply("오늘은 한국에 가서 학교에 가는 날입니다."))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    with pytest.raises(RuntimeError, match="not a Chinese transcript"):
        AudioLLM().transcribe(Path("a.wav"))


def test_a_looping_reply_is_rejected(cfg, no_media, monkeypatch):
    """mimo-v2.6-pro produced 4.2k hanzi for ~800 spoken (repetition loop)."""
    fc = FakeClient(chat=lambda kw: _chat_reply("好的。" * 200))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    with pytest.raises(RuntimeError, match="looping"):
        AudioLLM().transcribe(Path("a.wav"))           # 10 s chunks → 400 hanzi is absurd


@pytest.mark.parametrize("raw, clean", [
    ("0.03-12.51 | SPEAKER_00: 大家好。", "大家好。"),
    ("[00:01 - 00:04] 你好", "你好"),
    ("说话人1：今天吃什么", "今天吃什么"),
    ("大家好，2024年我们见。", "大家好，2024年我们见。"),     # a year is content, not a stamp
])
def test_unasked_timestamps_and_speaker_labels_are_stripped(raw, clean):
    assert _cloud.clean_transcript(raw) == clean


def test_a_little_english_inside_chinese_is_fine():
    _cloud.check_transcript("今天我们去 Costco 买 iPhone 和 vlog 素材。", 10.0, model="m")


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


def test_an_empty_reply_over_heard_speech_falls_back_to_whisper_text(monkeypatch):
    """mimo-v2.5 returned nothing for a noisy real vlog: the chunk's speech must not
    vanish — whisper's own text (timed by itself) stands in for it."""
    from app.providers.asr import funasr_aligned as fa

    words = [(ch, i * 0.3, i * 0.3 + 0.3) for i, ch in enumerate("今天我们去学校上课然后去便利店买了柠檬水和红茶")]
    monkeypatch.setattr(_cloud, "_installed", lambda m: True)
    monkeypatch.setattr(fa, "_faster_whisper_words", lambda media, lang: words)
    cues = _cloud.time_groups(Path("a.wav"), [((0.0, 30.0), ["嗯。"])], "zh")
    assert "".join(c.text for c in cues).startswith("今天我们去学校")


def test_a_short_reply_over_near_silence_is_left_alone(monkeypatch):
    from app.providers.asr import funasr_aligned as fa

    words = [("嗯", 1.0, 1.2), ("好", 2.0, 2.2)]
    monkeypatch.setattr(_cloud, "_installed", lambda m: True)
    monkeypatch.setattr(fa, "_faster_whisper_words", lambda media, lang: words)
    cues = _cloud.time_groups(Path("a.wav"), [((0.0, 30.0), ["好的。"])], "zh")
    assert [c.text for c in cues] == ["好的。"]


# --- no local faster-whisper: the word track comes from the cloud --------------

def _word_reply(words):
    return {"text": "x", "segments": [], "words": [
        {"word": w, "start": s, "end": e} for w, s, e in words]}


def test_audio_llm_fetches_cloud_words_when_whisper_is_not_installed(cfg, no_media, monkeypatch):
    monkeypatch.setattr(_cloud, "local_words_available", lambda: False)
    fc = FakeClient(chat=lambda kw: _chat_reply("你好。"),
                    transcribe=lambda kw: _word_reply([("你", 1.0, 1.2), ("好", 1.2, 1.5)]))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    AudioLLM().transcribe(Path("a.wav"))

    tr_calls = [c for c in fc.calls if "timestamp_granularities" in c]
    assert len(tr_calls) == 2                                  # one per chunk
    assert tr_calls[0]["model"] == _cloud.DEFAULT_TIMING_MODEL
    assert "word" in tr_calls[0]["timestamp_granularities"]
    words = no_media.words_seen[0]
    assert words[0] == ("你", 1.0, 1.2) and words[2] == ("你", 11.0, 11.2)   # chunk offset


def test_audio_llm_uses_local_words_when_whisper_is_installed(cfg, no_media, monkeypatch):
    fc = FakeClient(chat=lambda kw: _chat_reply("你好。"),
                    transcribe=lambda kw: pytest.fail("no cloud word call expected"))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    AudioLLM().transcribe(Path("a.wav"))
    assert no_media.words_seen == [None]                      # → time_groups runs local


def test_timing_model_is_configurable(cfg, monkeypatch):
    assert _cloud.timing_model() == "openai/whisper-large-v3"
    monkeypatch.setattr(settings, "stt_timing_model", "whisper-large-v3")
    assert _cloud.timing_model() == "whisper-large-v3"


def test_cloud_stt_reuses_its_own_words_for_long_segments(cfg, no_media, monkeypatch):
    """One call returns text AND words: no second request to time 30 s windows."""
    monkeypatch.setattr(_cloud, "local_words_available", lambda: False)
    calls = []

    def tr(kw):
        calls.append(kw)
        return {"segments": [{"start": 0.0, "end": 30.0, "text": "大家好。"}],
                "words": [{"word": "大家", "start": 0.5, "end": 0.9},
                          {"word": "好", "start": 0.9, "end": 1.2}]}

    fc = FakeClient(transcribe=tr)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    CloudSTT().transcribe(Path("a.wav"))
    assert len(calls) == 2                                    # one per chunk, nothing extra
    assert no_media.words_seen[0][0] == ("大家", 0.5, 0.9)
    assert no_media.words_seen[0][2] == ("大家", 10.5, 10.9)


def test_cloud_stt_text_only_model_without_whisper_fetches_cloud_words(cfg, no_media, monkeypatch):
    monkeypatch.setattr(_cloud, "local_words_available", lambda: False)
    monkeypatch.setattr(settings, "stt_api_model", "openai/gpt-4o-mini-transcribe")

    def tr(kw):
        if kw["model"] == "openai/gpt-4o-mini-transcribe":
            if kw["response_format"] == "verbose_json":
                raise FakeErr(400, 'does not support response_format "verbose_json"')
            return {"text": "你好。"}
        return _word_reply([("你好", 0.2, 0.6)])            # the timing model

    fc = FakeClient(transcribe=tr)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    CloudSTT().transcribe(Path("a.wav"))
    timing_calls = [c for c in fc.calls if c["model"] == _cloud.DEFAULT_TIMING_MODEL]
    assert len(timing_calls) == 2
    assert no_media.words_seen[0][0] == ("你好", 0.2, 0.6)


# --- E-14: the Whisper word track is paid for, so it shows in usage.json ---------

def _usage_models(job_dir: Path) -> dict:
    import json

    return json.loads((job_dir / "usage.json").read_text(encoding="utf-8"))["models"]


def test_cloud_word_track_cost_comes_from_the_response(cfg, no_media, monkeypatch, tmp_path):
    """OpenRouter's /audio/transcriptions answers usage {seconds, cost} (live 2026-10-26:
    60 s of whisper-large-v3 → cost 0.00045). That cost goes into usage.json."""
    from app import usage

    monkeypatch.setattr(_cloud, "local_words_available", lambda: False)
    reply = {**_word_reply([("你", 1.0, 1.2)]), "usage": {"seconds": 10.0, "cost": 0.0001}}
    fc = FakeClient(chat=lambda kw: _chat_reply("你好。"), transcribe=lambda kw: reply)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    job = tmp_path / "job"
    job.mkdir()
    with usage.track(job, phase="gate"):
        AudioLLM().transcribe(Path("a.wav"))
    whisper = _usage_models(job)[_cloud.DEFAULT_TIMING_MODEL]
    assert whisper["calls"] == 2 and whisper["cost"] == pytest.approx(0.0002)
    assert usage.summary(job)["cost"] == pytest.approx(0.0002)    # in the job's "AI $"


def test_without_a_cost_in_the_response_the_audio_length_is_priced(cfg, no_media, monkeypatch,
                                                                   tmp_path):
    """A direct vendor may answer no usage at all: seconds × STT_PRICE_PER_MIN."""
    from app import usage

    monkeypatch.setattr(_cloud, "local_words_available", lambda: False)
    monkeypatch.setattr(settings, "stt_price_per_min", 0.006)
    fc = FakeClient(chat=lambda kw: _chat_reply("你好。"),
                    transcribe=lambda kw: _word_reply([("你", 1.0, 1.2)]))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    with usage.track(tmp_path, phase="gate"):
        AudioLLM().transcribe(Path("a.wav"))               # two 10 s chunks
    whisper = _usage_models(tmp_path)[_cloud.DEFAULT_TIMING_MODEL]
    assert whisper["calls"] == 2 and whisper["cost"] == pytest.approx(2 * 10 / 60 * 0.006)


def test_usage_seconds_beat_the_chunk_length_when_pricing(monkeypatch, tmp_path):
    from app import usage

    monkeypatch.setattr(settings, "stt_price_per_min", 0.6)
    with usage.track(tmp_path, phase="gate"):
        _cloud.record_stt("m", {"usage": {"seconds": 30.0}}, 240.0)   # no cost field
        _cloud.record_stt("m", {"duration": 60.0}, 240.0)              # no usage at all
        _cloud.record_stt("m", SimpleNamespace(usage=None, duration=None), 6.0)
    m = _usage_models(tmp_path)["m"]
    assert m["calls"] == 3 and m["cost"] == pytest.approx(0.3 + 0.6 + 0.06)


def test_cloud_stt_records_its_transcription_calls(cfg, no_media, monkeypatch, tmp_path):
    from app import usage

    def tr(kw):
        return {"segments": [{"start": 1.0, "end": 2.0, "text": "你好"}],
                "usage": {"seconds": 10.0, "cost": 0.00008}}

    fc = FakeClient(transcribe=tr)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    with usage.track(tmp_path, phase="gate"):
        CloudSTT().transcribe(Path("a.wav"))
    m = _usage_models(tmp_path)[cloud_stt.DEFAULT_MODEL]
    assert m["calls"] == 2 and m["cost"] == pytest.approx(0.00016)


def test_cached_word_chunks_are_not_charged_again(cfg, monkeypatch, tmp_path):
    from app import usage

    media = tmp_path / "source.wav"
    media.write_bytes(b"RIFF")
    chunk = tmp_path / "c0.mp3"
    chunk.write_bytes(b"ID3")
    fc = FakeClient(transcribe=lambda kw: {**_word_reply([("你", 1.0, 1.2)]),
                                           "usage": {"seconds": 10.0, "cost": 0.0001}})
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    cache, aid = _cloud.cache_for(media)
    job = tmp_path / "job"
    job.mkdir()
    with usage.track(job, phase="gate"):
        for _ in range(2):                                   # the second run hits the cache
            _cloud.cloud_words([(chunk, (0.0, 10.0))], "zh", cache_dir=cache, audio_id=aid)
    assert _usage_models(job)[_cloud.DEFAULT_TIMING_MODEL]["calls"] == 1


# --- a word track that comes back empty for speech (2026-10-28) ------------------------

def test_empty_word_answer_for_speech_is_asked_again_and_not_cached(cfg, tmp_path, monkeypatch):
    """The service once answered a 4-minute chunk full of speech with its text but NO
    words; cached, that left 72 lines on guessed times for good."""
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")
    replies = [{"text": "你好", "words": [], "segments": []},
               _word_reply([("你", 1.0, 1.2), ("好", 1.2, 1.5)])]
    fc = FakeClient(transcribe=lambda kw: replies.pop(0))
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    words = _cloud.cloud_words([(chunk, (0.0, 240.0))], "zh", cache_dir=tmp_path,
                               audio_id="a")
    assert words == [("你", 1.0, 1.2), ("好", 1.2, 1.5)] and len(fc.calls) == 2


def test_still_empty_falls_back_to_segment_times_or_is_not_cached(cfg, tmp_path, monkeypatch):
    chunk = tmp_path / "c.mp3"
    chunk.write_bytes(b"x")
    seg = {"text": "你好", "words": [], "segments": [{"start": 2.0, "end": 3.0, "text": "你好"}]}
    fc = FakeClient(transcribe=lambda kw: seg)
    monkeypatch.setattr(_cloud, "client", lambda: fc)
    words = _cloud.cloud_words([(chunk, (10.0, 20.0))], "zh", cache_dir=tmp_path,
                               audio_id="a")
    assert words == [("你", 12.0, 12.5), ("好", 12.5, 13.0)]       # spread over its segment

    empty = {"text": "你好", "words": [], "segments": []}
    fc2 = FakeClient(transcribe=lambda kw: empty)
    monkeypatch.setattr(_cloud, "client", lambda: fc2)
    other = tmp_path / "b"
    other.mkdir()
    assert _cloud.cloud_words([(chunk, (0.0, 5.0))], "zh", cache_dir=other,
                              audio_id="b") == []
    assert not list(other.glob("words_*.json"))                    # asked again next time

    silent = {"text": "", "words": [], "segments": []}               # real silence: kept
    fc3 = FakeClient(transcribe=lambda kw: silent)
    monkeypatch.setattr(_cloud, "client", lambda: fc3)
    assert _cloud.cloud_words([(chunk, (0.0, 5.0))], "zh", cache_dir=other,
                              audio_id="c") == []
    assert len(fc3.calls) == 1 and len(list(other.glob("words_*.json"))) == 1
