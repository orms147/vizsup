# vizsup — Ordered Development Plan (v2 · 2026-07-02)

Single sequential workflow for one maintainer. Each task lists WHAT, the files it touches, effort
(S/M/L), dependencies, the key risk, and how to VERIFY it — **headless-first** (pytest construct/
render checks; GUI gestures get one manual smoke). Task ids `N-*` are stable; milestones group them
by theme and order.

**How v2 was produced:** a 5-area multi-agent audit of the full codebase (backend pipeline, desktop
GUI, providers, docs-vs-code, tests/quality) on 2026-07-02, followed by 3-lens prioritization
(user-value / correctness-risk / engine-depth), dedup, and spot verification. The load-bearing bug
claims below were **verified by execution or direct code read** before being scheduled — this plan
does not repeat v1's staleness (v1's "Where we are" described Phase B/C as pending long after they
shipped).

---

## Where we are

**Done and integrated (see git history):** the full desktop path works end-to-end — download
(yt-dlp, Douyin URL normalization, cookie hints) → auto hardsub-detect (E-1, `--source auto`,
"Tự động" default) → OCR (RapidOCR v3, GPU ladder, grouping/noise filters, opt-in text-only LLM
proofread = E-3-light) **or** ASR (faster-whisper CUDA→CPU, FunASR) → 2-pass translate with real
refine pass + glossary auto-load → **edit gate** (timeline+table editor, per-line styles Phase C,
drag/resize subtitle box on live video Phase B, per-line gain/mute, nghe thử, LLM rút gọn) → TTS
(edge-tts) → assemble (overlap-free absolute-timeline fit, rubberband≤1.30×, mix/duck/replace,
style/`style_overrides.json` honored) → render. Utilities: `vizsup doctor`, glossary files.
M0–M4 of plan v1 are closed except **E-2**, which is now **N-7**.

**Known-broken right now (all execution- or read-verified 2026-07-02):**

| Bug | Evidence | Fixed by |
|---|---|---|
| SRT/ASS time formatter emits 4-digit ms — `_fmt_srt_time(1.9995)` → `00:00:01,1000`, parsed back as 1.1s: silent cue shrink on every file round-trip | executed | **N-2** |
| FastAPI render always TypeErrors: `jobs.py:201` passes removed `replace_audio=` kwarg (after TTS already ran) | jobs.py:201 | **N-11** |
| Re-running `vizsup run` on the same URL silently overwrites a human-edited `vi.srt` (workdir = sha1(url); no skip/guard/backup) | cli.py:48-88 | **N-3** |
| Azure/FPT/Google TTS are `NotImplementedError` stubs whose `available()` returns True with a key → preflight passes, render dies | azure.py:19-23 | **N-12** |
| Render screen passes the literal string `"(mặc định)"` as the TTS voice when a provider lists no voices | render_view.py:162,172 | **N-13** |
| Chinese reference column misaligns on reopen after split/merge/delete (`load_pair` pairs `vi[i]`↔`zh[i]` by index) | cuemodel.py:94-107 | **N-17** |
| Debounced (600 ms) editor autosave is lost on quit/job-switch; `_resume`/`_delete_job` not busy-guarded | editor_view.py, main.py | **N-1** |
| `GAP_RESET=0.7` is a dead constant; the assemble docstring promises a reset rule the code doesn't implement | assemble.py:46 (sole ref) | **N-5** |
| One failed translate batch discards all completed paid batches; one edge-tts 403 discards all finished TTS segments | translate.py / tts.py | **N-4 / N-10** |

---

## Milestone 5 — Correctness & data safety (P0 — do first, in this order)

