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

# Shared by the draft and refine passes so the two can never drift apart.
_FORMAT_RULES = (
    "Output format (strict):\n"
    "- Preserve the exact line numbering and count. Output ONLY lines of the form `n| <vietnamese>`.\n"
    "- Do not merge, split, reorder, add, or drop lines. No commentary, notes, or extra text.\n"
    "- One sentence may be spread over several numbered lines: translate each line so the lines\n"
    "  still read naturally in sequence, keeping each line's content on its own line.\n"
    "- Each line appears on screen at the moment it is spoken. NEVER pull words from a later\n"
    "  line into an earlier one (it gives away the next line — a joke's punchline, a reveal —\n"
    "  before the speaker says it), and never push a line's content into the next one.\n"
    "- Inside a line keep the Chinese clause order: where the Chinese line has clauses separated\n"
    "  by ，/。/？/！, write the Vietnamese clauses in the same order, separated the same way.\n"
    "- Never fall behind or run ahead: the names, numbers and key nouns of line n's Vietnamese\n"
    "  come from Chinese line n. A line may repeat the tail of the previous one — that part was\n"
    "  already rendered there; translate only what is new in this line.\n"
    "- A line whose Chinese is \"—\", empty, exactly the previous line again, or unreadable\n"
    "  noise (stray characters read off the picture, not a sentence): output `n| …` and fill it\n"
    "  with nothing — never invent a sentence from noise.\n"
    "- Noise INSIDE a line (stray characters or digits around a readable clause, e.g.\n"
    "  \"135 完了 我撞人了\"): translate only the readable clause; never turn the noise into a\n"
    "  name, place, number or action.\n"
)

_TRANSLATION_RULES = (
    "How to translate:\n"
    "- Natural, everyday SPOKEN Vietnamese — what a Vietnamese creator would actually say.\n"
    "  Avoid stiff word-for-word Sino-Vietnamese (\"văn dịch\"); prefer common words.\n"
    "- Pronouns (xưng hô): Chinese 我/你/他/我们 must become the Vietnamese pronouns that fit\n"
    "  the speaker, the listener and their relationship (mình / tôi / em / anh / chị / bạn /\n"
    "  các bạn / mọi người …). Decide once from the context and keep them CONSISTENT for the\n"
    "  whole video. A creator talking to viewers usually says \"mình\" and \"các bạn\" / \"mọi người\".\n"
    "- Names: Chinese person and place names take their Hán-Việt reading (小王 → Tiểu Vương,\n"
    "  张总 → Tổng Trương, 成都 → Thành Đô). Keep Latin-script brands and names as written.\n"
    "  When a glossary is given, follow it exactly.\n"
    "- Internet slang, memes and idioms → a natural Vietnamese equivalent with the same effect,\n"
    "  never a literal gloss.\n"
    "- Sentence-final particles (啊/吧/呢/哦/嘛) → Vietnamese particles (nhé, nha, đấy, à, mà)\n"
    "  only where they sound natural; do not overuse them.\n"
    "- Brevity: every line is shown on screen AND voiced by a Vietnamese dub in the same time\n"
    "  slot. Drop filler (那个, 就是, 然后…) and keep each line about as short as the source.\n"
    "  Meaning first, then brevity.\n"
    "- Keep numbers, prices and units accurate (元 → tệ). Keep English words from the source.\n"
    "- Laughter / onomatopoeia → natural Vietnamese (哈哈哈 → Haha).\n"
    "- Keep proper nouns / brand names consistent across all lines.\n"
    "- Speaker first: decide who speaks to whom before choosing pronouns; choose by the\n"
    "  listener's gender and relation — never \"anh\"/\"chú\" for a woman or girl. Use matching\n"
    "  pairs only (tao–mày, tôi–cô/cậu/anh, em–anh, tớ–cậu); never mix them (tao–cô). Never add\n"
    "  kinship words (con/chú/cháu/bác/thầy) the Chinese does not imply.\n"
    "- A pair works in both directions: when A says \"em\" to B's \"anh\", B says \"anh\" to A's\n"
    "  \"em\". Adjacent lines of the same speaker keep the same self-pronoun.\n"
    "- Keep the logic: a negation stays a negation (我也不强制你们… → \"tôi cũng không ép các\n"
    "  cô…\", never an invitation), a condition without 如果 stays a condition (现在不X，等下别Y →\n"
    "  \"giờ mà không X thì lát nữa đừng hòng Y\"), a question stays a question, and a thought\n"
    "  said to nobody addresses nobody.\n"
    "- The Chinese comes from OCR / speech recognition and can be wrong: a line that reverses\n"
    "  what the speaker plainly means (我同意 in the middle of an objection — a dropped 不), or a\n"
    "  name written with a homophone, is translated the way the surrounding lines and the\n"
    "  glossary make it right.\n"
    "- The voice-over reads every character: never write abbreviations it cannot say (ĐM, vcl,\n"
    "  vl …) — use the spoken words (Mẹ kiếp, Chết tiệt …). A known pop-culture nickname keeps\n"
    "  its known name (祖国人 → Homelander).\n"
    "- A sentence that continues from the previous line starts in lowercase.\n"
)

