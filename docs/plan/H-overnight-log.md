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
