# Workstream A — API Optimization (latency + token cost)

**Goal:** cut the two waits the user actually feels — *Start → edit gate* and *render-click → MP4*
— and cut token spend, with special attention to the **OCR path** (videos that already carry
burned-in Chinese subtitles: the dominant use case; noisy source text, more cues, a proofread
stage that ASR jobs don't have).

**Provenance:** every task below was produced by a 3-area code audit (network call surface,
OCR-path token economics, remaining work) on 2026-07-02 and then **adversarially verified against
the code** — premises confirmed, file:line citations checked, "already implemented" ruled out by
grep. Two tasks carry verifier amendments folded in (API-9 cancel contract, API-10 amendments 1–2).
Line numbers cite the 2026-07-02 tree (HEAD `14784d3`); re-locate by symbol name if drifted.

## Verified external facts (cited in specs below)

- **openai SDK 2.43.0 defaults** (installed): `DEFAULT_MAX_RETRIES=2`,
  `DEFAULT_TIMEOUT=Timeout(connect=5, read=600, write=600, pool=600)`. So today one hung batch can
  stall ~600 s per SDK attempt × 3 SDK attempts × 3 app attempts ≈ **90 min worst case**.
- **DeepSeek context caching is automatic** ("Context Caching on Disk"): prefix must be
  **byte-identical from token 0** and **≥ 1024 tokens** to hit; hits bill ~2 % of miss price.
  Source: https://api-docs.deepseek.com/guides/kv_cache and /news/news0802. Implication: our
  ~200-token system prompt alone never hits; a run with a sizeable glossary/context prefix does —
  IF the prefix is byte-stable (API-3).
- **edge-tts is an unofficial Microsoft endpoint** with a history of 403 blocks
  (github.com/rany2/edge-tts issue #290). Concurrency must stay conservative (≤3) with the
  existing retry ladder. edge-tts **7.2.8** (installed) supports a shared `aiohttp` `connector=`
  param on `Communicate` — but `backend/pyproject.toml` pins `edge-tts>=6.1`, so API-10 must bump
  the pin to `>=7`.

## Dependency graph & phases

```
Phase 1 (foundations, independent, S/M):   API-1  API-2(→1)  API-3  API-4
Phase 2 (cheap wins, S/M):                 API-5(→4)  API-6  API-7(→3)
Phase 3 (big rocks, L):                    API-8(→3,5)  API-9(→1,2,6)  API-10(independent)
```
API-6, API-8, API-9 all touch `refine_pass` — land in that order and rebase deliberately.

---

## API-1 [M] Cache one HTTP client per provider + explicit timeouts + single retry authority

**Premise (verified):** `OpenAICompatTranslator._client()` builds a **new `OpenAI()` per call**
(`backend/app/providers/translation/_common.py:133-137`), invoked from `translate_texts` (:150),
`refine_texts` (:163), `proofread_texts` (:178), `rewrite` (:191). Each pays TCP+TLS (~50–300 ms)
and leaks its pool (never `.close()`d); the retry lambda in `translate.py:141-143` builds yet
another client per attempt. Same per-call construction in `claude.py:33,48,62`, `gemini.py:30,40,49`;
DeepL uses bare `httpx.post` (`deepl.py:26`). No call sets a timeout → SDK default 600 s read.
Registry providers are module-level singletons (`registry.py:24-38`), so instance-level caching is
safe and long-lived. `api_key` is a live settings-backed `@property` (e.g. `deepseek.py:17-19`) that
the Settings dialog can change at runtime → cache must key on credentials.

**Change:**
1. `_common.py` — replace `_client()` with a credential-keyed cache:
   module-level `_CLIENT_LOCK = threading.Lock()`; key `(self.api_key, self.base_url)`;
   double-checked build of
   `OpenAI(api_key=…, base_url=…, default_headers=self.extra_headers or None, max_retries=0,
   timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0))` stored in
   `self._client_cache`. `max_retries=0` makes the app-level `_retry`
   (`translate.py:69-78`) the ONLY retry ladder (today: up to 9 wire attempts/batch).
   **Do NOT set max_tokens here** — API-4 owns output caps.
2. `glm.py:36-39` chat-ping fallback: `self._client().with_options(timeout=15.0)` so a dead GLM
   endpoint fails the Settings "Test kết nối" in 15 s, not 10 min.
3. `claude.py` / `gemini.py`: cache their SDK clients the same way (keyed on api_key); give Gemini
   an explicit request timeout via its config object (google-genai has NO client timeout by
   default) — these providers are dormant (SDKs not installed) so tests must monkeypatch.
4. `deepl.py`: one cached `httpx.Client(timeout=60)` per instance instead of bare `httpx.post`.

**Gain:** 5–15 s off the pre-gate wait on a typical job (6–40 calls); worst-case hung-batch stall
90 min → ≤ 6.2 min (−93 %); Settings test buttons unfreeze in ≤15 s.
**Risk:** stale client after key change — mitigated by the credential key.
**Verify:** new `backend/tests/test_client_reuse.py`: monkeypatch `openai.OpenAI` with a recording
fake — (a) two `translate_texts` calls on one instance → exactly 1 construction; (b) constructor
kwargs contain `max_retries=0` and `timeout.read == 120`; (c) flip
`settings.deepseek_api_key` → second construction; (d) DeepL: one `httpx.Client` across calls.

---

## API-2 [S] Retry-ladder intelligence: fail fast on non-transient, honor Retry-After on 429

**Premise (verified):** `_retry` (`translate.py:69-78`) retries ALL exceptions blindly with fixed
2 s/8 s — a bad API key (401) burns ~10 s and 3 paid duplicate requests before erroring; no code
anywhere reads `Retry-After` (grep: zero hits). Depends on API-1 (`max_retries=0`), else the SDK
still hides 429s from this layer.

**Change (single function rewrite, keep signature `_retry(fn, *, sleep)` — tests inject sleep):**
- Module helpers: `_NON_RETRYABLE = {400, 401, 403, 404, 422}`;
  `_status(exc) = getattr(exc, "status_code", None)` (openai/anthropic `APIStatusError` both expose
  it — duck-typing keeps translate.py free of SDK imports);
  `_retry_after(exc)` reads `exc.response.headers["retry-after"]` guarded, clamped to
  `min(30.0, value)`, else None.
- Body: `for delay in (*_RETRY_DELAYS, None): try: return fn(); except Exception as exc:
  if delay is None or _status(exc) in _NON_RETRYABLE: raise;
  sleep(max(delay, _retry_after(exc) or 0.0))`.
- **Do NOT touch `_synth_retry` in `tts.py:72-80`** — edge-tts 403 is the transient error that
  ladder exists for and carries no Retry-After; add a comment saying so.

**Invariants preserved:** exactly 3 attempts max for transients with 2 s/8 s floors (existing
asserts `sleeps == [2.0]` at `test_translate_resilience.py:80` and `[2.0, 8.0]` at :103 still pass —
plain `RuntimeError` has no `status_code` → retried; `max(floor, 0.0) == floor`).
**Gain:** bad key surfaces <1 s instead of ~10 s + 3 paid calls; 429 with Retry-After ≤30 s
succeeds within one retry.
**Verify:** extend `test_translate_resilience.py`: (a) `status_code=401` → raises after exactly
1 call, `sleeps == []`; (b) 429 with `retry-after: 7` failing twice then OK → `sleeps == [7.0, 8.0]`;
(c) plain RuntimeError keeps legacy behavior.

---

## API-3 [S] Deterministic prompt-prefix rendering (provider-cache foundation)

**Premise (verified):** glossary renders in dict-insertion order (`_common.py:78` refine, `:100`
user prompt) — `load_glossary` (`translate.py:237-253`) merges global-then-project via
`dict.update`, and hand-edited files are unsorted, so prompt bytes vary with file history →
breaks DeepSeek's byte-exact prefix cache across runs. Context meta lines also render unordered
and have no private-key filter (`:54, :74, :96`). Proofread context omits `platform`
(`desktop/worker.py:163`) vs translate's context — different shape for no reason.

**Change:**
1. In `build_user_prompt`, `build_refine_prompt`: iterate `sorted(glossary.items())`.
2. In all three builders (incl. `build_proofread_prompt`): meta line =
   `", ".join(f"{k}: {v}" for k, v in sorted(context.items()) if v and not k.startswith("_"))` —
   **keys starting with `_` are reserved private plumbing that must never render** (API-7 uses
   `_ocr_source`, API-8 uses `_budgets`).
3. `desktop/worker.py:163`: proofread context gains `platform` to match translate's shape.

Note in the commit message: sorted rendering changes prefix bytes exactly once (one-time provider
cache-family reset), then stable forever.
**Gain:** enabler + direct payoff on resumed/repeated runs: a 100-entry glossary (~900 prefix
tokens) on a 6-batch job re-hits DeepSeek disk cache on ~5 400 input tokens at ~98 % discount.
**Verify:** new `backend/tests/test_prompt_stability.py`: (a) same glossary entries inserted in
different orders → byte-identical prompts; (b) context containing `_ocr_source`/`_budgets` renders
byte-identically to the same context without them; (c) repeated calls → identical bytes.

---

## API-4 [M] Right-size `max_tokens` + loud truncation guard (`finish_reason == "length"`)

**Premise (verified):** no OpenAI-compat call sets `max_tokens` (`_common.py:150-158, 163-173,
178-187, 191-199`) → output bounded only by the provider's server default; a repetition-loop reply
bills unbounded output. Worse, truncation is **silent**: `parse_numbered` (:107-117) pre-fills the
fallback so it always returns n lines and `_check_count` passes — untranslated Chinese lines are
discovered only at the edit gate.

**Change (both in `_common.py`):**
1. `def _completion_cap(n_lines: int) -> int: return max(300, min(8192, 60 * n_lines))`
   (subtitle lines ≈ 15–35 completion tokens incl. numbering; 60/line ≈ 2× headroom). Pass
   `max_tokens=_completion_cap(len(texts))` in `translate_texts`/`proofread_texts`,
   `_completion_cap(len(vi_texts))` in `refine_texts`; add optional `max_tokens: int = 300`
   parameter to `rewrite()` (API-5's batched shorten passes a cap for its chunk).
2. After each `create()`: `if resp.choices[0].finish_reason == "length": raise RuntimeError(
   "Provider reply truncated at max_tokens=… — batch will be retried, not silently padded")` —
   **before** `parse_numbered`. The exception rides the `_retry` ladder (repetition loops at
   temperature 0.3 are stochastic; a retry usually clears them); persistent failure is loud with
   the checkpoint intact; refine degrades that batch to draft; proofread keeps its partial-batch
   log (`ocr.py:322-326`).

**Gain:** runaway-output worst case −90 %; silent truncation becomes loud/retried. Normal spend
unchanged.
**Risk:** 60 tok/line too tight for exploded lines → burns 3 retries then fails loud (strictly
better than silent CN fallback). 300 floor protects tiny batches.
**Verify:** new `backend/tests/test_completion_cap.py` with a kwargs-recording stub client:
(a) 50 lines → `max_tokens=3000`; 2 lines → 300; (b) `finish_reason="length"` → RuntimeError and
`parse_numbered` never called (sentinel); (c) `finish_reason="stop"` missing one line → per-line
fallback as today.

---

## API-5 [S] Batch `_autoshorten` — all overlong lines in ONE `rewrite()` call per chunk

**Premise (verified):** `_autoshorten` (`translate.py:190-217`) makes **one API round-trip per
overlong line**, serially, pre-gate; each call re-resolves the provider and rebuilds a client.
`rewrite(text, instruction)` already exists on `OpenAICompatTranslator` (`_common.py:189`) —
no provider interface change needed. `shorten_line` (`:220-234`) must stay untouched — the GUI
editor calls it per-line (`desktop/views/editor_view.py:357-364`).

**Change (rewrite `_autoshorten` only):**
1. Pass 1 collects `todo: list[(i, pct)]` — same slot/fits logic as today (:203-207); bucket the
   ratio to nearest 10 %, clamp 50–90: `pct = max(50, min(90, round(ratio*10)*10))`. Empty todo, or
   provider lacks `rewrite`/unavailable (existing guard :198-199) → return unchanged, zero calls.
2. Per chunk of ≤ `BATCH` lines: numbered body
   `f"{k}| (rút còn ~{pct}%) {out[i]}"`, plus a CONSTANT module-level
   `SHORTEN_BATCH_INSTRUCTION` (Vietnamese: biên tập phụ đề lồng tiếng; rút gọn về ~N% giữ nghĩa,
   giọng tự nhiên; trả về đúng số dòng dạng `n| <câu>`, không giải thích). Constant instruction =
   cacheable prefix.
3. One `prov.rewrite(body, SHORTEN_BATCH_INSTRUCTION, max_tokens=_completion_cap(len(chunk)))` per
   chunk (needs API-4's param); parse with the existing tested
   `parse_numbered(reply, len(chunk), fallback=[current lines])`; strip like `shorten_line` does;
   keep the per-line acceptance gate (only accept if `speech_seconds` decreased).

**Gain:** m overlong lines: m round-trips → ceil(m/50); a chatty video's 15–20 serial calls
(~30–60 s pre-gate) → one ~2–4 s call; −~70 tokens/line (instruction paid once).
**Risk:** model renumbers/echoes annotation → bounded by fallback + acceptance gate (worst case: a
line stays long — exactly today's failure mode, never corruption).
**Verify:** new `backend/tests/test_autoshorten_batch.py` with a rewrite-recording fake: (a) 10
cues, 3 overlong → exactly ONE call, body has 3 numbered `(rút còn ~` lines, only those indexes
changed; (b) reply missing a line → that line keeps original; (c) reply longer than original →
original kept.

---

## API-6 [M] Checkpoint the refine pass (protect the most expensive tokens)

**Premise (verified):** draft pass checkpoints every batch (`translate.py:123-129`, resume
`:87-95`), but `refine_pass` (`:150-167`) accumulates only in memory — a crash/kill mid-refine
re-buys ALL completed refine batches, and refine (CN + draft re-sent ≈ 2.4× draft input, frontier
prices) is ~95–98 % of translation dollars on two-pass runs.

**Change (mirror the draft machinery in `translate.py`):**
1. `ckpt_refine = work_dir / "translate.refine.checkpoint.json"` (when work_dir set).
2. Key: `_ckpt_key("refine|" + provider_name, cn_src + ["\x00"] + vi_draft)` — covers BOTH source
   and draft (a re-run whose draft differs must invalidate) with an unambiguous separator.
3. Load via `_load_checkpoint` (whole-batch prefix truncation at :92 already generalizes — it uses
   module `BATCH`, works with the tests' monkeypatched BATCH=2); skip covered batches; log resume.
4. Persist after each batch (success OR draft-degrade at :162-166):
   `{"key": key, "done": out}` — do NOT reuse `_persist_progress` (it also rewrites `vi.draft.srt`,
   which must keep meaning *draft*, not half-refined).
5. Cleanup in `translate()` (:311-312): also unlink the refine checkpoint on success.
6. Docstring note: a batch that degraded to draft is checkpointed as final for this run (consistent
   with today's semantics).

**Gain:** kill mid-refine on a 600-cue/12-batch job: resume re-buys 1 partial batch instead of 12 —
preserves up to ~92 % of refine tokens ≈ ~90 % of job dollars. Zero happy-path cost.
**Verify:** extend `test_translate_resilience.py` (BATCH=2 fixture exists): (a) pre-seeded refine
checkpoint → counting fake called once (batch 2 only); (b) stale key (different draft) → both
batches re-run; (c) successful `translate()` unlinks both checkpoints.

---

## API-7 [M] OCR-tolerance in translate prompts by default; demote separate proofread to opt-in

**The core OCR-path task.** **Premise (verified):** with "AI sửa lỗi OCR" ticked, every proofread
line's CN text is billed twice as input + once as output; on job dirs without `ocr_report.json`
(all pre-N-14 jobs) it degrades to a FULL-transcript double-send. Grep confirms no `OCR_HINT` /
`_ocr_source` exists. Depends on API-3 (`_`-key filtering must land first or `_ocr_source: True`
leaks into prompt text — verified: context renders unfiltered at `_common.py:74,96`).

**Change:**
1. `_common.py`: module constant `OCR_HINT` — "Note: the Chinese lines were read off video frames
   by OCR and may contain misrecognized characters (wrong/similar-looking hanzi, stray symbols) —
   translate the intended meaning, not the garbled surface form." In `build_user_prompt` and
   `build_refine_prompt`, after context/glossary: `if context and context.get("_ocr_source"):
   parts.append(OCR_HINT)`. Shared builders → all chat providers inherit; constant position →
   prefix stays byte-stable per run.
2. `translate.py::translate()` (:270): after building context (:301-304),
   `if (job.work_dir / "ocr_report.json").exists(): context["_ocr_source"] = True` + one log line
   ("ℹ Nguồn OCR — dịch chịu lỗi OCR trong prompt (+~25 token/lô)"). `ocr_report.json` is written
   by `extract_hardsubs` (`ocr.py:262`) in both GUI and CLI paths.
3. Stale-signal guard: in the provided-SRT branches (`desktop/worker.py:144-147` and the CLI
   equivalent) delete any leftover `ocr_report.json` from a previous OCR run of the same dir, so
   an SRT-sourced re-run doesn't claim OCR provenance.
4. UI copy: the existing "Dùng AI sửa lỗi OCR" checkbox stays as the opt-in escape hatch
   (suspect-only proofread survives untouched); update its tooltip to say the translate prompt
   already tolerates OCR noise by default.

**Gain:** legacy full-transcript proofread disappears (−45–50 % of draft-provider tokens on the
OCR path); suspect-only runs −15–30 %; non-tickers pay only +~25 tokens/batch for the hint.
**Verify:** new `backend/tests/test_ocr_hint.py`: (a) builders with `_ocr_source: True` contain
OCR_HINT exactly once and the literal `_ocr_source` nowhere; (b) `translate()` on a tmp job with
`ocr_report.json` passes `_ocr_source` through to the provider (recording fake); (c) SRT-source
branch removes a stale report file.

---

## API-8 [M] Fold length budgets into the refine prompt (kill the triple-pay on overlong lines)

**Premise (verified):** an overlong line is paid for three times — draft, refine polish of the
soon-discarded long version, then `_autoshorten` (runs AFTER refine, `translate.py:184-185`).
Nothing budget-related exists (grep zero). Depends on API-3 (`_budgets` privacy) and API-5
(residual overlong lines then cost 1 batched call, not m). Touches the same lines as API-6 —
land after it, rebase deliberately.

**Change:**
1. `refine_pass`: before the loop, compute per-line word budgets from the DRAFT via
   `app.pipeline.fit_estimate` (`MAX_TEMPO, SYL_PER_SEC, fits, slot_seconds`):
   `budgets[i] = int(slot_seconds(cues, i) * MAX_TEMPO * SYL_PER_SEC)` when
   `slot > 0 and not fits(vi_draft[i], slot)`, else `None` (fitting lines render byte-identically
   to today). Thread `cues` into `refine_pass` (signature change internal to translate.py).
2. Per-batch: `ctx_b = {**(context or {}), "_budgets": budgets[slice]}` passed as `context`.
3. `build_refine_prompt`: pop `_budgets`; for budgeted i render
   `[i] 中文: {zh}\n[i] nháp (quá dài — rút còn tối đa ~{b} từ): {vi}`, else exactly the current
   two-line form (:82-83). Add one CONSTANT sentence to `REFINE_SYSTEM_PROMPT`: "If a draft line is
   annotated with a word budget, also shorten it to fit while keeping the meaning."

**Gain:** typical 15–20 overlong lines → 0–3 residual after refine; the pre-gate shorten step drops
to usually zero calls; −2–4 s wall clock and −~1.5 k tokens per chatty video.
**Risk:** over-aggressive shortening — bounded: human gate reviews everything; a refiner that
ignores the annotation costs nothing extra (API-5 safety net catches it).
**Verify:** new `backend/tests/test_refine_budgets.py`: (a) recording fake — only the overlong
line's block contains `rút còn tối đa`, `_budgets` appears nowhere; (b) all-fitting drafts →
prompt byte-identical to pre-change (golden assert); (c) refine failure on an annotated batch
still degrades to draft.

---

## API-9 [L] Parallel draft+refine batches (4-wide) + checkpoint v2 + cancel mid-translate

**Premise (verified):** draft and refine are strict sequential for-loops (`translate.py:137-148,
150-167`); the only structural blockers are the prefix-format checkpoint and ordered accumulation.
`translate_cues` takes NO cancel parameter — GUI Cancel does nothing until the whole stage returns.
Qt cross-thread `Signal.emit` is queued/thread-safe (`desktop/worker.py:29`), pipeline already runs
on a QThread. Depends on API-1 (thread-safe cached clients, 120 s cap), API-2 (sane per-batch
retry), API-6 (refine checkpoint exists to migrate).

**Change (all in `translate.py` + one wiring line in `worker.py`):**
1. **Checkpoint v2:** `{"key": …, "v": 2, "batches": {"0": [...], "2": [...]}}` — per-completed-
   batch entries keyed by batch index; gaps explicit and safe. `_load_checkpoint` → returns
   `dict[int, list[str]]`; accepts v2 AND legacy v1 (`done` prefix list, incl. API-6's refine file)
   by converting the whole-batch prefix. Validate each stored batch's length; drop invalid.
2. **Writer** `_persist_batches(ckpt_path, draft_srt_path, key, batches, cues)`: atomic tmp+replace
   (mirror `save_glossary` `:264-267`); `vi.draft.srt` written from the LONGEST CONTIGUOUS PREFIX
   of completed batches only — the recovery SRT never has holes.
3. **Declared capability:** `TranslationProvider.parallel_batches: int = 1` (`base.py`, same
   convention as `can_refine`); set 4 on `OpenAICompatTranslator` and `DeepLTranslator`; leave 1
   elsewhere. Width = `min(4, provider.parallel_batches, n_missing)`.
4. **Executor:** ThreadPoolExecutor; each future runs `_retry(lambda: provider.translate_texts(…))`;
   per-batch count enforced INSIDE the task (loud mismatch). Coordinator loops `as_completed`:
   store, `_persist_batches`, emit "… dịch lô k/n" (coordinator-only logging). First failed future →
   `executor.shutdown(cancel_futures=True)`, wait in-flight, persist completed, re-raise — a hard
   failure still leaves every completed batch on disk. Same shape for refine (with per-batch
   draft-degrade preserved).
5. **Cancel:** `translate_cues(…, cancel: Callable[[], bool] | None = None)` — coordinator checks
   before submitting each batch and between completions; on cancel: stop submitting, wait
   in-flight, persist, raise `TranslateCancelled(RuntimeError)`. `worker.run_to_gate` passes
   `cancel=lambda: self._cancel` and maps `TranslateCancelled` to the existing "Đã hủy." path.
   Resume after cancel re-buys nothing (checkpoint has the completed batches).

**Gain:** translate wall clock ÷ 3–4: 300-cue two-pass ~2 min → ~40 s; 600-cue ~4–6 min → ~1.5 min.
Cancel takes effect within ~one batch.
**Risk:** the v1→v2 migration is the sharp edge — covered by a dedicated migration test; provider
rate limits under 4-wide fan-out → API-2's Retry-After handling is the safety valve.
**Verify:** new `backend/tests/test_translate_parallel.py` (BATCH=2): (a) concurrency proof —
fake provider blocks on `threading.Barrier(3, timeout=5)`; 3 batches → ≥3 calls in flight;
(b) out-of-order completion yields ordered output; (c) kill batch 2 of 3 → checkpoint holds batches
1+3, resume re-buys only 2; (d) v1 checkpoint file loads as prefix batches; (e) cancel after batch 1
→ `TranslateCancelled`, checkpoint has batch 1, no further provider calls.

---

## API-10 [L] edge-tts: persistent event loop + shared connector + bounded 3-wide concurrency

**Premise (verified):** each cue pays a fresh `asyncio.run` + `Communicate` + WSS TLS handshake
(`edge.py:37-46`), strictly serial in `tts.synthesize` (:102-127). edge-tts 7.2.8 supports a shared
`connector=` (live signature check) — **bump `pyproject.toml` pin `edge-tts>=6.1` → `>=7`**.
Two verifier amendments folded in below.

**Change:**
1. `base.py`: `TTSProvider.supports_batch: bool = False` + documented optional
   `synthesize_many(items, *, voice=None, rate=None, concurrency=3, retry_delays=(1.0, 4.0),
   on_done=None, cancel=None) -> list[tuple[key, Exception | None]]`,
   `items = [(key, text, out_path)]`.
2. `edge.py`: `supports_batch = True`; `synthesize_many` = ONE `asyncio.run` for the whole job
   (keep the per-cue path in `synthesize()` for single-shot GUI preview). Inside: one
   `aiohttp.TCPConnector` + `asyncio.Semaphore(3)`. Per item: (a) if `cancel()` → record
   `(key, InterruptedError())` WITHOUT starting network work; (b) retry ladder mirroring the
   pipeline's (1 s/4 s, `NoAudioReceived` = deterministic → record immediately, no retry);
   (c) success → `(key, None)`. After the FIRST fatal (non-NoAudioReceived, retries-exhausted)
   failure: stop admitting new items (event checked before each semaphore acquire) — dead endpoint
   fails fast. Keep the running-loop guard (:40-45). `asyncio.run` executes on the calling pipeline
   QThread — GUI-thread safety unchanged.
3. `tts.py::synthesize` → two phases. Phase A (sequential scan, logic unchanged): muted/empty →
   `manifest.pop`; hash-match → kept; else append to `todo`. Phase B: if todo and
   `getattr(tts, "supports_batch", False)` → `synthesize_many(…, cancel=cancel, on_done=_done)`
   where `_done(key, exc)`:
   - `exc is None` → `manifest[key] = want_hash`; `_save_manifest` (index-keyed dict → out-of-order
     writes crash-safe; `_done` runs serialized on the loop thread);
   - `_unvoiceable(exc)` → log skip, no manifest entry (mirror :62-69 semantics);
   - **`isinstance(exc, InterruptedError)` → NOT fatal, no manifest write, no raise** —
     after return, `synthesize()` returns the partial cue-ordered list exactly like the sequential
     break (:103-104). *(Amendment 1: the locked contract is partial-return-no-exception —
     `test_tts_resume.py:109-114` asserts it; worker maps its own flag to "Đã hủy.".)*
   - other exc → remember as fatal; re-raise after drain.
   Build the returned `segments` by iterating cues in order (determinism; note `assemble` sorts by
   cue start itself — `assemble.py:177-178` — so ordering here is for byte-identical outputs, not a
   hard requirement. *(Amendment 2.)*
   Fallback: providers without `supports_batch` (azure/fpt/google stubs, test fakes) take the
   existing sequential path byte-unchanged — all `test_tts_resume.py` tests must pass untouched.

**Gain:** 200-cue dub ~5–8 min → ~1.5–2.5 min (−60–70 % of the TTS stage). Even at concurrency=1
the persistent loop saves ~1–2.5 min (no per-cue loop+TLS setup).
**Risk:** unofficial endpoint may 403 more under 3 simultaneous handshakes — concurrency is one
constant, drop to 2/1 if observed; per-item retry ladder + fail-fast bound the damage.
**Verify:** new `backend/tests/test_tts_parallel.py`: (a) FakeBatchTTS calling `on_done` out of
order (3,1,2) → manifest on disk after each `_done` contains exactly completed keys; final segments
cue-ordered; muted cues popped; (b) fatal on item 2 → items 1,3 persisted, exception raised after
drain; (c) cancel mid-batch → partial return, NO exception (mirror `test_tts_resume.py:109-114`);
(d) all existing `test_tts_resume.py` tests pass unchanged (sequential fallback untouched).
