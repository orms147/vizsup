# Workstream C — Workflow & UX (remaining)  ← **ACTIVE WORKSTREAM**

Workstreams A and B have shipped; this is the live to-do list. Statuses were verified against code
2026-07-02 and re-verified 2026-09-11 (DOC-1 done, UX-6 parked — see each task).

Shipped 2026-09-11: **UX-3**, **UX-4**, **UX-5** (see each task). **UX-6 is PARKED.**
Remaining, in recommended order: **UX-2** (timing nudge + lock) → **UX-1** (GUI batch queue)
→ **UX-7** (Douyin download fallback).

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

## ✅ UX-3 [M] Undo completeness + redo — SHIPPED `84ae480`

All four gaps closed, plus `_redo_stack` on Ctrl+Y / Ctrl+Shift+Z. Two things worth remembering:
- `Timeline.cueChanged` fires from `mouseMoveEvent` (per pixel), so the snapshot hangs off a NEW
  `Timeline.dragStarted` signal emitted in `mousePressEvent` — one undo step per gesture. Same
  shape for the gain slider (`sliderPressed`, plus `not isSliderDown()` for keyboard/wheel steps).
- The mute snapshot lives INSIDE `_on_mute`, not on a second `toggled` connection: `_on_mute` is
  connected in `_cue_panel()`, which runs earlier, so a later connection captures the POST-edit
  state. If you add another snapshot point, check the connection order the same way.
Covered by `backend/tests/test_undo_redo.py` (13 tests).

---

## ✅ UX-4 [S] Find/replace in the editor — SHIPPED `6bc1327`

Ctrl+F / 🔍 toolbar button; Enter = find next (wraps); Esc closes. Search policy is pure, in
`desktop/cuemodel.py` next to `split_cue`/`merge_cues`: `find_matches`, `find_next`,
`replace_in_cue`, `replace_all`. Decisions: VI column only (zh is a read-only reference),
case-insensitive by default replacing the matched span, needle escaped so `(giảm)` is literal,
and a no-match replace-all never touches the undo/redo stacks.
Covered by `backend/tests/test_find_replace.py` (23 tests).

---

## ✅ UX-5 [S] Settings dialog expansion — SHIPPED `66fdb60`

All seven fields added as an `OPTIONS` table with file/dir browsers and editable combos. Note the
deliberately DIFFERENT save semantics: API keys keep "blank = unchanged" (they render masked, so a
stray clear must not destroy a key), while options write even when blank, because for them an empty
value IS the instruction. `vizsup_storage_dir` is typed `Path` and is assigned as one.
Covered by `backend/tests/test_settings_options.py` (9 tests). Its fixture blanks the real key
attrs first — the dialog prefills from the live settings object, i.e. the developer's real .env.

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
