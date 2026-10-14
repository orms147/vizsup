"""Per-chunk cache for the cloud ASR: a finished chunk is never bought twice.

Only the network edge is faked (``_cloud.client``): every request is recorded, so the
tests count what would have been paid for. No ffmpeg, no network, no real storage — the
job lives in ``tmp_path``.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.config import settings
from app.pipeline.align import _spread
from app.providers.asr import _cloud, audio_llm
from app.providers.asr.audio_llm import AudioLLM

SPANS = [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0), (30.0, 40.0)]


class FakeErr(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


class FakeAPI:
    """Stands in for the OpenAI-compatible client. Each chunk file holds its own name as
    bytes (``chunk0``, ``chunk1``…), so a request says which chunk it carried. ``fail_on``
    makes that chunk fail once, with a non-retryable 401."""

    def __init__(self):
        self.reply = lambda name: f"第{name[-1:].decode()}句。"
        self.fail_on: bytes | None = None
        self.heard: list[bytes] = []     # chunks sent to the transcription model
        self.timed: list[bytes] = []     # chunks sent for word timestamps
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._timing))

    def _chat(self, **kw):
        name = base64.b64decode(kw["messages"][0]["content"][1]["input_audio"]["data"])
        self.heard.append(name)
        if name == self.fail_on:
            self.fail_on = None
            raise FakeErr(401, "bad key")
        text = self.reply(name) if callable(self.reply) else self.reply
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=text))])

    def _timing(self, **kw):
        name = kw["file"].read()
        self.timed.append(name)
        return {"words": [{"word": "第", "start": 1.0, "end": 1.4}]}


@pytest.fixture()
def job(tmp_path, monkeypatch):
    """A job folder holding its source audio; chunking, ffmpeg and alignment stood in for."""
    for k, v in {"stt_api_base_url": "", "stt_api_key": "", "audio_llm_model": "",
                 "openrouter_api_key": "sk-or"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(AudioLLM, "_mandatory_reasoning", set())
    media = tmp_path / "job" / "source.wav"
    media.parent.mkdir()
    media.write_bytes(b"RIFF-v1")

    monkeypatch.setattr(_cloud, "plan_chunks", lambda media, target: SPANS)
    monkeypatch.setattr(_cloud, "local_words_available", lambda: True)   # no word upload
    monkeypatch.setattr(audio_llm, "_voice", lambda media, say: None)
    monkeypatch.setattr(_cloud, "time_groups", lambda media, groups, language, words=None,
                        voice=None: [c for (lo, hi), sents in groups
                                     for c in _spread(sents, lo, hi)])

    @contextmanager
    def chunk_files(media, spans):
        out = tmp_path / "chunks"
        out.mkdir(exist_ok=True)
        paths = []
        for i in range(len(spans)):
            p = out / f"chunk{i}.mp3"
            p.write_bytes(f"chunk{i}".encode())
            paths.append(p)
        yield paths

    monkeypatch.setattr(_cloud, "chunk_files", chunk_files)
    api = FakeAPI()
    monkeypatch.setattr(_cloud, "client", lambda: api)
    return SimpleNamespace(media=media, api=api)


def _timed(cues):
    return [(c.start, c.end, c.text) for c in cues]


def test_a_second_transcribe_reuses_every_chunk(job):
    first_log, second_log = [], []
    first = AudioLLM().transcribe(job.media, log=first_log.append)
    second = AudioLLM().transcribe(job.media, log=second_log.append)
    assert len(job.api.heard) == len(SPANS)            # the second run uploaded nothing
    assert _timed(second) == _timed(first)
    assert not any("Dùng lại" in m for m in first_log)
    assert "↷ Dùng lại 4/4 đoạn đã nghe trước đó (không tốn thêm tiền)" in second_log
    assert (job.media.parent / "asr_cache").is_dir()


def test_a_failed_chunk_is_the_only_one_paid_again(job, monkeypatch):
    # Three chunks: chunk 0 runs alone, chunk 1 runs beside chunk 2, which fails. The
    # result of chunk 1 is collected before chunk 2's error surfaces, so it is stored.
    monkeypatch.setattr(_cloud, "plan_chunks", lambda media, target: SPANS[:3])
    job.api.fail_on = b"chunk2"
    with pytest.raises(FakeErr):
        AudioLLM().transcribe(job.media)
    assert len(list((job.media.parent / "asr_cache").glob("transcript_*.json"))) == 2

    job.api.heard.clear()
    cues = AudioLLM().transcribe(job.media)
    assert job.api.heard == [b"chunk2"]                # only the chunk that never finished
    assert [c.text for c in cues] == ["第0句。", "第1句。", "第2句。"]


def test_a_new_model_or_a_changed_source_invalidates_the_cache(job, monkeypatch):
    AudioLLM().transcribe(job.media)
    monkeypatch.setattr(settings, "audio_llm_model", "other/omni-model")
    AudioLLM().transcribe(job.media)
    assert len(job.api.heard) == 2 * len(SPANS)        # new model → every chunk asked again
    job.media.write_bytes(b"RIFF-v2-a-different-length")   # re-extracted / other audio
    AudioLLM().transcribe(job.media)
    assert len(job.api.heard) == 3 * len(SPANS)


def test_an_empty_transcript_is_a_real_answer_and_is_kept(job):
    job.api.reply = ""
    assert AudioLLM().transcribe(job.media) == []
    assert len(job.api.heard) == len(SPANS)
    assert AudioLLM().transcribe(job.media) == []
    assert len(job.api.heard) == len(SPANS)           # silent chunks are not asked again


def test_cloud_words_cache_gives_identical_words_and_skips_paid_chunks(job, tmp_path):
    items = []
    for i, span in enumerate(SPANS):
        p = tmp_path / f"w{i}.mp3"
        p.write_bytes(f"chunk{i}".encode())
        items.append((p, span))
    plain = _cloud.cloud_words(items, "zh")
    cache = tmp_path / "asr_cache"
    fresh = _cloud.cloud_words(items, "zh", cache_dir=cache, audio_id="source.wav:7:1")
    again = _cloud.cloud_words(items, "zh", cache_dir=cache, audio_id="source.wav:7:1")
    assert fresh == plain == again
    assert fresh[1][0] == "第" and fresh[1][1] == pytest.approx(11.0)   # chunk 1 starts at 10 s
    assert len(job.api.timed) == 2 * len(SPANS)       # the third run sent nothing

    other = tmp_path / "other"
    _cloud.cloud_words(items, "zh", cache_dir=other)  # no audio identity → never stored
    assert not other.exists()


def test_no_audio_on_disk_means_nothing_is_cached(tmp_path):
    assert _cloud.cache_for(tmp_path / "missing" / "source.wav") == (None, "")


def test_chunk_cache_stores_empty_answers_but_never_failures(tmp_path):
    def boom():
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        _cloud.chunk_cache(tmp_path, "transcript", "k", boom)
    assert _cloud.chunk_cache(tmp_path, "transcript", "k", lambda: "ok") == "ok"
    assert _cloud.chunk_cache(tmp_path, "transcript", "e", lambda: "") == ""
    assert _cloud.chunk_cache(tmp_path, "transcript", "e", lambda: pytest.fail("asked twice")) == ""
    assert not list(tmp_path.glob("*.tmp"))           # written via rename, nothing left over


def test_a_damaged_entry_is_recomputed_not_trusted(tmp_path):
    _cloud.chunk_cache(tmp_path, "transcript", "k", lambda: "good")
    next(tmp_path.glob("transcript_*.json")).write_text("{not json", encoding="utf-8")
    assert _cloud.chunk_cache(tmp_path, "transcript", "k", lambda: "again") == "again"
    assert _cloud.chunk_cache(tmp_path, "transcript", "k", lambda: pytest.fail("x")) == "again"


def test_dropped_uploads_are_split_and_the_limit_is_remembered(tmp_path, monkeypatch):
    """2026-10-14: uploads of > ~200 KB to OpenRouter were cut by this network every time
    and ASR died. A dropped chunk is halved and resent; later chunks are cut up front."""
    import httpx

    from app.providers.asr import _cloud

    monkeypatch.setattr(_cloud, "_upload_limit", None)
    cuts = []

    def fake_cut(media, span, out):
        cuts.append(span)
        out.write_bytes(b"x" * int((span[1] - span[0]) * 1000))   # 1 KB per second
        return out

    monkeypatch.setattr(_cloud, "_cut", fake_cut)

    def call(path, span):
        if path.stat().st_size > 70_000:                          # "the network" cuts > 70 KB
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return f"{span[0]:.0f}-{span[1]:.0f}"

    big = tmp_path / "chunk.mp3"
    big.write_bytes(b"x" * 240_000)
    pieces = _cloud.split_on_drop(tmp_path / "src.wav", (0.0, 240.0), big, call)
    assert [r for _s, r in pieces] == ["0-60", "60-120", "120-180", "180-240"]
    assert _cloud._upload_limit is not None and _cloud._upload_limit <= 120_000

    cuts.clear()                                                  # next chunk: cut up front
    nxt = tmp_path / "next.mp3"
    nxt.write_bytes(b"x" * 240_000)
    calls = []
    out = _cloud.split_on_drop(tmp_path / "src.wav", (240.0, 480.0), nxt,
                               lambda p, sp: calls.append(sp) or call(p, sp))
    assert len(out) == 4 and all(sp[1] - sp[0] == 60 for sp in calls)   # no failed big send


def test_server_errors_are_not_mistaken_for_drops(tmp_path):
    import httpx

    from app.providers.asr import _cloud

    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    err = httpx.HTTPStatusError("400", request=req, response=httpx.Response(400, request=req))
    err.status_code = 400
    assert not _cloud.is_upload_drop(err)
    assert _cloud.is_upload_drop(httpx.ReadError("reset"))
