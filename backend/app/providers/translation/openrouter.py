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


class OpenRouter(OpenAICompatTranslator):
    name = "openrouter"
    display_name = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    extra_headers = {"HTTP-Referer": "https://github.com/vizsup", "X-Title": "vizsup"}

    # Process-wide memos (the registry holds one instance). set.add is atomic, and
    # the worst race is one extra probe request — no lock needed.
    _mandatory_reasoning: set[str] = set()   # models that rejected REASONING_OFF
    _dead_promo: set[str] = set()            # promo models that answered "gone"

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
        """Send with reasoning switched off; degrade to minimal reasoning for
        endpoints that insist on it, and to the main model if the promo vanished.
        Both fallbacks are remembered, so each costs at most one extra request."""
        # Worst case is 4 sends: promo(off) → promo(min) → main(off) → main(min). Each
        # fallback adds to a memo set, so the loop cannot cycle.
        for _ in range(4):
            model = kw["model"]
            mandatory = model in self._mandatory_reasoning
            call = dict(kw, extra_body=REASONING_MIN if mandatory else REASONING_OFF)
            if mandatory:
                call["max_tokens"] = kw["max_tokens"] + REASONING_HEADROOM
            try:
                return require_choices(self._client().chat.completions.create(**call))
            except Exception as exc:
                if not mandatory and _reasoning_is_mandatory(exc):
                    self._mandatory_reasoning.add(model)
                    continue
                if model == self.active_promo() and _model_is_gone(exc):
                    self._dead_promo.add(model)
                    log.warning("promo model %s unavailable (%s) — using %s",
                                model, exc, self.main_model())
                    kw = dict(kw, model=self.main_model())
                    continue
                raise
        raise RuntimeError("OpenRouter: request fallbacks exhausted")  # unreachable in practice
