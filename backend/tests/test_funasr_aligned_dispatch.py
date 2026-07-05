"""ENG-3: the fused ASR's phase-2 must PREFER whisperX forced alignment when it's
importable (and FunASR sentences are available), but always fall back to the
faster-whisper baseline — on absence, on no sentences, or on a whisperX error — so
the provider can never regress below FunASR-only. The real models aren't installed;
this locks the DISPATCH logic with the model calls stubbed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.asr import funasr_aligned as fa


@pytest.fixture()
def stub(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(fa, "_faster_whisper_words",
                        lambda media, lang: (calls.append("baseline"), [])[1])
    monkeypatch.setattr(fa, "_whisperx_words",
                        lambda media, lang, sents: (calls.append("whisperx"), [])[1])
    return calls


def _run(sentences):
    return fa.FunASRAligned._word_timing(Path("x.wav"), "zh", sentences=sentences)


def test_baseline_when_whisperx_absent(stub, monkeypatch):
    monkeypatch.setattr(fa, "_installed", lambda m: False)
    _run([object()])
    assert stub == ["baseline"]


def test_whisperx_when_installed_with_sentences(stub, monkeypatch):
    monkeypatch.setattr(fa, "_installed", lambda m: True)
    _run([object()])
    assert stub == ["whisperx"]


def test_baseline_when_no_sentences(stub, monkeypatch):
    monkeypatch.setattr(fa, "_installed", lambda m: True)
    _run(None)                                   # nothing to align → baseline
    assert stub == ["baseline"]


def test_whisperx_failure_falls_back_to_baseline(monkeypatch):
    calls: list[str] = []

    def boom(media, lang, sents):
        calls.append("whisperx")
        raise RuntimeError("align failed")

    monkeypatch.setattr(fa, "_installed", lambda m: True)
    monkeypatch.setattr(fa, "_whisperx_words", boom)
    monkeypatch.setattr(fa, "_faster_whisper_words",
                        lambda media, lang: (calls.append("baseline"), [])[1])
    _run([object()])
    assert calls == ["whisperx", "baseline"]     # tried whisperx, degraded to baseline