Cheap, high-value closures. N-9 lands first so every later task is CI-gated.

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **N-9** | **CI + lint.** `.github/workflows/ci.yml` (ubuntu, py3.11: install `backend[dev]`, `ruff check backend/app desktop`, `pytest backend/tests -q`). Root `ruff.toml` (line-length 100, py311) so `desktop/` is finally linted; root pytest config (`testpaths`). Fix the 5 existing findings (F541 assemble.py:240 + flow_steps.py:52-53, F401 style_panel.py:17, E741 `l` in video_canvas.py:27). | S | — | Linux CI hitting a Windows-only dep — keep CI to base deps, tests are pure. | Actions run green on a pushed branch; `ruff check` exits 0 locally. |
| **N-2** | **Fix ms-carry corruption + QA foundation.** Rewrite `_fmt_srt_time`/`_fmt_ass_time` on integer `divmod` chains (`total_ms = round(t*1000)`). New `backend/app/qa.py`: `cer()`, `srt_text_cer()`, `timing_stats()` (pure, stdlib). `assemble()` writes `<job>/fit_report.json` from `_plan_segments` output; CLI render passes `log=print` (the "câu QUÁ DÀI" warnings are currently discarded headless). | M | — | Formatter rewrite must stay byte-identical below the carry threshold or the P0-1 goldens break — re-bless deliberately, never blindly. | pytest: `_fmt_srt_time(1.9995)=='00:00:02,000'`; 10k random floats round-trip within 1 ms; test_write_ass stays green; render produces fit_report.json. |
| **N-5** | **Policy regression tests** for the three untested quality-policy functions + one guard: `_plan_segments` (no-overlap invariant, 1.30 cap, span reclaim, trim-vs-push, carry cap, zero-duration skip, input-index preservation) with `probe_duration` monkeypatched; delete dead `GAP_RESET` + fix docstring; `dedupe_adjacent` merge table; `parse_numbered` (missing/dup/out-of-range indices, refine-echo `[n] 中文:` never matched); hard guard in `translate_cues`: `len(vi)!=len(cn)` → RuntimeError (today a short DeepL reply silently drops trailing cues at the `zip`). | M | — | Tests written to match buggy behavior would enshrine it — argue each case from the documented policy; prefer invariants over golden floats. | `pytest -q` <1s; mutation checks (flip SPEED_MAX→2.0, drop the `max(cue.start, prev_end)` clamp) fail the suite. |
| **N-1** | **Editor data safety.** `EditorView.flush()` (stop 600 ms debounce, `save_now()` if dirty) called on job-switch in `load()` and in `MainWindow.closeEvent` (skip while a render worker runs). Busy-guard `_resume`/`_delete_job`; refuse deleting the currently-open job. | S | — | `save_now()` racing a running render reading vi.srt — flush only when not busy. | pytest-qt offscreen: edit → immediately `load(jobB)` → jobA's vi.srt has the edit; manual quit-within-0.5s smoke. |
| **N-3** | **Edit-safe re-runs.** `translate()` gains `force=False`: always back up existing vi.srt to `vi.srt.bak-<ts>`; refuse (clear VN error) when vi.srt is newer than cn.srt and not forced. `--force` in CLI; same guard in jobs.py; wire the desktop's legitimate re-translate path. Without `--force`, `cmd_run` skips stages whose artifact exists (`[run] found existing X -> skipping`). Also: CLI `--source` default `asr`→`auto` (match GUI); re-save metadata.json after detection so `has_hardsubs` persists. | M | — | Skip logic confusing "I changed the translator, nothing happened" — skip messages must name the artifact and the escape hatch. | pytest: edited-vi.srt tmp job → `translate()` raises without force, .bak with; `cmd_run` twice → skips, bytes identical. |
| **N-11** | **Fix the API render TypeError + contract tests.** `jobs.py:201` `replace_audio=…` → `audio_mode=('mix' if keep_audio else 'replace')` via a `_render_kwargs(opts)` helper; expose audio_mode/volumes in `RenderOptions`; align CreateJob's hardcoded `asr='whisperx_funasr'` to `settings.vizsup_default_asr`; fix lying docstrings (main.py:6-8, asr.py:4-5 still say "stubs"). `test_api_contract.py`: `inspect.signature(...).bind` the kwargs helper against the real signatures; async JobManager run with fake stages → AWAIT_EDIT then DONE. | M | — | Async test needs an event-loop fixture (pytest-asyncio/anyio dev dep); don't test through live uvicorn. | Contract tests green; re-introducing a bad kwarg fails the bind test (mutation check). |
| **N-12** | **Provider honesty + fresh model ids.** Stub TTS `available()` → False (UI greys "sắp có", not hidden). `can_refine`/`can_proofread` class flags on `TranslationProvider` (True on OpenAICompat; can_refine on Claude/Gemini); refine combo greys non-refiners; `proofread_srt` skips non-proofreaders with a logged "⚠ bỏ qua" (today Claude/Gemini/DeepL no-op and the worker logs a false "✓ Đã rà soát N dòng") and try/excepts the batch loop so a mid-proofread API error keeps fixed batches. Route duplicated `'edge'`/ASR literals through settings. **Web-verify at implementation time:** GLM id (`glm.py` TODO, add `zhipu_model` override + fix Settings "Test kết nối" for paas/v4), OpenRouter default slug (`deepseek/deepseek-chat-v3` deprecates 2026-07-24). Delete orphan `elevenlabs_api_key`. Guard test: no default id contains `deepseek-chat`/`deepseek-reasoner`. | M | — | A wrong "fixed" slug is worse than the TODO — web verification is mandatory, not optional. | pytest: list_providers shows stub TTS unavailable; capability flags match overrides; proofread partial-failure keeps batch-1 fixes; id-guard test green; real-key "Test kết nối" passes for GLM/OpenRouter. |
| **N-20** | **Anchor storage + .env to the repo root** (`config.py`: `_ROOT = Path(__file__).resolve().parents[2]`; `env_file=str(_ROOT/'.env')`; storage default `_ROOT/'storage'`). Doctor prints resolved paths and warns about the legacy `backend/storage` split (already happened on this machine) + fails on opencv-full+PySide6 and onnxruntime+onnxruntime-gpu coexistence. | S | — | Keys/projects "move" once for anyone who launched from backend/ — the doctor warning must land in the same commit. | pytest: config resolves absolute regardless of cwd; doctor from root and backend/ prints identical paths. |