SYSTEM_PROMPT = (
    "You are a professional Chinese-to-Vietnamese subtitle translator for short videos\n"
    "(Douyin / Bilibili). Your lines are shown on screen and read aloud by a Vietnamese voice-over.\n\n"
    + _FORMAT_RULES + "\n" + _TRANSLATION_RULES
)

REFINE_SYSTEM_PROMPT = (
    "You are a senior Vietnamese subtitle editor doing a SECOND-PASS polish of Chinese-to-\n"
    "Vietnamese subtitles for short videos. For each line you get the Chinese source and a\n"
    "first-pass Vietnamese draft. Rewrite each draft into the best natural spoken Vietnamese,\n"
    "faithful to the Chinese meaning: fix mistranslations, wrong or inconsistent xưng hô, and\n"
    "stiff phrasing. If a draft line is already good, keep it. If a draft line is annotated\n"
    "with a word budget, also shorten it to fit while keeping the meaning.\n"
    "VERIFY BEFORE POLISHING: check that the draft's names, numbers and key nouns belong to\n"
    "THIS line's Chinese. A draft that belongs to a neighbouring line (it lags or runs ahead)\n"
    "is discarded and the Chinese line translated afresh — never polish a mismatched draft.\n"
    "CHECK THE PRONOUNS: for every line decide speaker → listener, compare the draft's pair\n"
    "with the brief / series sheet (in both directions) and rewrite any line that deviates —\n"
    "the draft's xưng hô is the most frequent error you are here to fix.\n"
    "Do not add jokes, emphasis or details the Chinese lacks; keep a joke's target (whose\n"
    "sausage, whose brain) when the Chinese has one.\n\n"
    + _FORMAT_RULES + "\n" + _TRANSLATION_RULES
)

# Per-video brief (pipeline/brief.py) — appended to the SYSTEM prompt, not the user
# turn: it is constant across every batch of one video, so the cacheable prefix stays
# byte-identical within the video, and system position gives it instruction weight.
_BRIEF_HEADER = (
    "\nVIDEO BRIEF — written for THIS video. Follow it, especially for xưng hô, names and\n"
    "tone. Where it records the user's own notes, those are facts and override your guesses:\n"
)


LYRICS_HINT = (
    "\nTHIS VIDEO IS MOSTLY SUNG. Lines that are song lyrics must read as natural, singable\n"
    "Vietnamese lyrics: keep the imagery and each line's rhythm, keep repeated lines (the\n"
    "chorus) worded identically every time, no explanations, no added words. Spoken\n"
    "comments between songs stay conversational.\n"
)


_SERIES_HEADER = (
    "\nTHIS VIDEO IS ONE EPISODE OF A SERIES. Names, xưng hô and tone are FIXED across "
    "episodes — follow this sheet (the brief above adds what is specific to this episode):\n")
_PREV_HEADER = (
    "\nHOW THE PREVIOUS EPISODE ENDED (approved Vietnamese lines — for continuity of names, "
    "pronouns and tone only; do NOT translate or repeat them):\n")


