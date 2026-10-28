# Overnight run — 2026-10-29 (log for the owner to check)

Owner asleep ~4–5 h. Coordinator: this session ("Vizsup agent đánh giá kế hoạch (fork)").
Workers: **session 1** (research: CapCut / Adobe / dubbing tools), **session 2** (UX audit of
the running app). Branch: `api-optimization` (push allowed, never `main`, never force).

## Permissions given (2026-10-29)
- Commit / push work branches. Install new libraries — every install logged below.
- Touch real projects, test the real app.
- Budgets: Anthropic API ≤ $50, Meta API ≤ $50 (strongest model; tools/evaluation, NOT the
  translation model), the app's own models (OpenRouter) ≤ ~$3 for testing.
- Human gate: the owner's 4 real episodes are NOT approved by me; end-to-end tests run on
  COPIES in a test storage.

## Plan
0. Finish the pending work: watermark feature (tests → commit); batch render → join
   episodes 1–8 → add episode 9 (on copies).
1. Research (sessions 1+2 in parallel) → `docs/plan/research/*.md`.
2. Coordinator: prioritised roadmap → `docs/plan/H-roadmap.md`.
3. Build the top items (separate git worktrees per session, coordinator reviews/merges,
   full test suite + real app check before every push).

## Spend
| when | what | provider | ≈ $ |
|---|---|---|---|

## Libraries installed
| when | package | why |
|---|---|---|

## Log
- 00:00 — log created; auto-continue check-in scheduled (~every 40 min while idle).
- 00:20 — Research workers: "session 1"/"session 2" turned out to be OTHER projects
  (WriTory…); briefs withdrawn with an apology. Reassigned to the vizsup sessions
  **vizsup-9c** (competitor research → docs/plan/research/competitors.md) and
  **vizsup-2e** (UX audit → docs/plan/research/ux-audit.md).
- 00:25 — commit dcc9db4 feat(render): translator watermark (pending task finished,
  1005 tests pass).
- 00:35 — workers renamed by the owner: 'sesion 1 vizup' (= vizsup-9c, competitor research) and 'sesion 2 vizup' (= vizsup-2e, UX audit) — both busy on their briefs.
- 00:36 — E2E test on COPIES (scratchpad/e2e_storage): 4 episodes approved there (the owner's real projects stay unapproved), batch render started in the app (template: owner's short_3min settings + test watermark 'Vietsub: Kênh Thử', hop corners).
- 00:50 — sesion 1 vizup: competitor research done → docs/plan/research/competitors.md
  (top: TTS pronunciation dictionary, series profile carried episode to episode,
  per-line voice/gender, 9:16 blur-fill, Netflix-style timing post-pass, final loudness).
- 00:51 — batch dialog: titles elide ("Tập 1–5 · …"), the settings line mentions the
  watermark (uncommitted, tests pass).
- 00:55 — sesion 1 vizup → build task: TTS spoken-text normaliser + pronunciation
  dictionary on worktree C:\Work\AI\vizsup-wt\tts-normalize, branch feat/tts-normalize
  (coordinator reviews + merges).
- 01:05 — sesion 2 vizup: UX audit done → docs/plan/research/ux-audit.md (+ shots). Then
  build task: UX quick wins (audit items 1,5,6,7,9,11,13,14,15) on worktree
  C:\Work\AI\vizsup-wt\ux-quickwins, branch feat/ux-quickwins.
- 01:06 — commit c657b60 (batch dialog polish + research docs).
- 01:30 — commit 345bda5 feat(render): final-mix loudness (-14 LUFS, true peak -1.5) +
  blurred 9:16 bars (render page "Nền hai bên": hình mờ / đen). Real render checked.
- 01:35 — merged feat/tts-normalize (sesion 1 vizup, 18c0ce8): backend/app/pipeline/speak.py
  — the dub reads numbers/units/dates/money/abbreviations the Vietnamese way;
  pronounce.json (global + per project). Subtitles unchanged. 1108 tests pass. NOTE: lines
  with digits / % / units will re-voice once on the next render (cache key = spoken text).
- 01:40 — sesion 1 vizup → next: timing post-pass (gaps / min gap / min duration, optional
  scene-cut snap), branch feat/timing-pass.
- 00:35 — commit af2f480 fix(tts): edge-tts answered ~40 % of requests "no audio" at random
  tonight; each paused every worker 5–60 s (batch crawled at ~5 lines/min). Lines now retry
  alone first; 6 in flight. 30 real lines: 122 s → 57 s, 0 failed.
- 00:35 — merged feat/timing-pass (sesion 1, 59946aa): timing post-pass after ASR/OCR
  (2-frame gaps, chained short gaps, min duration; optional scene snap) + editor menu action
  + input option "Chỉnh thời gian chuẩn". 1139 tests pass. → 5192596.
- 00:35 — sesion 1 → next: "Bộ phim" series profile (branch feat/series).
- NOTE: times above 00:20–01:40 were estimates (wrong); real clock from here on.
- 00:40 — E2E: demo app (old code, batch crawling on the edge-tts bug) killed hard =
  crash test. Relaunched on the new code: "Làm tiếp việc còn dở?" appeared, accepted → the batch
  resumed at 1/4 · 40 % (TTS cache kept). Episode 9 started in the main slot at the same time.
- 00:40 — CLAUDE.md updated for tonight's features (da52cb7).
- 00:45 — commit 7e6f6a3 feat(render): "Thẻ tập" — "Tập {n}" chip (first 5 s) + "Còn tiếp ·
  Tập {next}" end card (last 4 s), resolved from each title, fits a batch (roadmap wave 2 #5).
- 00:45 — merged feat/ux-quickwins (sesion 2, 8 commits: D = Đã xem + next, Ctrl+↑/↓ in the
  text box, one-row find bar, right-click menus, "Xem N câu" in approve dialog, render cards height
  fix, status line / step rail, library keys, new shortcut sheet) → 212d703.
- 00:45 — merged feat/series (sesion 1: "Bộ phim" profile in storage/_series/, glossary +
  characters + brief + pronounce inherited, learn on approval, previous episode's last 8 lines as
  context, input "Bộ phim" combo) → 243b53d. Full suite 1214 passed, ruff clean.
- 00:45 — next: sesion 1 → series dialog + CLI --series + per-line voice by gender;
  sesion 2 → compact cue list + 9:16 layout, overview strip, library chips/sort; me → model catalog
  auto-update, E2E.
- 00:51 — commit 62eaf57 feat(update): model catalog auto-update (catalog/models.json on the
  main branch, checked daily + Settings → "Cập nhật model mới"; only valid catalogs whose every model
  is live on OpenRouter; user picks always win; "Có vizsup X mới" notice). Live check: 456 OpenRouter
  models, every model the app uses is live. NOTE for owner: takes effect once catalog/models.json is
  on main (I never push main).
- 00:51 — E2E: ep 9 reached the gate (43 CN lines, 13 "Timing đoán"; Whisper's word times
  collapsed → repaired). ⚠ The ep-9 link (modal_id 7699919754521283890) is NOT 《末日校车》: it is a
  剑网3 game ad ("50级新世代！… 10月29日公测", no uploader) — the app downloaded exactly that id.
  Used it anyway to test the mechanics: approved the COPY, "Thêm vào hàng dựng" added it to the
  running batch (session file shows it queued).
