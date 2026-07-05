"""Shared prompt building + robust parsing for LLM subtitle translation.

Lines are numbered ``n| text`` so the model must return the same count in the
same order — far more reliable than free-form JSON. Timecodes never reach the
model (the pipeline strips and re-attaches them).
"""
from __future__ import annotations

import re
import threading

import httpx

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

REFINE_SYSTEM_PROMPT = (
    "You are a senior Vietnamese subtitle editor doing a SECOND-PASS polish.\n"
    "For each line you get the Chinese source and a first-pass Vietnamese draft.\n"
    "Rewrite each Vietnamese line into natural, fluent spoken Vietnamese with the right\n"
    "register, staying faithful to the Chinese meaning and fixing any mistranslation.\n"
    "Rules:\n"
    "- Keep each line roughly the same length as the source (it must fit on screen and be dubbable).\n"
    "- Preserve the exact line numbering and count. Output ONLY lines of the form `n| <vietnamese>`.\n"
    "- Do not merge, split, reorder, add, or drop lines. No commentary, no extra text.\n"
    "- Keep proper nouns / brand names consistent; follow the glossary if given.\n"
    "- If a draft line is already good, keep it.\n"
    "- If a draft line is annotated with a word budget, also shorten it to fit while "
    "keeping the meaning."
)

PROOFREAD_SYSTEM_PROMPT = (
    "You are correcting OCR errors in Chinese subtitle lines read off a video.\n"
    "Each line may have character-recognition mistakes: wrong/similar-looking hanzi,\n"
    "stray Latin letters or symbols, or missing punctuation. Repair ONLY obvious OCR\n"
    "errors so each line reads as correct, natural Chinese.\n"
    "Rules:\n"
    "- DO NOT translate. Keep the language as-is (a real inserted English line stays English).\n"
    "- Only fix mis-recognized characters — do not rephrase, summarize, add, or drop content.\n"
    "- Preserve the exact line numbering and count. Output ONLY lines of the form `n| <text>`.\n"
    "- If a line is already correct, output it unchanged. No commentary, no extra text."
)

OCR_HINT = (
    "Note: the Chinese lines were read off video frames by OCR and may contain "
    "misrecognized characters (wrong/similar-looking hanzi, stray symbols) — "
    "translate the intended meaning, not the garbled surface form."
)

_LINE = re.compile(r"^\s*(\d+)\s*\|\s?(.*)$")

# One HTTP client per (api_key, base_url) is cached on the instance (registry
# providers are module-level singletons). max_retries=0 is deliberate: the app's
# translate._retry is the SINGLE retry authority — letting the SDK also retry
# would stack to ~9 wire attempts per batch. Explicit timeouts replace the SDK's
# 600 s read default (one hung batch could stall ~90 min).
_CLIENT_LOCK = threading.Lock()
_CLIENT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0)


def _completion_cap(n_lines: int) -> int:
    """Right-size max_tokens. Subtitle lines run ~15-35 completion tokens incl.
    numbering; 60/line is ~2x headroom. Floor 300 protects tiny batches; cap 8192
    is the common provider ceiling. Bounds a repetition-loop reply's output cost."""
    return max(300, min(8192, 60 * n_lines))


def _raise_if_truncated(resp, cap: int) -> None:
    """A reply cut off at max_tokens is a silent-truncation trap: parse_numbered
    pre-fills the fallback so it always returns n lines and the count guard passes
    — untranslated lines would surface only at the edit gate. Raise instead so the
    batch rides the retry ladder (temperature-0.3 loops are stochastic) and, if it
    persists, fails loud with the checkpoint intact."""
    if resp.choices[0].finish_reason == "length":
        raise RuntimeError(
            f"Provider reply truncated at max_tokens={cap} — "
            "batch will be retried, not silently padded"
        )


def build_proofread_prompt(texts: list[str], *, context: dict | None = None) -> str:
    parts: list[str] = []
    if context:
        meta = ", ".join(f"{k}: {v}" for k, v in sorted(context.items())
                         if v and not k.startswith("_"))
        if meta:
            parts.append(f"Video context: {meta}")
    parts.append("Correct OCR errors in each numbered Chinese line. Reply only as `n| <text>`:\n")
    parts.append("\n".join(f"{i}| {t}" for i, t in enumerate(texts, 1)))
    return "\n".join(parts)


