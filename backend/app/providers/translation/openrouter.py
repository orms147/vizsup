"""OpenRouter — one API key, access to virtually every model. OpenAI-compatible.
Each call names a ROLE (draft / refine / brief / …); the model per role comes from
app.providers.roles (Settings → AI và mô hình, else the benchmarked default). A
time-boxed promo model (settings.openrouter_promo_*) replaces the DRAFT role only,
until its expiry date.
"""
from __future__ import annotations

import logging
from datetime import date

from app.config import settings
from app.providers.translation._common import OpenAICompatTranslator

log = logging.getLogger(__name__)

# The draft role's default (2026-10-13 benchmark; see app/providers/roles.py).
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"

# Subtitle translation needs no chain-of-thought — and on OpenRouter, reasoning
# tokens count against max_tokens, while _completion_cap budgets ~60 tokens/line
# of VISIBLE text. Measured 2026-10-01 on a 50-line batch (cap 3000):
#   deepseek-v4.1-flash, no reasoning param -> finish=length, cut at line 34
#   deepseek-v4.1-flash, effort "none"      -> 968 tokens, 50/50 lines, 2x faster
#   qwen-plus (non-reasoning), effort "none" -> param ignored, unchanged
REASONING_OFF = {"reasoning": {"effort": "none"}}
# Some endpoints refuse to switch it off (stealth/space-bunny-alpha answers 400
# "Reasoning is mandatory"). Then use the smallest effort and widen the cap for the
# hidden tokens: minimal still spent ~1.3k on that batch, and ~200 on a 4-line one,
# which alone would blow the 300-token floor.
REASONING_MIN = {"reasoning": {"effort": "minimal"}}
REASONING_HEADROOM = 2048

# Which HOST serves the model. One id is sold by many hosts at very different prices:
# deepseek-v4.1-flash by ~40 (2026-10-31: 0.013–0.6 $/M in, 0.14–2.4 out, half of them
# 4-bit). OpenRouter's default balancing put a real 50-line draft batch on Wafer (mxfp4)
# or CoreWeave for $0.00046–0.00071; sort=price alone ranks by the INPUT price and took a
# 4-bit host with 0.53 $/M output ($0.00043); with 4/6-bit hosts left out it took an fp8
# host for $0.00015–0.00018 — a third — and two blind judges scored all seven samples
# within noise (docs/plan/G-cost-log.md). One stable host also lets prompt caching hit.
# Hosts that state no quantisation ("unknown": the model's own vendor — OpenAI, Alibaba,
# DeepSeek) stay eligible. OPENROUTER_ROUTING=auto restores OpenRouter's own choice.
QUANT_OK = ["int8", "fp8", "fp16", "bf16", "fp32", "unknown"]
ROUTE_PRICE = {"provider": {"sort": "price", "quantizations": QUANT_OK}}


def routing_on() -> bool:
    return (settings.openrouter_routing or "").strip().lower() != "auto"


class EmptyReply(RuntimeError):
    """HTTP 200 with no ``choices`` — OpenRouter relays an upstream provider failure
    this way. Carries no status code on purpose: every retry ladder in the app treats
    status-less errors as transient. Seen 2026-10-01 on the stealth model: the brief
    pre-pass died with "TypeError: 'NoneType' object is not subscriptable"."""


def require_choices(resp):
    """Pass a chat completion through, or raise EmptyReply with the relayed error."""
    if getattr(resp, "choices", None):
        return resp
    extra = getattr(resp, "model_extra", None) or {}
    err = extra.get("error") if isinstance(extra, dict) else None
    detail = (err.get("message") if isinstance(err, dict) else err) or "no choices in reply"
    raise EmptyReply(f"OpenRouter returned an empty reply ({detail})")


def _status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None),
                                                         "status_code", None)


def _reasoning_is_mandatory(exc: Exception) -> bool:
    return _status(exc) == 400 and "reasoning is mandatory" in str(exc).lower()


def _model_is_gone(exc: Exception) -> bool:
    """Free/stealth models are routinely pulled, sometimes before their stated date."""
    if _status(exc) not in (400, 404):
        return False
    msg = str(exc).lower()
    return any(s in msg for s in ("not a valid model", "no endpoints found",
                                  "model not found", "has been deprecated", "expired"))


def _no_host(exc: Exception) -> bool:
    """No host matched the routing filter — 404 "No endpoints found for the request with
    quantization: …" (2026-10-31). A pulled model answers alike, so the caller first
    retries WITHOUT the filter; only a failure then means the model is gone."""
    return _status(exc) in (400, 404) and "no endpoints found" in str(exc).lower()


