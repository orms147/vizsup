"""OpenRouter: reasoning control, the time-boxed promo model, and its fallbacks.

Both models the user runs are reasoning models, and on OpenRouter reasoning
tokens count against max_tokens. With no reasoning param, a 50-line batch of
deepseek-v4.1-flash was cut off at line 34 (measured 2026-10-01) — the pipeline's
per-line completion cap assumes visible text only. These pin the request shaping
that fixes it, plus the promo-model expiry, with a scripted fake client (no network).
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.config import settings
from app.providers.translation import openrouter as orm
from app.providers.translation.openrouter import (
    REASONING_HEADROOM,
    REASONING_MIN,
    REASONING_OFF,
    OpenRouter,
)


class FakeErr(Exception):
    def __init__(self, status: int, msg: str) -> None:
        super().__init__(msg)
        self.status_code = status


def ok(text: str = "1| xin chào"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=text))])


class FakeClient:
    """Records every create() call; `script(call)` returns a response or raises."""

    def __init__(self, script) -> None:
        self.calls: list[dict] = []
        self._script = script
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        return self._script(kw)


MANDATORY = FakeErr(400, "Error code: 400 - Reasoning is mandatory for this endpoint "
                         "and cannot be disabled.")
GONE = FakeErr(404, "No endpoints found for stealth/space-bunny-alpha.")


@pytest.fixture()
def prov(monkeypatch):
    monkeypatch.setattr(OpenRouter, "_mandatory_reasoning", set())
    monkeypatch.setattr(OpenRouter, "_dead_promo", set())
    monkeypatch.setattr(OpenRouter, "_unroutable", set())
    # the reasoning / promo tests below compare whole request bodies; host routing has its
    # own section at the end ("price routing"), which switches it back on
    monkeypatch.setattr(settings, "openrouter_routing", "auto")
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(settings, "model_draft", "deepseek/deepseek-v4.1-flash")   # draft role
    monkeypatch.setattr(settings, "openrouter_promo_model", "")
    monkeypatch.setattr(settings, "openrouter_promo_expires", "")
    return OpenRouter()


def use(monkeypatch, prov, script) -> FakeClient:
    client = FakeClient(script)
    monkeypatch.setattr(prov, "_client", lambda: client)
    return client


# --- promo model selection ----------------------------------------------------

def test_no_promo_uses_the_main_model(prov):
    assert prov.model == "deepseek/deepseek-v4.1-flash"


def test_falls_back_to_the_default_when_nothing_is_configured(prov, monkeypatch):
    monkeypatch.setattr(settings, "model_draft", "")
    assert prov.model == orm.DEFAULT_MODEL


@pytest.mark.parametrize("today, expected", [
    (date(2026, 10, 1), "stealth/space-bunny-alpha"),
    (date(2026, 10, 4), "stealth/space-bunny-alpha"),     # last free day
    (date(2026, 10, 5), "deepseek/deepseek-v4.1-flash"),  # OpenRouter's expiration_date
    (date(2026, 11, 1), "deepseek/deepseek-v4.1-flash"),
])
def test_promo_is_used_strictly_before_its_expiry(prov, monkeypatch, today, expected):
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/space-bunny-alpha")
    monkeypatch.setattr(settings, "openrouter_promo_expires", "2026-10-05")
    assert (prov.active_promo(today) or prov.main_model()) == expected


def test_promo_without_an_expiry_stays_on(prov, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/x")
    assert prov.model == "stealth/x"


def test_a_typod_expiry_disables_the_promo_rather_than_running_it_forever(prov, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/x")
    monkeypatch.setattr(settings, "openrouter_promo_expires", "05/10/2026")
    assert prov.model == "deepseek/deepseek-v4.1-flash"


def test_cost_note_tells_the_user_what_happens_after_expiry(prov, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/x")
    monkeypatch.setattr(settings, "openrouter_promo_expires", "2999-01-01")
    note = prov.cost_note
    assert "nháp x" in note and "2999-01-01" in note and "deepseek-v4.1-flash" in note


# --- reasoning control --------------------------------------------------------

def test_reasoning_is_switched_off_by_default(prov, monkeypatch):
    client = use(monkeypatch, prov, lambda call: ok())
    prov.translate_texts(["你好"])
    assert client.calls[0]["extra_body"] == REASONING_OFF
    assert client.calls[0]["model"] == "deepseek/deepseek-v4.1-flash"


def test_mandatory_reasoning_degrades_to_minimal_with_headroom(prov, monkeypatch):
    def script(call):
        if call["extra_body"] == REASONING_OFF:
            raise MANDATORY
        return ok()

    client = use(monkeypatch, prov, script)
    prov.translate_texts(["你好"])

    assert [c["extra_body"] for c in client.calls] == [REASONING_OFF, REASONING_MIN]
    assert client.calls[1]["max_tokens"] == client.calls[0]["max_tokens"] + REASONING_HEADROOM


def test_the_mandatory_fallback_is_remembered(prov, monkeypatch):
    """Only the first batch pays the probe; parallel batches 2..N go straight to minimal."""
    def script(call):
        if call["extra_body"] == REASONING_OFF:
            raise MANDATORY
        return ok()

    client = use(monkeypatch, prov, script)
    prov.translate_texts(["一"])
    n = len(client.calls)
    prov.translate_texts(["二"])
    assert len(client.calls) == n + 1
    assert client.calls[-1]["extra_body"] == REASONING_MIN


def test_other_errors_propagate_untouched(prov, monkeypatch):
    """429/5xx belong to translate.py's retry ladder — not swallowed here."""
    def script(call):
        raise FakeErr(429, "rate limited")

    client = use(monkeypatch, prov, script)
    with pytest.raises(FakeErr, match="rate limited"):
        prov.translate_texts(["你好"])
    assert len(client.calls) == 1