def build_refine_prompt(
    cn_texts: list[str],
    vi_texts: list[str],
    *,
    context: dict | None = None,
    glossary: dict[str, str] | None = None,
) -> str:
    """Pair each Chinese source line with its Vietnamese draft for the refine pass.
    The source is echoed as ``[n] …`` so it can't be mistaken for the required
    ``n| <vietnamese>`` answer format (only the latter is parsed back)."""
    parts: list[str] = []
    if context:
        meta = ", ".join(f"{k}: {v}" for k, v in sorted(context.items())
                         if v and not k.startswith("_"))
        if meta:
            parts.append(f"Video context: {meta}")
    if glossary:
        gl = "; ".join(f"{k} -> {v}" for k, v in sorted(glossary.items()))
        parts.append(f"Glossary (zh -> vi): {gl}")
    if context and context.get("_ocr_source"):
        parts.append(OCR_HINT)
    parts.append("Polish each Vietnamese draft against its Chinese source. "
                 "Reply with only the improved Vietnamese as `n| <vietnamese>`:\n")
    # API-8: an overlong draft line carries a word budget so the refiner shortens it
    # WHILE polishing; fitting lines (budget None/absent) render exactly as before.
    budgets = context.get("_budgets") if context else None
    body = []
    for i, (zh, vi) in enumerate(zip(cn_texts, vi_texts), 1):
        b = budgets[i - 1] if budgets and i - 1 < len(budgets) else None
        draft_line = (f"[{i}] nháp (quá dài — rút còn tối đa ~{b} từ): {vi}" if b
                      else f"[{i}] nháp: {vi}")
        body.append(f"[{i}] 中文: {zh}\n{draft_line}")
    parts.append("\n".join(body))
    return "\n".join(parts)


def build_user_prompt(
    texts: list[str],
    *,
    context: dict | None = None,
    glossary: dict[str, str] | None = None,
) -> str:
    parts: list[str] = []
    if context:
        meta = ", ".join(f"{k}: {v}" for k, v in sorted(context.items())
                         if v and not k.startswith("_"))
        if meta:
            parts.append(f"Video context: {meta}")
    if glossary:
        gl = "; ".join(f"{k} -> {v}" for k, v in sorted(glossary.items()))
        parts.append(f"Glossary (zh -> vi): {gl}")
    if context and context.get("_ocr_source"):
        parts.append(OCR_HINT)
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

    can_refine = True      # real editor pass (refine_texts below)
    can_proofread = True   # real OCR-fix pass (proofread_texts below)
    parallel_batches = 4   # cached thread-safe client + own retry ladder → fan out (API-9)
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    extra_headers: dict | None = None  # e.g. OpenRouter HTTP-Referer / X-Title
    _client_cache: dict | None = None  # {(api_key, base_url): OpenAI} — per instance

    def available(self) -> bool:
        return bool(self.api_key) and _installed("openai")

    def _client(self):
        """One cached OpenAI client per (api_key, base_url). api_key is a live
        settings-backed property, so the Settings dialog changing a key mints a
        fresh client instead of reusing a stale one."""
        from openai import OpenAI

        key = (self.api_key, self.base_url)
        cache = self._client_cache
        if cache is not None and key in cache:
            return cache[key]
        with _CLIENT_LOCK:
            if self._client_cache is None:
                self._client_cache = {}
            client = self._client_cache.get(key)
            if client is None:
                client = OpenAI(
                    api_key=self.api_key, base_url=self.base_url,
                    default_headers=self.extra_headers or None,
                    max_retries=0, timeout=_CLIENT_TIMEOUT,
                )
                self._client_cache[key] = client
            return client

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
        cap = _completion_cap(len(texts))
        resp = self._client().chat.completions.create(
            model=self.model,
            temperature=0.3,
            max_tokens=cap,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(texts, context=context, glossary=glossary)},
            ],
        )
        _raise_if_truncated(resp, cap)
        return parse_numbered(resp.choices[0].message.content or "", len(texts), texts)

    def refine_texts(self, cn_texts, vi_texts, *, context=None, glossary=None):
        if not vi_texts:
            return []
        cap = _completion_cap(len(vi_texts))
        resp = self._client().chat.completions.create(
            model=self.model,
            temperature=0.3,
            max_tokens=cap,
            messages=[
                {"role": "system", "content": REFINE_SYSTEM_PROMPT},
                {"role": "user",
                 "content": build_refine_prompt(cn_texts, vi_texts, context=context, glossary=glossary)},
            ],
        )
        _raise_if_truncated(resp, cap)
        # fallback is the draft (vi_texts), so a parse miss keeps the draft, not the source
        return parse_numbered(resp.choices[0].message.content or "", len(vi_texts), vi_texts)

    def proofread_texts(self, texts, *, context=None):
        if not texts:
            return []
        cap = _completion_cap(len(texts))
        resp = self._client().chat.completions.create(
            model=self.model,
            temperature=0.2,
            max_tokens=cap,
            messages=[
                {"role": "system", "content": PROOFREAD_SYSTEM_PROMPT},
                {"role": "user", "content": build_proofread_prompt(texts, context=context)},
            ],
        )
        _raise_if_truncated(resp, cap)
        # fallback is the original OCR text, so a parse miss keeps it (never blanks a line)
        return parse_numbered(resp.choices[0].message.content or "", len(texts), texts)

    def rewrite(self, text: str, instruction: str, *, max_tokens: int = 300) -> str:
        """Single-shot rewrite (e.g. shorten a VI line to fit the dub slot).
        ``max_tokens`` lets a batched caller (API-5) size the cap to its chunk."""
        resp = self._client().chat.completions.create(
            model=self.model,
            temperature=0.4,
            max_tokens=max_tokens,
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