def _host(resp) -> str | None:
    """The host that served a reply (OpenRouter's top-level ``provider`` field)."""
    extra = getattr(resp, "model_extra", None) or {}
    h = extra.get("provider") if isinstance(extra, dict) else None
    return h if isinstance(h, str) and h else None


class OpenRouter(OpenAICompatTranslator):
    name = "openrouter"
    display_name = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    extra_headers = {"HTTP-Referer": "https://github.com/vizsup", "X-Title": "vizsup"}

    # Process-wide memos (the registry holds one instance). set.add is atomic, and
    # the worst race is one extra probe request — no lock needed.
    _mandatory_reasoning: set[str] = set()   # models that rejected REASONING_OFF
    _dead_promo: set[str] = set()            # promo models that answered "gone"
    _unroutable: set[str] = set()            # models no host matched ROUTE_PRICE for

    @property
    def api_key(self) -> str:  # type: ignore[override]
        return settings.openrouter_api_key

    # --- model selection --------------------------------------------------
    def active_promo(self, today: date | None = None) -> str | None:
        """The promo model if it is set, not yet expired, and not known-dead."""
        m = settings.openrouter_promo_model.strip()
        if not m or m in self._dead_promo:
            return None
        exp = settings.openrouter_promo_expires.strip()
        if exp:
            try:
                expires = date.fromisoformat(exp)
            except ValueError:
                # a typo'd date must not keep a free model in use forever
                log.warning("OPENROUTER_PROMO_EXPIRES=%r is not YYYY-MM-DD; promo ignored", exp)
                return None
            if (today or date.today()) >= expires:
                return None
        return m

    def main_model(self) -> str:
        """The draft model without the promo (where a vanished promo falls back to)."""
        from app.providers import roles

        return roles.model_for("draft")

    def model_for(self, role: str) -> str:  # type: ignore[override]
        from app.providers import roles

        if role == "draft":
            return self.active_promo() or self.main_model()
        return roles.model_for(role)

    @property
    def model(self) -> str:  # type: ignore[override]
        return self.model_for("draft")

    @property
    def cost_note(self) -> str:  # type: ignore[override]
        draft, refine = self.model_for("draft"), self.model_for("refine")
        note = f"1 key · nháp {draft.split('/')[-1]} · tinh chỉnh {refine.split('/')[-1]}"
        until = settings.openrouter_promo_expires.strip()
        if self.active_promo() and until:
            note += f" (khuyến mãi tới {until}, sau đó {self.main_model().split('/')[-1]})"
        return note

    # --- request shaping --------------------------------------------------
    def _chat(self, **kw):
        """Send with reasoning switched off and the cheapest non-4-bit host (ROUTE_PRICE);
        degrade to minimal reasoning for endpoints that insist on it, to OpenRouter's own
        host choice when no host matches the filter, and to the main model if the promo
        vanished. Every fallback is remembered, so each costs at most one extra request."""
        # Worst case is 6 sends: promo(off) → promo(min) → promo(min, any host) → main(…)
        # likewise. Each fallback adds to a memo set, so the loop cannot cycle.
        for _ in range(6):
            model = kw["model"]
            mandatory = model in self._mandatory_reasoning
            routed = routing_on() and model not in self._unroutable
            body = dict(REASONING_MIN if mandatory else REASONING_OFF)
            if routed:
                body.update(ROUTE_PRICE)
            call = dict(kw, extra_body=body)
            if mandatory:
                call["max_tokens"] = kw["max_tokens"] + REASONING_HEADROOM
            try:
                resp = require_choices(self._client().chat.completions.create(**call))
                from app import usage

                usage.record(model, getattr(resp, "usage", None), host=_host(resp))  # E-14
                return resp
            except Exception as exc:
                if not mandatory and _reasoning_is_mandatory(exc):
                    self._mandatory_reasoning.add(model)
                    continue
                if routed and _no_host(exc):
                    self._unroutable.add(model)
                    log.info("no host matches the price routing for %s — OpenRouter picks", model)
                    continue
                if model == self.active_promo() and _model_is_gone(exc):
                    self._dead_promo.add(model)
                    log.warning("promo model %s unavailable (%s) — using %s",
                                model, exc, self.main_model())
                    kw = dict(kw, model=self.main_model())
                    continue
                raise
        raise RuntimeError("OpenRouter: request fallbacks exhausted")  # unreachable in practice
