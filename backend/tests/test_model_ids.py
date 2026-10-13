"""N-12: guard against re-introducing legacy model ids (CLAUDE.md: the
deepseek-chat/deepseek-reasoner API deprecates 2026-07-24; ids are web-verified
at change time, this test just stops silent regressions)."""
from __future__ import annotations

from app.providers.registry import TRANSLATORS
from app.providers.translation.openrouter import DEFAULT_MODEL

_DEPRECATED = ("deepseek-chat", "deepseek-reasoner")


def test_openrouter_default_not_legacy():
    assert not any(bad in DEFAULT_MODEL for bad in _DEPRECATED), DEFAULT_MODEL
    assert DEFAULT_MODEL == "qwen/qwen3.7-plus"   # benchmarked + listed on OpenRouter 2026-10-13


def test_no_role_default_uses_deprecated_ids():
    from app.providers.roles import ROLES

    for r in ROLES.values():
        assert not any(bad in r.default for bad in _DEPRECATED), r


def test_no_registered_default_uses_deprecated_ids(monkeypatch):
    # class-level defaults only — a user's OPENROUTER_MODEL is their business
    from app.config import settings

    monkeypatch.setattr(settings, "openrouter_model", "")
    monkeypatch.setattr(settings, "zhipu_model", "")
    for name, p in TRANSLATORS.items():
        model = getattr(p, "model", "") or ""
        for bad in _DEPRECATED:
            assert bad not in model, f"{name} still defaults to a {bad} id"


def test_glm_model_override_via_settings(monkeypatch):
    from app.config import settings

    glm = TRANSLATORS["glm"]
    monkeypatch.setattr(settings, "zhipu_model", "")
    assert glm.model == "glm-5.2"                          # web-verified 2026-07-02
    monkeypatch.setattr(settings, "zhipu_model", "glm-6-test")
    assert glm.model == "glm-6-test"
