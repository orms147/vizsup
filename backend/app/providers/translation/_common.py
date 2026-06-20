"""Shared prompt building + robust parsing for LLM subtitle translation.

Lines are numbered ``n| text`` so the model must return the same count in the
same order — far more reliable than free-form JSON. Timecodes never reach the
model (the pipeline strips and re-attaches them).
"""
from __future__ import annotations

import re

from app.providers.base import TranslationProvider, _installed

SYSTEM_PROMPT = (
    "You are a professional Chinese-to-Vietnamese subtitle translator for short videos.\n"
    "Rules:\n"
    "- Translate into natural, spoken Vietnamese with the right register for the content.\n"
    "- Keep each line roughly the same length as the source (it must fit on screen and be dubbable).\n"
    "- Preserve the exact line numbering and count. Output ONLY lines of the form `n| <vietnamese>`.\n"
    "- Do not merge, split, reorder, add, or drop lines. No commentary, no extra text.\n"
    "- Keep proper nouns / brand names consistent; follow the glossary if given."
)

_LINE = re.compile(r"^\s*(\d+)\s*\|\s?(.*)$")


def build_user_prompt(
    texts: list[str],
    *,
    context: dict | None = None,
    glossary: dict[str, str] | None = None,
) -> str:
    parts: list[str] = []
    if context:
        meta = ", ".join(f"{k}: {v}" for k, v in context.items() if v)
        if meta:
            parts.append(f"Video context: {meta}")
    if glossary:
        gl = "; ".join(f"{k} -> {v}" for k, v in glossary.items())
        parts.append(f"Glossary (zh -> vi): {gl}")
    parts.append("Translate every numbered line to Vietnamese:\n")
    parts.append("\n".join(f"{i}| {t}" for i, t in enumerate(texts, 1)))
    return "\n".join(parts)


def parse_numbered(reply: str, n: int, fallback: list[str]) -> list[str]:
    """Parse ``n| text`` lines back into order. Missing lines fall back to source."""
    out = list(fallback)
    for line in reply.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if 0 <= idx < n:
            out[idx] = m.group(2).strip()
    return out


class OpenAICompatTranslator(TranslationProvider):
    """Base for OpenAI-compatible chat endpoints (OpenRouter, DeepSeek, Qwen, ...)."""

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    extra_headers: dict | None = None  # e.g. OpenRouter HTTP-Referer / X-Title

    def available(self) -> bool:
        return bool(self.api_key) and _installed("openai")

    def _client(self):
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, base_url=self.base_url,
                      default_headers=self.extra_headers or None)

    def translate_texts(
        self,
        texts,
        *,
        source_lang="zh",
        target_lang="vi",
        context=None,
        glossary=None,
    ):
        if not texts:
            return []
        resp = self._client().chat.completions.create(
            model=self.model,
            temperature=0.3,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(texts, context=context, glossary=glossary)},
            ],
        )
        return parse_numbered(resp.choices[0].message.content or "", len(texts), texts)

    def rewrite(self, text: str, instruction: str) -> str:
        """Single-shot rewrite (e.g. shorten a VI line to fit the dub slot)."""
        resp = self._client().chat.completions.create(
            model=self.model,
            temperature=0.4,
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    def list_models(self) -> list[str]:
        """GET {base_url}/models (works for OpenRouter / DeepSeek / Qwen-compatible)."""
        import httpx

        headers = dict(self.extra_headers or {})
        if self.api_key:  # some endpoints (e.g. OpenRouter /models) are public — don't send "Bearer "
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = httpx.get(self.base_url.rstrip("/") + "/models", headers=headers, timeout=20)
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data") or payload.get("models") or []
        ids = [(m.get("id") or m.get("name")) for m in data if isinstance(m, dict)]
        return sorted(x for x in ids if x)

    def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "Chưa nhập API key."
        try:
            n = len(self.list_models())
            return True, f"OK · {n} model khả dụng"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
