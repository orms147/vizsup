# UI design prompt — vizsup (desktop, PySide6) — RECOMMENDED

> Copy the block below and paste it to Claude to design **and build** the desktop GUI.
> A web (React) variant brief is kept at the bottom for reference.
> Regenerate with the `/ui-design-prompt` skill.

---

You are designing and implementing the GUI for **vizsup**, a personal, local desktop app that turns a **Chinese short video** (Douyin / Bilibili) into the same video with **Vietnamese subtitles + Vietnamese voiceover (lồng tiếng)**. Single user, runs on the user's own Windows PC (RTX 4070, 8GB). Build it with **PySide6 (Qt 6)** in Python — one process, no web server, no JavaScript.

A Python backend already exists and is UI-agnostic — **call it directly** (no HTTP). Key modules (package `app`, under `backend/`):
- `app.models.Job` — a working dir per video. Construct `Job(id=..., work_dir=Path("storage/<id>"), url=...)`. It exposes paths: `job.source_video`, `job.cn_srt`, `job.vi_srt`, `job.subs_ass`, `job.output_video`, `job.tts_dir`.
- `app.models.Cue` — `{index:int, start:float, end:float, text:str}` (seconds). This is the in-memory subtitle unit.
- `app.providers.registry.list_providers()` → `{"translation":[...], "tts":[...], "asr":[...]}`; each item `{name, display_name, cost_note, local, available}`. Use this to populate dropdowns; disable options where `available` is false.
- Pipeline stage functions (all synchronous — run them in a **QThread**, never on the UI thread):
  - `app.pipeline.download.download(job)` — yt-dlp; fills `job.source_video` + `job.metadata`.
  - `app.pipeline.asr.transcribe(job, provider=...)` — writes `job.cn_srt` (ASR; may be a stub initially).
  - `app.pipeline.translate.translate(job, draft=..., refine=...)` — reads `job.cn_srt`, writes `job.vi_srt`.
  - `app.pipeline.tts.synthesize(job, provider=..., voice=...)` → list[(Cue, Path)].
  - `app.pipeline.assemble.assemble(job, segments, replace_audio=..., cover_hardsubs=..., font=...)` → `job.output_video`.
  - `app.pipeline.srt.parse_srt(path)`, `write_srt(cues, path)`, `cues_to_dicts`, `dicts_to_cues`.
- Config/keys: `app.config.settings` (loaded from `.env`).

## The core principle to honour
There is a **human edit gate**: the user reviews/fixes the Vietnamese subtitles **after translation and before any TTS/render**. The app's flow and visuals must make this explicit — nothing downstream runs until the user clicks **Approve & Render**.

## App structure (3 views in a QStackedWidget + a Settings dialog)

**View 1 — Input / Setup**
- A text field for a Douyin/Bilibili URL, and an "Open local file…" button (QFileDialog) as an alternative source.
- An optional "Use existing Chinese .srt" file picker (skips ASR — useful before ASR is implemented).
- Four dropdowns (QComboBox), populated from `list_providers()`, each row showing `display_name — cost_note`, disabled if `available` is false:
  - **ASR engine** (no-hardsub path)
  - **Translation — draft**
  - **Translation — refine** (optional; include a "— none —" entry)
  - **TTS voice engine**
- A **Start** button. On click: build a `Job`, start a QThread that runs `download → (srt or asr) → translate`, then switch to View 2.
- A live **progress area**: a labelled progress bar + a scrolling log, and a **Cancel** button. Progress comes from Qt signals emitted by the worker between stages (stages: download, subtitle, translate). Cancel sets a flag the worker checks between stages.

