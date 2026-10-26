"""API-10: batch TTS path (tts._synthesize_batch). A batch-capable provider
synthesizes the whole job on one event loop; the pipeline persists the manifest and
builds the segment list from the provider's on_done callbacks. Out-of-order
completions must stay crash-safe (index-keyed manifest), a fatal must persist the
successes before raising, and a cancel must return a partial list WITHOUT raising.
The sequential fallback (providers without supports_batch) is covered unchanged by
test_tts_resume.py.
"""
from __future__ import annotations

import json

import pytest

from app.models import Job
from app.pipeline import tts as tts_mod
from app.pipeline.tts import synthesize

VI = ("1\n00:00:00,000 --> 00:00:01,000\ncâu một\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\ncâu hai\n\n"
      "3\n00:00:02,000 --> 00:00:03,000\ncâu ba\n")


@pytest.fixture()
def job(tmp_path):
    j = Job(id="t", work_dir=tmp_path / "job")
    j.vi_srt.write_text(VI, encoding="utf-8")
    return j


def _use(monkeypatch, fake):
    monkeypatch.setattr(tts_mod, "get_tts", lambda name: fake)
    return fake


class FakeBatchTTS:
    """Drives on_done in a scripted (key, exc) order; snapshots the on-disk manifest
    after each callback so a test can prove writes are incremental/crash-safe."""

    supports_batch = True

    def __init__(self, manifest_path, script):
        self.manifest_path = manifest_path
        self.script = script            # list of (key, exc)
        self.snapshots: list[list[str]] = []

    def available(self):
        return True

    def synthesize(self, *a, **k):      # the batch path must never call this
        raise AssertionError("batch provider should use synthesize_many")

    def synthesize_many(self, items, *, voice=None, rate=None, concurrency=3,
                        retry_delays=(1.0, 4.0), on_done=None, cancel=None):
        by_key = {k: (k, t, p) for k, t, p in items}
        out = []
        for key, exc in self.script:
            k, t, p = by_key[key]
            if exc is None:
                p.write_bytes(b"mp3" + t.encode())
            on_done(k, exc)
            self.snapshots.append(
                sorted(json.loads(self.manifest_path.read_text(encoding="utf-8"))))
            out.append((k, exc))
        return out


def _mpath(job):
    return job.tts_dir / "manifest.json"


def test_out_of_order_completion_incremental_and_ordered(job, monkeypatch):
    fake = _use(monkeypatch, FakeBatchTTS(
        _mpath(job), script=[("3", None), ("1", None), ("2", None)]))
    segs = synthesize(job, provider="fake")
    assert [c.index for c, _ in segs] == [1, 2, 3]        # returned in cue order
    assert fake.snapshots == [["3"], ["1", "3"], ["1", "2", "3"]]   # each write persisted
    assert sorted(json.loads(_mpath(job).read_text(encoding="utf-8"))) == ["1", "2", "3"]


def test_fatal_persists_successes_then_raises(job, monkeypatch):
    _use(monkeypatch, FakeBatchTTS(
        _mpath(job), script=[("1", None), ("3", None), ("2", RuntimeError("boom"))]))
    with pytest.raises(RuntimeError, match="boom"):
        synthesize(job, provider="fake")
    assert sorted(json.loads(_mpath(job).read_text(encoding="utf-8"))) == ["1", "3"]


def test_cancel_returns_partial_without_exception(job, monkeypatch):
    _use(monkeypatch, FakeBatchTTS(
        _mpath(job), script=[("1", None), ("2", InterruptedError()), ("3", InterruptedError())]))
    segs = synthesize(job, provider="fake")               # no exception
    assert [c.index for c, _ in segs] == [1]              # partial, cue 1 only
    assert sorted(json.loads(_mpath(job).read_text(encoding="utf-8"))) == ["1"]


def test_muted_cue_popped_and_cached_kept(job, monkeypatch):
    # first full run dubs 1,2,3
    _use(monkeypatch, FakeBatchTTS(
        _mpath(job), script=[("1", None), ("2", None), ("3", None)]))
    synthesize(job, provider="fake")
    # mute cue 2, re-run: nothing to synthesize (1,3 cached), 2 popped from manifest
    (job.work_dir / "overrides.json").write_text(
        json.dumps([{}, {"mute": True}, {}]), encoding="utf-8")
    fake2 = _use(monkeypatch, FakeBatchTTS(_mpath(job), script=[]))   # empty todo
    segs = synthesize(job, provider="fake")
    assert [c.index for c, _ in segs] == [1, 3]
    assert fake2.snapshots == []                          # synthesize_many not needed
    assert "2" not in json.loads(_mpath(job).read_text(encoding="utf-8"))


def test_keep_alive_connector_survives_session_close():
    """Regression for the API-10 shared-connector bug: edge_tts opens one
    ClientSession(connector_owner=True) per save(), which closes a PLAIN shared
    connector after the first segment. _keep_alive_connector must survive that and
    only close on an explicit shutdown() (network-free — no request is made)."""
    import asyncio

    aiohttp = pytest.importorskip("aiohttp")
    from app.providers.tts.edge import _keep_alive_connector

    async def _probe():
        plain = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=plain):
            pass
        assert plain.closed is True                      # a plain pool is torn down

        keep = _keep_alive_connector(3)
        async with aiohttp.ClientSession(connector=keep):
            pass
        assert keep.closed is False                      # ours survives for the next cue
        await keep.shutdown()
        assert keep.closed is True                       # closed for real at the end

    asyncio.run(_probe())


def test_unvoiceable_line_skipped_in_batch(job, monkeypatch):
    class NoAudioReceived(RuntimeError):
        pass

    _use(monkeypatch, FakeBatchTTS(
        _mpath(job),
        script=[("1", None), ("2", NoAudioReceived("no audio")), ("3", None)]))
    logs: list[str] = []
    segs = synthesize(job, provider="fake", log=logs.append)
    assert [c.index for c, _ in segs] == [1, 3]           # line 2 skipped, no raise
    assert any("không phát âm được" in m for m in logs)
    assert "2" not in json.loads(_mpath(job).read_text(encoding="utf-8"))


def test_progress_counts_cache_hits_then_each_line(job, monkeypatch):
    """A 717-line dub takes minutes; the render page shows "d/n câu" instead of a bar
    frozen at one value."""
    _use(monkeypatch, FakeBatchTTS(_mpath(job), script=[("1", None), ("2", None), ("3", None)]))
    synthesize(job, provider="fake")
    seen = []
    # second run: line 2 changed → 2 cache hits, one new line
    job.vi_srt.write_text(VI.replace("câu hai", "câu hai mới"), encoding="utf-8")
    _use(monkeypatch, FakeBatchTTS(_mpath(job), script=[("2", None)]))
    synthesize(job, provider="fake", progress=lambda d, n: seen.append((d, n)))
    assert seen == [(2, 3), (3, 3)]
