"""API-4: right-size max_tokens and turn silent truncation loud. Without a cap a
repetition-loop reply bills unbounded output; a reply cut off at max_tokens used
to be padded by parse_numbered and pass the count guard, hiding untranslated lines
until the edit gate. Now finish_reason=="length" raises BEFORE parse_numbered.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.providers.translation import _common
from app.providers.translation._common import _completion_cap
from app.providers.translation.deepseek import DeepSeekFlash


def _resp(content: str, finish: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)]
    )


class _StubClient:
    """Records the kwargs of the last create() call; reply is configurable."""

    def __init__(self, reply):
        self.last_kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._reply = reply

    def _create(self, **kw):
        self.last_kwargs = kw
        return self._reply


def _wire(monkeypatch, prov, reply):
    stub = _StubClient(reply)
    monkeypatch.setattr(prov, "_client", lambda: stub)
    return stub


def test_completion_cap_formula():
    assert _completion_cap(50) == 3000        # 60 * 50
    assert _completion_cap(2) == 300          # floor
    assert _completion_cap(1000) == 8192      # ceiling


def test_max_tokens_passed_through(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "k")
    prov = DeepSeekFlash()
    stub = _wire(monkeypatch, prov, _resp("\n".join(f"{i}| vi" for i in range(1, 51))))
    prov.translate_texts([f"cn{i}" for i in range(50)])
    assert stub.last_kwargs["max_tokens"] == 3000

    stub2 = _wire(monkeypatch, prov, _resp("1| a\n2| b"))
    prov.translate_texts(["cn1", "cn2"])
    assert stub2.last_kwargs["max_tokens"] == 300


def test_truncation_raises_before_parse(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "k")
    prov = DeepSeekFlash()
    _wire(monkeypatch, prov, _resp("1| a", finish="length"))

    def _boom(*a, **k):
        raise AssertionError("parse_numbered must not run on a truncated reply")

    monkeypatch.setattr(_common, "parse_numbered", _boom)
    with pytest.raises(RuntimeError, match="truncated"):
        prov.translate_texts(["cn1", "cn2"])


def test_stop_with_missing_line_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "k")
    prov = DeepSeekFlash()
    _wire(monkeypatch, prov, _resp("1| a\n3| c", finish="stop"))
    out = prov.translate_texts(["cn1", "cn2", "cn3"])
    assert out == ["a", "cn2", "c"]           # line 2 falls back to source, no raise
