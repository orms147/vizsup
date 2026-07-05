"""API-1: one HTTP client per (api_key, base_url), cached on the provider
instance, built with max_retries=0 (app-level _retry is the single retry ladder)
and explicit timeouts (no 600 s SDK default). A key change mints a fresh client.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.providers.translation.deepl import DeepLTranslator
from app.providers.translation.deepseek import DeepSeekFlash


def _resp(content: str, finish: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)]
    )


class _RecordingOpenAI:
    """Records constructor kwargs and how many times it was built."""

    built: list["_RecordingOpenAI"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _RecordingOpenAI.built.append(self)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: _resp("1| vi"))
        )


@pytest.fixture(autouse=True)
def _reset():
    _RecordingOpenAI.built = []
    yield


@pytest.fixture
def _fake_openai(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", _RecordingOpenAI)


def test_client_built_once_per_instance(monkeypatch, _fake_openai):
    monkeypatch.setattr(settings, "deepseek_api_key", "k1")
    prov = DeepSeekFlash()
    prov.translate_texts(["你好"])
    prov.translate_texts(["再见"])
    assert len(_RecordingOpenAI.built) == 1     # cached across calls


def test_client_kwargs_have_no_retry_and_finite_timeout(monkeypatch, _fake_openai):
    monkeypatch.setattr(settings, "deepseek_api_key", "k1")
    DeepSeekFlash().translate_texts(["你好"])
    kw = _RecordingOpenAI.built[0].kwargs
    assert kw["max_retries"] == 0
    assert kw["timeout"].read == 120.0          # not the SDK's 600 s default


def test_key_change_mints_new_client(monkeypatch, _fake_openai):
    prov = DeepSeekFlash()
    monkeypatch.setattr(settings, "deepseek_api_key", "k1")
    prov.translate_texts(["你好"])
    monkeypatch.setattr(settings, "deepseek_api_key", "k2")   # Settings dialog changed the key
    prov.translate_texts(["你好"])
    assert len(_RecordingOpenAI.built) == 2
    assert _RecordingOpenAI.built[0].kwargs["api_key"] == "k1"
    assert _RecordingOpenAI.built[1].kwargs["api_key"] == "k2"


class _RecordingHttpxClient:
    built: list["_RecordingHttpxClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _RecordingHttpxClient.built.append(self)

    def post(self, url, **kw):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"translations": [{"text": "vi"}]},
        )


def test_deepl_reuses_one_httpx_client(monkeypatch):
    _RecordingHttpxClient.built = []
    import httpx
    monkeypatch.setattr(httpx, "Client", _RecordingHttpxClient)
    monkeypatch.setattr(settings, "deepl_api_key", "dk")
    prov = DeepLTranslator()
    prov.translate_texts(["你好"])
    prov.translate_texts(["再见"])
    assert len(_RecordingHttpxClient.built) == 1
    assert _RecordingHttpxClient.built[0].kwargs.get("timeout") == 60
