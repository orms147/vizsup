# Improvement roadmap — 2026-10-29 (overnight run)

Sources: [research/competitors.md](research/competitors.md) (CapCut, Premiere, Resolve, Descript,
Subtitle Edit/Aegisub, ElevenLabs/Rask/HeyGen) and [research/ux-audit.md](research/ux-audit.md)
(every screen at 1366×768 / 1920×1080). Ranked by value for the owner's real workflow — reposting
Chinese AI-anime **series** (many short episodes) in Vietnamese — divided by effort.

## Wave 1 — tonight (three parallel tracks, each on its own branch, merged by the coordinator)

| Track | Item | Why | Effort | Branch / owner |
|---|---|---|---|---|
| A | **Spoken-text normaliser + pronunciation dictionary** for the dub (numbers, units, abbreviations; global + per project; cache-aware) | edge-tts misreads numbers/symbols; fix once, every episode | S | `feat/tts-normalize` · sesion 1 vizup |
| B | **Editor/library quick wins**: "Đã xem" shortcut + next pending, one-row find bar, Ctrl+↑/↓ from the text box, right-click menus, "Xem N câu" in the approve dialog, stale status text / rail, library double-click/Enter/Delete, complete shortcut sheet, verify render mode cards | ~80 mouse trips less per 300-line episode | 9 × S | `feat/ux-quickwins` · sesion 2 vizup |
| C | **Even loudness per episode** (final mix, true-peak limit) | joined episodes jump in volume today (only the dub track is normalised) | S | coordinator |
| C | **9:16 blur-fill** instead of black bars | most sources are landscape; one ffmpeg graph | S | coordinator |
| C | **E2E test of the series flow** on copies: batch render → join 1–8 → add 9 | the pending task | — | coordinator |

Status 01:00: wave 1 all merged (A 18c0ce8→356db8f, B 212d703, C af2f480 + loudness/blur).

## Wave 2 — next (after wave 1 is merged)

1. ✅ **"Bộ phim" series profile** — merged 243b53d (sesion 1). Follow-up in progress: edit dialog
   for brief / characters / pronounce, CLI `--series`.
2. ⏳ **Per-line voice by gender** — sesion 1, branch feat/series-voices.
3. ⏳ **Compact cue list + 9:16-aware layout** (ux E1, E5/R3) — sesion 2, branch feat/ux-wave2.
4. ✅ **Timing post-pass** — merged 5192596 (sesion 1).
5. ✅ **Episode chip + end card** — 7e6f6a3 (coordinator).
6. ⏳ **Whole-episode overview strip** — sesion 2, feat/ux-wave2.
7. ⏳ Library status chips + episode sort (L1) — sesion 2, feat/ux-wave2.
8. ✅ **Model catalog auto-update** (owner's question 2026-10-28) — 62eaf57; live once
   `catalog/models.json` is on main.

## Later / maybe
Animated captions (sentence level first), platform safe-zone overlay (needs video_canvas, wait for
the 9:16 layout work), ✅ AI post title/hashtags (584e66d), Vietnamese common-error fixer,
CSV export/import, trim head/tail (risky: every sidecar shifts), signed installer + update download
(the catalog already announces new versions).

## Not building
Lip-sync, removing other creators' watermarks, anti-duplicate-detection tricks, subject-tracking
reframe, voice cloning (out of scope v1).
