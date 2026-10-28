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

## Wave 2 — next (after wave 1 is merged)

1. **"Bộ phim" series profile** — glossary, characters/xưng hô, voice, style, watermark, render
   settings, pronunciation dictionary carried to the next episode; the previous episode's last lines
   as translation context. (M, highest value for series.)
2. **Per-line voice by gender** (pitch.py per line on vocals.wav, editable chip; edge-tts has one
   male + one female vi-VN voice). (M)
3. **Compact cue list + 9:16-aware layout** (ux E1, E5/R3). (M)
4. **Timing post-pass** (merge gaps < 0.5 s, min gap / min duration in frames, optional scene-cut
   snap). (S–M)
5. **Episode chip + end card ("Xem tập tiếp")** via the existing Tag events. (S)
6. **Whole-episode overview strip** on the timeline (flags / reviewed). (M)
7. Library status chips + episode sort (L1). (M)

## Later / maybe
Animated captions (sentence level first), platform safe-zone overlay, AI post title/hashtags,
Vietnamese common-error fixer, CSV export/import, trim head/tail (risky: every sidecar shifts).

## Not building
Lip-sync, removing other creators' watermarks, anti-duplicate-detection tricks, subject-tracking
reframe, voice cloning (out of scope v1).
