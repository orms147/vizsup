# Workstream C — Workflow & UX (remaining)  ← **ACTIVE WORKSTREAM**

Workstreams A and B have shipped; this is the live to-do list. Statuses were verified against code
2026-07-02 and re-verified 2026-09-11 (DOC-1 done, UX-6 parked — see each task).

Recommended order: **UX-4** (find/replace, [S], biggest per-job win) → **UX-3** (undo gaps + redo)
→ **UX-5** (settings fields) → UX-2 / UX-1 / UX-7 by user preference. UX-6 is parked.

---

## ✅ DOC-1 [S] Fix docs that contradict the code — DONE

`CLAUDE.md` and `docs/dev-plan.md:158` were corrected on 2026-07-02 (same commit as this handbook);
items 1-4 below landed in `108d062`; the `docs/plan/` refresh (test counts, shipped workstreams,
false "does not exist" premises) landed 2026-09-11. Original list:
1. `docs/architecture.md:14-25` still describes VideOCR / WhisperX / a React editor — the
   implementation is RapidOCR frame sampling (`backend/app/pipeline/ocr.py:5-9`), faster-whisper /
   FunASR providers, and a PySide6 editor (`desktop/views/editor_view.py`).
2. `docs/architecture.md:56` links a `frontend/` directory that does not exist.
3. `docs/architecture.md:72` calls hardsub-detect + OCR "Stubbed (with TODOs)" — both fully
   implemented (`hardsub_detect.py`, `ocr.py`).
4. The ASR provider id `whisperx_funasr` is misleading (it is FunASR-only; its own docstring
   admits it, `backend/app/providers/asr/whisperx_funasr.py:5-7`). Do NOT rename the id (config
   compat — plan v2 decision); instead make `display_name`/docstrings state "FunASR (Paraformer)"
   plainly and note the fused provider is `funasr_aligned`.
Verify: re-read each doc after editing; grep for `VideOCR|React|frontend/` leftovers.

---

## UX-1 [L] GUI batch queue (was N-19 — CLI half done, GUI half not started)

Verified: `queue_state.py` absent; Screen-1 URL field is a single-line QLineEdit; `store.list_jobs`
(`desktop/store.py`) lists only dirs with vi.srt/output.mp4 — failed/pending jobs are invisible.
1. Headless core: `backend/app/pipeline/queue_state.py` (no Qt imports) — `QueueState` with items
   `[{url, job_id?, status: pending|running|gated|failed, error?}]` persisted to
   `<storage>/queue.json`; methods `add(urls)`, `next_pending()`, `mark(...)`.
2. Desktop: URL field accepts multiple lines (swap to QPlainTextEdit or split on whitespace);
   >1 URL → enqueue all, start the first; on worker `finished` (gate_reached → mark `gated` +
   `store.save_job(..., "await_edit")`; failed → mark `failed`), MainWindow auto-starts the next
   pending through the SAME single PipelineWorker slot (sequential heavy models — 8 GB rule).
   Gated jobs appear in "Dự án gần đây".
3. Widen `store.list_jobs` to include dirs with job.json in failed/pending stages so queue items
   are visible and deletable.
Sharp edge: the job/opts handoff is the `_resume`-hijack bug class — the queue owns the handoff;
touch MainWindow state only on the UI thread via the finished signal. Prereqs already landed:
editor flush on job-switch (N-1), artifact stage-skipping (N-3).
Verify: pytest on QueueState (add/next/mark/persist/reload); CLI already covers multi-URL; one
manual GUI auto-advance smoke.

---

## UX-2 [M] Per-line timing nudge (±ms) + lock (backlog item — not started)

Editor gains, per selected cue: nudge start/end by ±50/±200 ms buttons (or Shift+arrow on the
timeline), and a "khóa" toggle stored in the dub-overrides sidecar
(`backend/app/pipeline/sidecars.py` — extend the overrides dict with `lock: bool`).
`assemble._plan_segments` treats locked cues as immovable: never push a locked cue later (trim the
previous instead). Keep `EditorCue` positional identity. Verify: pytest on `_plan_segments` with
locked cues (extend `test_plan_segments.py`); offscreen editor test for the sidecar round-trip.