def system_prompt_for(base: str, context: dict | None) -> str:
    """``base`` plus the per-video brief when one rides in ``context['_brief']``, plus
    the lyrics rule when ``context['_lyrics']`` (E-8: the listening model heard singing)."""
    ctx = context or {}
    out = base
    if ctx.get("_lyrics"):
        out += LYRICS_HINT
    if ctx.get("_style"):                       # E-7: rules learnt from the user's edits
        out += ("\nTHE USER'S OWN STYLE (learnt from their corrections — follow it unless "
                "the brief says otherwise):\n" + str(ctx["_style"]).strip() + "\n")
    brief = ctx.get("_brief")
    if brief and str(brief).strip():
        out += _BRIEF_HEADER + str(brief).strip() + "\n"
    if ctx.get("_series") and str(ctx["_series"]).strip():       # Bộ phim: fixed across episodes
        out += _SERIES_HEADER + str(ctx["_series"]).strip() + "\n"
    prev = [str(x).strip() for x in (ctx.get("_prev") or []) if str(x).strip()]
    if prev:
        out += _PREV_HEADER + "\n".join(f"- {x}" for x in prev) + "\n"
    return out

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
# the word budget echoed back ("im hết! *(6 từ)*", Luna 2026-11-01) — never part of a subtitle
_BUDGET_ECHO = re.compile(r"\s*\*?\(\s*(?:tối đa\s*)?~?\s*\d+\s*từ\s*\)\*?\s*$", re.IGNORECASE)

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


def _ranges(nums: list[int]) -> str:
    """[1, 2, 3, 5] → "1-3, 5". Pure."""
    out: list[str] = []
    for n in nums:
        if out and n == int(out[-1].split("-")[-1]) + 1:
            out[-1] = out[-1].split("-")[0] + f"-{n}"
        else:
            out.append(str(n))
    return ", ".join(out)


def voice_hint(context: dict | None, n: int) -> str:
    """The "who speaks" line for a batch of ``n`` lines from ``context['_voices']`` (one
    ``m`` / ``f`` / None per line, ``voices.line_hints``); "" when absent or misaligned."""
    tags = (context or {}).get("_voices")
    if not tags or len(tags) != n:
        return ""
    man = [i for i, t in enumerate(tags, 1) if t == "m"]
    woman = [i for i, t in enumerate(tags, 1) if t == "f"]
    if not man and not woman:
        return ""
    heard = "; ".join(f"{label}: {_ranges(nums)}" for label, nums in
                      (("a man's voice", man), ("a woman's voice", woman)) if nums)
    return ("Voice heard on each line (measured from the audio's pitch — usually right; lines "
            f"not listed are unsure): {heard}. Use it to tell who is speaking, and so which "
            "xưng hô pair applies; when a line's own words plainly say otherwise, trust the words.")


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
    voices = voice_hint(context, len(cn_texts))
    if voices:
        parts.append(voices)
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
    voices = voice_hint(context, len(texts))
    if voices:
        parts.append(voices)
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
            out[idx] = _BUDGET_ECHO.sub("", m.group(2)).strip()
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
        resp = self._chat(
            model=self.model_for("draft"),
            temperature=0.3,
            max_tokens=cap,
            messages=[
                {"role": "system", "content": system_prompt_for(SYSTEM_PROMPT, context)},
                {"role": "user", "content": build_user_prompt(texts, context=context, glossary=glossary)},
            ],
        )
        _raise_if_truncated(resp, cap)
        return parse_numbered(resp.choices[0].message.content or "", len(texts), texts)

    def refine_texts(self, cn_texts, vi_texts, *, context=None, glossary=None):
        if not vi_texts:
            return []
        cap = _completion_cap(len(vi_texts))
        resp = self._chat(
            model=self.model_for("refine"),
            temperature=0.3,
            max_tokens=cap,
            messages=[
                {"role": "system", "content": system_prompt_for(REFINE_SYSTEM_PROMPT, context)},
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
        resp = self._chat(
            model=self.model_for("proofread"),
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

    def model_for(self, role: str) -> str:
        """Model for a job role (draft/refine/proofread/brief/terms/shorten/qa/vision).
        Single-model vendors ignore the role; OpenRouter maps it (app.providers.roles)."""
        return self.model

    def _chat(self, **kw):
        """The ONE place a chat completion is sent. Subclasses override this to add
        endpoint-specific request shaping (OpenRouter: reasoning control + model
        fallback) without touching the four prompt builders that call it."""
        return self._client().chat.completions.create(**kw)

    def rewrite(self, text: str, instruction: str, *, max_tokens: int = 300,
                role: str = "shorten") -> str:
        """Single-shot rewrite (e.g. shorten a VI line to fit the dub slot).
        ``max_tokens`` lets a batched caller (API-5) size the cap to its chunk;
        ``role`` picks the model (brief / terms / shorten / qa …)."""
        resp = self._chat(
            model=self.model_for(role),
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