**View 2 — Subtitle Editor (THE GATE — build this in the most detail)**
- **Video player**: `QMediaPlayer` + `QVideoWidget` showing `job.source_video`, with play/pause, a seek slider, and a current-time readout.
- **Subtitle table**: a `QTableView`/`QTableWidget` with columns — `#` | `start` | `end` | **Tiếng Việt (editable)** | 中文 (read-only reference). Editing the VN text or the start/end updates the model; double-click a row seeks the video to that cue's start. Times shown as `mm:ss.cs`, editable.
- **Timeline strip** under the video: a `QGraphicsView` showing cue blocks on a time axis (optionally a simple waveform behind them via numpy+QPainterPath). Dragging a block's edges edits its start/end and syncs the table; clicking a block selects its table row and seeks. (If the waveform is too much, ship the draggable blocks first.)
- Toolbar buttons: **Split**, **Merge**, **Add line**, **Delete line**, and a "too long to dub" warning icon on rows whose VN text likely won't fit the time slot.
- Autosave edits to `job.vi_srt` (via `write_srt(dicts_to_cues(rows), job.vi_srt)`) on change/debounce.
- A prominent **"Approve & Render →"** button (bottom-right) that advances to View 3. Make it visually clear this is the commit point.

**View 3 — Render / Export**
- Subtitle style controls: font (default "Be Vietnam Pro"; must render Vietnamese diacritics), font size, and a **"Cover leftover Chinese hardsubs (opaque box)"** checkbox → `cover_hardsubs`.
- TTS **voice** dropdown (from the chosen engine's `voices()`), optional rate.
- **Audio mix** radio group: Replace original / Mix over original / (Duck — future) → maps to `replace_audio`.
- A **Render** button → QThread runs `tts.synthesize` then `assemble.assemble`, with a progress bar. On finish: an **"Open output folder"** button and a small inline player/preview of `job.output_video`, plus the exported `vi.srt` / `subs.ass` paths.

**Settings dialog**
- Password fields for each provider API key (DeepSeek, Anthropic, Zhipu/GLM, Gemini, DashScope/Qwen, DeepL, FPT, Azure). Save to `.env` (or a settings store).
- Default provider per stage. Re-query `list_providers()` after saving so availability refreshes.

## Threading & UX rules
- All pipeline calls run in a **QThread worker** that emits Qt signals: `stage_started(str)`, `progress(float, str)`, `stage_done(str)`, `failed(str)`, `gate_reached()`, `render_done(str)`. The UI never blocks; Cancel is honoured between stages.
- 8GB VRAM: heavy models (WhisperX/FunASR later) run sequentially — the worker is single-threaded by design.
- Show errors inline (e.g. yt-dlp Douyin cookie failures, ffmpeg missing) without crashing.

## Visual style
- Dark theme, calm and dense — a focused pro media tool, not a consumer app. A left vertical step rail (1 Input · 2 Edit · 3 Render) reflecting progress, content on the right. Comfortable spacing, monospace for timecodes, a Vietnamese-capable UI font throughout. Keyboard shortcuts in the editor (space = play/pause, arrows = nudge selected cue time).

## Deliverable
A runnable PySide6 app: a `MainWindow` with the QStackedWidget of the three views, the QThread `PipelineWorker`, the editor widgets worked out in the most detail, and the Settings dialog — wired to the `app.*` functions above. Provide the file layout (e.g. `desktop/main.py`, `desktop/views/`, `desktop/worker.py`, `desktop/widgets/subtitle_table.py`, `desktop/widgets/timeline.py`) and the code. Use Qt signals/slots idiomatically; keep widgets small and uniform.

---

## Web alternative (only if you later want remote access / a fancier timeline)
A FastAPI backend already exposes the same flow (`/api/providers`, `POST /api/jobs`, `GET/PUT /api/jobs/{id}/subtitles`, `POST /api/jobs/{id}/render`, `WS /ws/jobs/{id}`). For a React + Vite + Tailwind UI, give Claude the same three-screen brief above but target the browser, using `<video>` + wavesurfer.js + react-timeline-editor, talking to those endpoints over fetch + WebSocket. The earlier React scaffold lives in `frontend/`.
