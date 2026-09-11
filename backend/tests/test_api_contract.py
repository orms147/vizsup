"""N-11: contract tests pinning jobs.py to the real pipeline signatures.

The API render path passed a kwarg (`replace_audio`) that assemble had dropped
long ago — every POST /render died with a TypeError AFTER TTS had already run.
These tests make that class of rot impossible: the kwargs builders are bound
against the live signatures, and the whole JobManager state machine is driven
with signature-checking fakes (no media, no network).
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app import jobs
from app.jobs import JobManager, Stage, _render_kwargs
from app.pipeline import assemble as real_assemble
from app.pipeline import tts as real_tts


# --- signature binding -------------------------------------------------------

def test_render_kwargs_bind_to_assemble_signature():
    sig = inspect.signature(real_assemble.assemble)
    for opts in ({}, {"keep_audio": True}, {"audio_mode": "duck", "orig_volume": 0.4},
                 {"cover_hardsubs": True, "font": "Noto Sans", "dub_volume": 1.2}):
        sig.bind(None, [], **_render_kwargs(opts))   # raises TypeError on drift


def test_render_kwargs_audio_mode_resolution():
    assert _render_kwargs({})["audio_mode"] == "replace"
    assert _render_kwargs({"keep_audio": True})["audio_mode"] == "mix"
    assert _render_kwargs({"keep_audio": True, "audio_mode": "duck"})["audio_mode"] == "duck"
    assert _render_kwargs({"audio_mode": None, "keep_audio": True})["audio_mode"] == "mix"


# --- full state machine with signature-checking fakes -------------------------

@pytest.fixture()
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.settings, "vizsup_storage_dir", tmp_path)

    def fake_download(job):
        job.source_video.write_bytes(b"v")
        job.metadata["title"] = "t"

    asr_sig = inspect.signature(jobs.asr.transcribe)

    def fake_transcribe(job, **kw):
        asr_sig.bind(job, **kw)
        job.cn_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

    tr_sig = inspect.signature(jobs.translate.translate)

    def fake_translate(job, **kw):
        tr_sig.bind(job, **kw)
        job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")

    tts_sig = inspect.signature(real_tts.synthesize)

    def fake_synthesize(job, **kw):
        tts_sig.bind(job, **kw)
        return []

    as_sig = inspect.signature(real_assemble.assemble)

    def fake_assemble(job, segments, **kw):
        as_sig.bind(job, segments, **kw)   # the historic rot point
        job.output_video.write_bytes(b"o")
        return job.output_video

    monkeypatch.setattr(jobs.download, "download", fake_download)
    monkeypatch.setattr(jobs.asr, "transcribe", fake_transcribe)
    monkeypatch.setattr(jobs.translate, "translate", fake_translate)
    monkeypatch.setattr(jobs.tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(jobs.assemble, "assemble", fake_assemble)
    return JobManager()


def test_run_until_gate_parks_then_render_completes(manager):
    state = manager.create("j1", url="https://example.com/v", options={"translator": "passthrough"})
    asyncio.run(manager.run_until_gate("j1"))
    assert state.stage == Stage.AWAIT_EDIT, state.error
    assert state.job.vi_srt.exists()
    assert not state.job.output_video.exists()      # human gate: nothing rendered yet

    state.options.update({"keep_audio": True})
    asyncio.run(manager.render("j1"))
    assert state.stage == Stage.DONE, state.error   # was Stage.ERROR (TypeError) before N-11
    assert state.job.output_video.exists()


def test_render_without_vi_srt_errors_cleanly(manager):
    state = manager.create("j2", url="https://example.com/v", options={})
    asyncio.run(manager.render("j2"))
    assert state.stage == Stage.ERROR
    assert "vi.srt" in (state.error or "")


def test_asr_default_resolves_from_settings(manager, monkeypatch):
    seen = {}
    orig = jobs.asr.transcribe

    def spy(job, **kw):
        seen.update(kw)
        return orig(job, **kw)

    monkeypatch.setattr(jobs.asr, "transcribe", spy)
    manager.create("j3", url="u", options={})       # no explicit asr
    asyncio.run(manager.run_until_gate("j3"))
    assert seen["provider"] == jobs.settings.vizsup_default_asr


# --- cancel reaches INSIDE the stages ------------------------------------------
# The API used to check `cancelled` only BETWEEN stages, so hitting cancel during a
# 200-line translate (or a long TTS run) did nothing until the whole stage finished
# — and kept spending provider credits the whole time. These pin the plumbing.
# (The desktop worker has always passed cancel=; the API had not.)

def test_translate_receives_a_live_cancel_callback(manager, monkeypatch):
    seen = {}
    inner = jobs.translate.translate            # the fixture's signature-checking fake

    def spy(job, **kw):
        seen["cancel"] = kw.get("cancel")
        return inner(job, **kw)

    monkeypatch.setattr(jobs.translate, "translate", spy)
    state = manager.create("c1", url="u", options={})
    asyncio.run(manager.run_until_gate("c1"))
    assert state.stage == Stage.AWAIT_EDIT, state.error

    cancel = seen["cancel"]
    assert callable(cancel), "translate must be handed a cancel callback"
    assert cancel() is False        # a live view of the flag, not a snapshot
    state.cancelled = True
    assert cancel() is True


def test_tts_receives_a_live_cancel_callback(manager, monkeypatch):
    seen = {}
    inner = jobs.tts.synthesize

    def spy(job, **kw):
        seen["cancel"] = kw.get("cancel")
        return inner(job, **kw)

    monkeypatch.setattr(jobs.tts, "synthesize", spy)
    state = manager.create("c2", url="u", options={})
    asyncio.run(manager.run_until_gate("c2"))
    asyncio.run(manager.render("c2"))

    cancel = seen["cancel"]
    assert callable(cancel)
    assert cancel() is False
    state.cancelled = True
    assert cancel() is True


def test_mid_batch_translate_cancel_reports_cancelled_not_failed(manager, monkeypatch):
    """TranslateCancelled means "user cancelled; completed batches are checkpointed" —
    it must not surface as a generic stage failure."""

    def boom(job, **kw):
        raise jobs.translate.TranslateCancelled("cancelled mid-batch")

    monkeypatch.setattr(jobs.translate, "translate", boom)
    state = manager.create("c3", url="u", options={})
    asyncio.run(manager.run_until_gate("c3"))

    assert state.stage == Stage.ERROR
    assert state.error == "cancelled"      # not "TranslateCancelled: cancelled mid-batch"
    assert not state.job.vi_srt.exists()


# --- thin FastAPI validation check ---------------------------------------------

def test_create_job_requires_url_or_file():
    fastapi = pytest.importorskip("fastapi")  # noqa: F841 - base dep, but be CI-safe
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/jobs", json={})
    assert r.status_code == 400