---

## UX-3 [M] Undo completeness + redo (backlog — partial: undo exists, gaps verified)

`editor_view._snapshot/_undo_action` cover split/merge/add/delete + table edits, but SKIP timeline
drags, per-line style changes, and gain/mute edits; there is no redo stack.
1. Call `_snapshot()` at the start of drag-commit (`_on_cue_dragged` path / `dragCommitted`),
   `_on_style_changed` (per-line branch), `_on_gain`, `_on_mute`.
2. Add `_redo_stack` (cleared on new edit); Ctrl+Z / Ctrl+Y shortcuts.
Verify: offscreen pytest — drag→undo restores times; gain→undo restores; undo→redo round-trips.

---

## UX-4 [S] Find/replace in the editor (backlog — not started)

Toolbar "🔍" → small bar: find text (vi column), replace, replace-all; wraps `table` selection.
Pure find logic testable on the cues list. Verify: offscreen pytest (replace-all count, case
sensitivity toggle).

---

## UX-5 [S] Settings dialog expansion (backlog — not started)

`desktop/settings_dialog.py` exposes only API keys. Add fields (all already exist in
`backend/app/config.py` settings): `YTDLP_COOKIES_FROM_BROWSER` / `YTDLP_COOKIES_FILE`,
`VIZSUP_STORAGE_DIR`, `FFMPEG_PATH`, `WHISPER_MODEL`, `BBDOWN_PATH`, `ZHIPU_MODEL`. Persist the
same way keys are persisted today (the dialog writes .env at repo root). Verify: offscreen
construct test — populated fields round-trip to .env.

---

## ⏸ UX-6 [M] API (FastAPI) parity: auto/OCR route + cover_mode — **PARKED, do not start**

**Parked 2026-09-11 by user decision.** The FastAPI surface has **no consumer**: there is no
`frontend/`, and both the desktop worker (`desktop/worker.py:19`) and the CLI import `app.*`
directly. The only caller of `/api/jobs` in the repo is one line in `tests/test_api_contract.py`.
Spending effort — and desktop-regression risk — on parity for a front-end that does not exist is
not worth it.

**Do not "fix" the gaps below, and do not propose a unified headless runner to close them.** The
duplication between `cli.py:67-141`, `jobs.py:147-221` and `desktop/worker.py:82-273` is known and
accepted: the desktop is the PRIMARY UI per `CLAUDE.md`, it works, and a shared-runner refactor
would put it at risk to serve dead code. (Note the heavy-model lock is **not** duplicated — it lives
inside the stages themselves: `asr.py:38`, `ocr.py:304`, `hardsub_detect.py:142`, `separate.py:42`.)

**Un-park only if** a real HTTP consumer appears (the React frontend in `D-backlog.md`). Then do
exactly this, and nothing larger:
1. `CreateJob` gains `source: "auto"|"asr"|"ocr"|None`, `ocr_region`, `ocr_proofread`; `jobs.py`
   mirrors the worker's auto→(OCR|ASR) routing, wrapped in `heavy_task` (already imported for ASR).
2. `RenderOptions.cover_mode: str | None` → `_render_kwargs` passes it through.
3. Extend `test_api_contract.py` with fakes for the OCR route.

Already done separately (2026-09-11, *not* parity work — just honesty): `cancel=` is now propagated
from `jobs.py` into `translate`/`tts`, and `_render_kwargs` documents which kwargs it deliberately
omits.

---

## UX-7 [M] Douyin download fallback (was N-24 remaining half)

BBDown (Bilibili) is wired; Douyin has no fallback (`download.py` falls straight through to the
yt-dlp cookie error). TikTokDownloader's CLI churns — **web-verify its current invocation the day
you implement** (that's why it was deferred). Wrap whatever works behind the same
`(job, url) -> Path` contract as `_bbdown`, behind a `TIKTOKDL_PATH` config knob, optional,
yt-dlp stays the untouched baseline. doctor check + monkeypatched routing tests mirroring the
BBDown ones.