def test_an_unrelated_400_is_not_mistaken_for_mandatory_reasoning(prov, monkeypatch):
    def script(call):
        raise FakeErr(400, "context length exceeded")

    use(monkeypatch, prov, script)
    with pytest.raises(FakeErr, match="context length"):
        prov.translate_texts(["你好"])
    assert OpenRouter._mandatory_reasoning == set()


# --- promo vanishing mid-job --------------------------------------------------

def test_a_vanished_promo_falls_back_to_the_main_model_and_sticks(prov, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/space-bunny-alpha")

    def script(call):
        if call["model"] == "stealth/space-bunny-alpha":
            raise GONE
        return ok()

    client = use(monkeypatch, prov, script)
    prov.translate_texts(["你好"])
    assert [c["model"] for c in client.calls] == ["stealth/space-bunny-alpha",
                                                  "deepseek/deepseek-v4.1-flash"]
    assert prov.model == "deepseek/deepseek-v4.1-flash"     # sticky for the process

    prov.translate_texts(["再见"])
    assert client.calls[-1]["model"] == "deepseek/deepseek-v4.1-flash"
    assert len(client.calls) == 3                            # no re-probe of the dead promo


def test_worst_case_chain_still_succeeds(prov, monkeypatch):
    """promo needs reasoning → promo then vanishes → main ALSO needs reasoning."""
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/p")

    def script(call):
        if call["extra_body"] == REASONING_OFF:
            raise MANDATORY
        if call["model"] == "stealth/p":
            raise GONE
        return ok()

    client = use(monkeypatch, prov, script)
    assert prov.translate_texts(["你好"]) == ["xin chào"]
    assert [(c["model"], c["extra_body"]) for c in client.calls] == [
        ("stealth/p", REASONING_OFF),
        ("stealth/p", REASONING_MIN),
        ("deepseek/deepseek-v4.1-flash", REASONING_OFF),
        ("deepseek/deepseek-v4.1-flash", REASONING_MIN),
    ]


def test_a_gone_error_for_the_main_model_is_not_swallowed(prov, monkeypatch):
    """Fallback is for the promo only — a broken MAIN model must fail loudly."""
    def script(call):
        raise GONE

    use(monkeypatch, prov, script)
    with pytest.raises(FakeErr):
        prov.translate_texts(["你好"])


# --- every prompt path goes through the seam ---------------------------------

@pytest.mark.parametrize("call", [
    lambda p: p.translate_texts(["你好"]),
    lambda p: p.refine_texts(["你好"], ["xin chào"]),
    lambda p: p.proofread_texts(["你好"]),
    lambda p: p.rewrite("xin chào bạn", "rút gọn"),
])
def test_all_four_prompt_paths_get_reasoning_control(prov, monkeypatch, call):
    client = use(monkeypatch, prov, lambda c: ok())
    call(prov)
    assert client.calls[0]["extra_body"] == REASONING_OFF


def test_other_openai_compatible_providers_send_no_openrouter_params(monkeypatch):
    """`reasoning` is an OpenRouter extension — DeepSeek's own API must not get it."""
    from app.providers.registry import TRANSLATORS

    ds = TRANSLATORS["deepseek"]
    client = FakeClient(lambda c: ok())
    monkeypatch.setattr(ds, "_client", lambda: client)
    ds.translate_texts(["你好"])
    assert "extra_body" not in client.calls[0]


# --- an HTTP 200 with no choices is a transient error, not a TypeError ---------

def test_empty_choices_raise_a_status_less_transient_error(prov, monkeypatch):
    """OpenRouter relays upstream failures as 200 + no choices; resp.choices[0] used to
    blow up as TypeError far from the cause. EmptyReply carries no status, so every
    retry ladder treats it as transient."""
    empty = SimpleNamespace(choices=None, model_extra={"error": {"message": "upstream 502"}})
    use(monkeypatch, prov, lambda call: empty)
    with pytest.raises(orm.EmptyReply, match="upstream 502") as exc:
        prov.translate_texts(["你好"])
    assert getattr(exc.value, "status_code", None) is None


def test_translate_ladder_retries_an_empty_reply(prov, monkeypatch):
    from app.pipeline import translate

    replies = iter([SimpleNamespace(choices=[], model_extra={}), ok("1| xin chào")])
    use(monkeypatch, prov, lambda call: next(replies))
    out = translate._retry(lambda: prov.translate_texts(["你好"]), sleep=lambda s: None)
    assert out == ["xin chào"]


# --- E-2: one model per role ----------------------------------------------------

def test_each_call_uses_its_roles_model(prov, monkeypatch):
    from app.providers import roles

    monkeypatch.setattr(settings, "model_refine", "")
    client = use(monkeypatch, prov, lambda call: ok())
    prov.translate_texts(["你好"])
    prov.refine_texts(["你好"], ["xin chào"])
    prov.rewrite("xin chào bạn", "rút gọn", role="brief")
    assert [c["model"] for c in client.calls] == [
        "deepseek/deepseek-v4.1-flash",              # draft: explicit MODEL_DRAFT
        roles.ROLES["refine"].default,               # refine: benchmarked default
        roles.ROLES["brief"].default,
    ]


def test_the_promo_only_replaces_the_draft(prov, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/x")
    monkeypatch.setattr(settings, "model_refine", "moonshotai/kimi-k3")
    assert prov.model_for("draft") == "stealth/x"
    assert prov.model_for("refine") == "moonshotai/kimi-k3"


def test_the_legacy_single_model_setting_is_no_longer_read(prov, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_model", "deepseek/deepseek-v4.1-flash")
    monkeypatch.setattr(settings, "model_draft", "")
    assert prov.model_for("draft") == orm.DEFAULT_MODEL


# --- price routing: the cheapest host that is not 4-bit (2026-10-31 cost pass) -------

NO_HOST = FakeErr(404, "No endpoints found for the request with quantization: fp8, bf16. "
                       "To learn more about provider routing, visit: https://openrouter.ai/docs")


@pytest.fixture()
def routed(prov, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_routing", "")      # the default
    return prov


def test_every_call_asks_for_the_cheapest_non_4bit_host_by_default(routed, monkeypatch):
    client = use(monkeypatch, routed, lambda call: ok())
    routed.translate_texts(["你好"])
    routed.rewrite("xin chào bạn", "rút gọn", role="qa")
    for call in client.calls:
        body = call["extra_body"]
        assert body["reasoning"] == REASONING_OFF["reasoning"]           # still switched off
        assert body["provider"]["sort"] == "price"
        quants = set(body["provider"]["quantizations"])
        assert not quants & {"int4", "fp4", "mxfp4", "nvfp4", "fp6"}
        assert "unknown" in quants         # the vendors' own hosts state no quantisation


def test_auto_leaves_the_host_to_openrouter(routed, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_routing", "auto")
    client = use(monkeypatch, routed, lambda call: ok())
    routed.translate_texts(["你好"])
    assert client.calls[0]["extra_body"] == REASONING_OFF


def test_no_matching_host_falls_back_to_openrouters_choice_and_sticks(routed, monkeypatch):
    def script(call):
        if "provider" in call["extra_body"]:
            raise NO_HOST
        return ok()

    client = use(monkeypatch, routed, script)
    assert routed.translate_texts(["你好"]) == ["xin chào"]
    assert ["provider" in c["extra_body"] for c in client.calls] == [True, False]
    routed.translate_texts(["再见"])                          # remembered: no second probe
    assert len(client.calls) == 3 and "provider" not in client.calls[-1]["extra_body"]
    assert OpenRouter._dead_promo == set()       # "no endpoints" here is not a dead model


def test_a_vanished_promo_still_falls_back_with_routing_on(routed, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/space-bunny-alpha")

    def script(call):
        if call["model"] == "stealth/space-bunny-alpha":
            raise GONE
        return ok()

    client = use(monkeypatch, routed, script)
    assert routed.translate_texts(["你好"]) == ["xin chào"]
    assert [(c["model"], "provider" in c["extra_body"]) for c in client.calls] == [
        ("stealth/space-bunny-alpha", True),       # gone looks like "no host" at first…
        ("stealth/space-bunny-alpha", False),      # …asked plainly it is still gone
        ("deepseek/deepseek-v4.1-flash", True),    # the main model keeps the price routing
    ]
    assert "stealth/space-bunny-alpha" in OpenRouter._dead_promo


def test_worst_case_chain_with_routing_still_succeeds(routed, monkeypatch):
    """promo needs reasoning → promo vanished → main needs reasoning → no host matches main."""
    monkeypatch.setattr(settings, "openrouter_promo_model", "stealth/p")

    def script(call):
        body = call["extra_body"]
        if body["reasoning"] == REASONING_OFF["reasoning"]:
            raise MANDATORY
        if call["model"] == "stealth/p":
            raise GONE
        if "provider" in body:
            raise NO_HOST
        return ok()

    client = use(monkeypatch, routed, script)
    assert routed.translate_texts(["你好"]) == ["xin chào"]
    assert [(c["model"].split("/")[0], c["extra_body"]["reasoning"]["effort"],
             "provider" in c["extra_body"]) for c in client.calls] == [
        ("stealth", "none", True), ("stealth", "minimal", True), ("stealth", "minimal", False),
        ("deepseek", "none", True), ("deepseek", "minimal", True), ("deepseek", "minimal", False),
    ]


def test_a_gone_main_model_still_fails_loudly_with_routing_on(routed, monkeypatch):
    def script(call):
        raise GONE

    client = use(monkeypatch, routed, script)
    with pytest.raises(FakeErr):
        routed.translate_texts(["你好"])
    assert len(client.calls) == 2


def test_the_serving_host_is_recorded_per_model(routed, monkeypatch, tmp_path):
    import json

    from app import usage

    def reply(call):
        r = ok()
        r.model_extra = {"provider": "Morph"}
        r.usage = {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0001}
        return r

    use(monkeypatch, routed, reply)
    with usage.track(tmp_path, phase="gate"):
        routed.translate_texts(["你好"])
        routed.translate_texts(["再见"])
    m = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))["models"]
    assert m["deepseek/deepseek-v4.1-flash"]["hosts"] == {"Morph": 2}
