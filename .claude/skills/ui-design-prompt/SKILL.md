---
name: ui-design-prompt
description: (Re)generate the UI design prompt for vizsup that the user hands to Claude (or another design tool) to produce the app interface. Use when the user wants to design/redesign the UI or update the design brief. Output goes to docs/ui-design-prompt.md.
---

# Generate the UI design prompt

Produce a self-contained design brief the user can paste into Claude to get a React UI design, and write it to [docs/ui-design-prompt.md](../../../docs/ui-design-prompt.md).

## What the brief must cover

**App**: vizsup — a personal, local desktop tool (run in the browser at localhost) that turns a Chinese short video into one with Vietnamese subtitles + Vietnamese voiceover. Single user, dark-mode-friendly, dense but calm.

**Three screens / flow** (the human-edit gate is the centerpiece):
1. **Input / Job** — paste a Douyin/Bilibili URL (or drop a file); per-stage **provider dropdowns** (ASR, translation draft + optional refine, TTS) populated from `GET /api/providers` (each option shows `display_name` + `cost_note`, disabled when `available` is false); a "Start" button; a live **progress** panel (stages: download → detect → subtitle → translate) with a **cancel** button.
2. **Subtitle Editor (the gate)** — the most important screen. An HTML5 `<video>` player, a **waveform** (wavesurfer.js), a **draggable timeline track** of cue blocks (react-timeline-editor), and a synced **table** of lines (index, start, end, Vietnamese text — all editable), with the original Chinese shown read-only beside each line. Play/pause, click a cue to seek, edit text/timing, split/merge lines. A prominent **"Approve & Render"** button — nothing downstream runs until approval.
3. **Render / Export** — options: subtitle style (font, size, opaque box to cover CN hardsubs), TTS voice, audio mix (replace original / mix / duck), then render; show progress and a **download** for the final MP4 plus the editable `.srt`/`.ass`.

**Settings panel** — API keys per provider; default provider per stage.

**Constraints to state in the brief**: the edit step sits AFTER translation and BEFORE TTS/render; providers are user-selectable per stage; Vietnamese diacritics must render (use a Vietnamese-capable font in the editor too). Stack: React + Vite + Tailwind talking to a FastAPI backend over REST + WebSocket/SSE.

## Steps
1. Read this repo's `docs/architecture.md` and `backend/app/main.py` for the API contract.
2. Write the brief to `docs/ui-design-prompt.md` as a single copy-pasteable prompt (include the screen list, component hints, the `/api/providers` shape, and the constraints above).
3. Keep it implementation-light — describe behavior and layout, not code.