**DoD (M5):** CI green on every commit; timestamps survive 10k-cue round-trips; an edited vi.srt
can no longer be silently destroyed (backup + guard + skip); the API renders again; provider list
never offers an engine that can't run; storage/.env stop depending on launch dir.

> **✅ M5 DONE (2026-07-02, commits `297228d`…`d7174bb`).** All 8 tasks landed with 89 headless
> tests green + ruff clean. Notes vs spec: N-3's mtime heuristic was replaced with
> exists+`--force`+always-backup (translate itself leaves vi.srt newer than cn.srt, so the mtime
> rule would block every legitimate re-run); N-12 web-verified `glm-5.2` as still-correct (no id
> change — TODO removed, `ZHIPU_MODEL` override + chat-ping test_connection added) and bumped
> OpenRouter's default to `deepseek/deepseek-v4-flash`; stub TTS now grey out as "sắp có";
> N-20 additionally promoted the two shared-package-dir landmines (opencv-full+PySide6,
> onnxruntime×2) to doctor hard-failures. Worker/CLI also gained artifact-based stage skipping
> (resume-friendly re-runs). Suite: 19 → 89 tests.

---

## Milestone 6 — Long-run reliability (paid-work durability, renders that can't be wasted)

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **N-10** | **TTS resume + retry.** `<job>/tts/manifest.json` (cue → sha1 of `provider|voice|rate|text`); skip matching non-empty segments (re-render = only changed lines); 3-attempt backoff in the pipeline layer (edge-tts 403 is documented but unhandled — today a 403 at cue 180/200 discards 179 segments); optional `cancel` callback between cues; muted cues leave no stale entry. | M | — | A cache key missing a future synthesis knob serves stale audio — hash derivation in one tested function. | FakeTTS pytest: 2nd run = 0 calls; one text change = 1 call; voice change = full resynth; fail-twice-then-succeed completes. |
| **N-13** | **Render integrity.** Snapshot cues at render start and pass as `burn_cues` (param already exists) so burned subs match what TTS spoke; lock editor/back-navigation while rendering (single `_set_render_locked()` on all exit paths); "Hủy" button wired to worker.cancel + N-10's per-cue cancel; **fix the `"(mặc định)"` literal voice** → `None`. | M | N-10 | Navigation lock must re-enable on done/failed/cancelled or the app soft-locks. | pytest: cancel after 3 cues stops cleanly; burn_cues equals snapshot even when vi.srt is rewritten mid-render (monkeypatch). One manual lock/cancel smoke. |
| **N-4** | **Translate resilience.** `_retry` (3 attempts, injectable sleep) around each batch; draft checkpoint `<job>/translate.checkpoint.json` keyed by sha1 of the post-dedupe CN list + incremental `vi.draft.srt`; refine-batch failure degrades to that batch's draft ("⚠ tinh chỉnh thất bại — dùng bản nháp") instead of discarding everything; per-batch progress `dịch lô i/N` to the UI log. | M | N-3 | Stale checkpoint after cn.srt changed — the hash must cover the post-dedupe text list. Retry only on exceptions, never on parse fallbacks. | Fake-provider pytest: fail-once recovers; refine-dead-on-batch-2 → draft kept + warning; draft-dead-on-batch-2 → checkpoint has batch 1, second call resumes. |
| **N-18** | **Long-video render robustness.** Write the filter graph to `filter_complex.txt` + `-filter_complex_script` (Windows 32k argv ceiling breaks ~200-250-cue renders today); chunk `amix` in groups of ~32; `probe_duration` catches `CalledProcessError` → 0.0 (skip corrupt segment, don't abort); probe source for an audio stream — when absent force `replace` with a note instead of a hard `[0:a]` ffmpeg error. | L | — | Chunked amix off-by-one silently drops segments — construct test must assert every input label appears exactly once. | pytest: 300 fake segments → argv <8k chars, script exists, 300 labels exactly once; one real `vizsup render` smoke. |
| **N-17** | **Cue-model integrity.** (a) `save_vi` also writes `zh_ref.json` (zh aligned 1:1 with saved rows); `load_pair` prefers it when length-matched, else today's index-pairing — fixes the reopen misalignment. (b) Extract split/merge policy from the QWidget into pure `cuemodel.split_cue/merge_cues` (word-half split, inheritance rules, zh concat with space). (c) Unify the 3 near-identical sidecar loaders (tts.py / assemble.py / cuemodel.py) into `backend/app/pipeline/sidecars.py` — int keys internally, current on-disk JSON schema unchanged. | L | N-2, N-9 | zh_ref drift when vi.srt is edited externally — length-guard must degrade to today's behavior, never worse; on-disk schema stays byte-compatible with existing jobs. | Headless pytest: load→split/merge/delete→save→load keeps every row's zh; sidecar round-trip; backend and editor loaders return identical data; grep shows one loader implementation. |

**DoD (M6):** a transient 429/403 can no longer waste a run's paid work; re-renders only redo
changed lines; a render can be cancelled and can't race the editor; 30–60-min videos render;
the Chinese reference column survives structural edits + reopen.

---

## Milestone 7 — Engine depth (output quality; includes E-2)

N-21 first — N-7 and N-8 depend on it. Every quality task carries a measurable acceptance metric
(the N-2 `qa.py` foundation).

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **N-21** | **Heavy-model sequencing.** `Provider.unload()` hook (no-op default; ASR providers drop `_model`, `gc.collect()`, `torch.cuda.empty_cache()`); `backend/app/heavy.py` with a process-wide `heavy_task()` RLock context; wrap ASR transcribe + OCR frame loop; jobs.py wraps its SUBTITLE stage — closes the audited API-concurrency VRAM violation. | S | — | Nested acquisition deadlock — RLock + acquire once at stage top. | pytest: two threads through fake heavy bodies → max concurrency 1. Optional on-device: post-unload `cuda.memory_allocated()` < 200 MB. |
| **N-7** | **E-2 — fused ASR provider `funasr_aligned`.** Keep existing providers untouched (`whisperx_funasr` id stays for config compat). Three sequential phases: FunASR sentence transcript → timing track (whisperx zh alignment if installed via new `[asr-align]` extra, else faster-whisper `word_timestamps=True` as guaranteed baseline) → pure `align.fuse_transcript_timing()` (char-anchor monotonic matching; <50% match keeps FunASR times). `scripts/asr_timing_eval.py` + one hand-labeled `samples/refs/*.cn.ref.srt` (~20 cues). Web-verify the whisperx zh model id at implementation time. | L | N-2, N-21 | whisperx zh alignment may be unavailable/heavy on this box — the faster-whisper fallback must guarantee the provider never regresses below FunASR-only. | pytest on synthetic word streams (exact/partial/no match, monotonicity); eval acceptance: fused median |dStart| ≤ FunASR-only AND ≤ 200 ms on the reference clip. |
| **N-14** | **OCR confidence + suspect-only proofread.** Extract the grouping state machine into pure `group_frames()` (behavior-preserving — diff a real cn.srt before/after); confidence rides through grouping; `<job>/ocr_report.json` per cue (`conf`, `rep_frame`, `suspect` via a pure classifier); `proofread_srt(only_suspect=True)` batches only suspect lines (today it proofreads everything = 5-10× the tokens); unit-test `_keep_line`/`_similar`/`_best`/`_to_lines` (incl. the never-exercised legacy API shape). `scripts/ocr_eval.py` vs a hand-fixed reference. | M | N-2 | Extraction must be byte-for-byte on group-break start times; threshold miscalibration mitigated by the all-lines escape hatch. | pytest grouping/classifier tables; before/after cn.srt identical; eval: suspect-only CER within 0.5 pt of full proofread while LLM-sent lines drop ≥50%. |
| **N-6** | **Length-aware drafting.** Pure `fit_estimate.py` (`speech_seconds` ≈ syllables/4.8, `slot_seconds` mirroring `_plan_segments` span). Optional `budgets` kwarg threaded into prompts — only tight lines get `(tối đa B từ)` annotations; post-refine, still-overlong lines get one `shorten_line` rewrite — all **before** the gate, so the user reviews already-fitting lines. | M | N-2, N-5 | Budget annotations confusing weaker draft models into truncating meaning — annotate only tight lines; parse fallback per line. | pytest units + prompt-builder cases; fit_report.json before/after on the same sample: flagged lines drop ≥50%. |
| **N-15** | **Auto term-extraction + glossary compliance.** `terms.extract_terms()` (one chunked LLM call over the CN transcript → `glossary.suggested.json`, merged lowest-precedence); opt-in Screen-1 checkbox; pure `check_glossary()` writes `glossary_report.json` after translate, worker warns "⚠ N dòng lệch thuật ngữ" for gate review. | M | — | Wrong LLM-suggested renderings — they surface as suggestions + warnings, never silently authoritative. | pytest compliance table + parser robustness; violation count on a multi-name sample → ~0 with merged glossary. |
| **N-16** | **Hardsub cover 2.0.** Persist the detected sub band (`hardsub_region.json`, pure crop→frame coord mapping) and the OCR-vs-ASR decision (currently never saved); `cover_mode='off'|'box'|'blur'` (blur = crop+boxblur+overlay on the band, box = existing BorderStyle-4); expose in GUI (cover_hardsubs is currently **unreachable from the GUI**) + CLI `--cover`. Inpainting stays parked. | M | — | Moving hardsubs make one band rect wrong — box stays the safe default, blur opt-in. | pytest coord-mapping; `cover_eval.py`: OCR the output band → 0 CJK for box, ≤10% of baseline for blur. |
| **N-22** | **Dub polish.** Speech-tuned rubberband options (probe `-h filter=rubberband`, graceful fallback); `loudnorm` on the dub bus (fallback speechnorm if it pumps between sparse lines); `aresample=48000` per segment + `-ar 48000` out (edge-tts is 24 kHz mp3). | S | — | loudnorm pumping on a mostly-silent bus — the A/B listen decides the fallback. | pytest locks the filter-string builder; ebur128 of two rendered samples within −16±1 LUFS; one A/B listen at 1.3×. |
| **N-8** | **Demucs "keep the music" mode.** `separate.py` runs demucs two-stems via **subprocess** (torch state out-of-process) inside `heavy_task()`, cached `<job>/bg.wav`; `audio_mode='keep_music'` routes the instrumental through the existing mix path; GUI combo + CLI + doctor check. All post-gate. | L | N-21 | Demucs torch pin vs funasr/faster-whisper — subprocess isolation + doctor check. | `duck_eval.py`: speech-band of bg.wav −10 dB vs source, music section within 3 dB; filter-graph unit test; one manual listen. |

**DoD (M7):** ASR timing measurably improves (eval script, reference clip) without a VRAM
violation; OCR proofread cost drops ≥50% at equal CER; overlong-line flags drop ≥50% before the
gate; music-preserving dub exists; every claim backed by a committed eval script, not vibes.

---

## Milestone 8 — Workflow & UX

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **N-19** | **Batch queue.** Headless `queue_state.py` (`<storage>/queue.json`, statuses pending/running/gated/failed) + multi-line URL input; MainWindow chains the next pending job through the SAME single worker slot (sequential heavy models); gated jobs park in "Dự án gần đây"; CLI `nargs='+'`. Widen `store.list_jobs` to show failed/pending job dirs (currently invisible — no GUI path to retry/clean them). | L | N-1, N-3 | The job/opts handoff is exactly the _resume-hijack bug class — queue owns the handoff, UI-thread only via the finished signal. | pytest QueueState + CLI two-URL run → two parked workdirs, gate honored, no output.mp4. One manual GUI auto-advance. |
| **N-23** | **Glossary editor dialog.** `save_glossary()` (atomic write) + a 2-column QDialog with scope selector (chung / dự án); entry points on Screen 1 and the editor toolbar. Complements N-15's suggestions. | M | — | Two scopes confusing — show the merged effective view or state "dự án ghi đè chung". | pytest save/load round-trip + offscreen construct test. |
| **N-24** | **Download fallback chain.** Refactor to `_ytdlp` + optional `_bbdown` (bilibili) / `_tiktokdl` (douyin) behind one `(job,url)→Path` contract; per-attempt logging, aggregated errors (keep the VN cookie hint), `metadata['downloader']`; config paths + doctor checks. **Web-verify both tools' current CLIs at implementation time** — TikTokDownloader churns. | M | — | Both tools' CLIs churn — every fallback optional; plain yt-dlp remains the untouched baseline. | pytest with monkeypatched downloaders (routing, fallback order, skip-unconfigured); acceptance: one real Douyin URL that fails yt-dlp today succeeds via fallback. |

**DoD (M8):** paste 5 URLs before lunch, edit 5 gated jobs after; glossary maintained without a
text editor; a Douyin cookie failure no longer ends the session.

---

## Backlog (research/design needed — not scheduled)

Per-line timing nudge (±ms) + lock; undo for drags/style/gain + redo stack; hardsub **inpainting**
(STTN/LaMa — N-16's blur is the pragmatic middle step); **vision-LLM OCR proofread** (suspect lines
+ frame crops; revisit after N-14 — opt-in upload per CLAUDE.md); karaoke word-highlight captions;
web-frontend feature parity (N-11 only fixes the crash; auto/OCR route + duck/volume knobs via API
are still desktop-only); Settings dialog expansion (cookies, storage dir, ffmpeg path, whisper
model); real waveform + follow-playback timeline; find/replace in the editor; packaging (PyInstaller
one-folder + ffmpeg download step); VieNeu-TTS provider; FireRedASR.

---

## Working rules (unchanged + additions)

- **Headless first.** Every backend/data-model change ships with a pytest construct/render check
  before the manual GUI smoke. GUI drag/paint gets a manual smoke only.
- **Guard the goldens.** test_write_ass byte-locks `write_ass`; N-2 adds round-trip locks for time
  formatting. Never edit these paths without re-running the suite; re-bless deliberately.
- **Cue identity is positional** (index = vi.srt order = override list position). N-17's zh_ref
  sidecar rides the same rule — never introduce UUID keys.
- **Providers are pluggable** — one file + registry; capability differences are declared flags
  (N-12), never runtime surprises.
- **Verify model ids/pricing via web at implementation time** — several ids in this plan are
  flagged, none were trusted from memory.
- **8 GB VRAM: heavy models run sequentially** — through `heavy_task()` once N-21 lands.
- **The human edit gate stays before TTS/render.** Nothing in this plan moves it; N-3/N-13 exist to
  protect what the human did there.
