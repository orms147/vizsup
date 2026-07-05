# Architecture

## Pipeline data flow

```
INPUT (Douyin/Bilibili URL or local file)
  │
  ▼
DOWNLOAD ───────────► source.mp4 + metadata.json
  │   yt-dlp (cookies, domain-routed); fallback BBDown/TikTokDownloader
  ▼
HARDSUB DETECT  ── RapidOCR (ONNX PP-OCRv5+) detection on ~16 bottom-third crops
  │
  ├── hardsubs present ─► OCR (RapidOCR frame sampling · per-cue confidence → ocr_report.json) ─► cn.srt ─► (LLM proofread, suspect lines only — opt-in)
  │
  └── no hardsubs ──────► ASR (faster-whisper default; funasr_aligned = FunASR Paraformer + word timing) ─► cn.srt
  │
  ▼
TRANSLATE (two-pass, pluggable) ── strip timecodes · batch ~50 · context+glossary
  │   pass1 CN-native draft → pass2 frontier refine → re-attach timecodes
  ▼
vi.srt
  │
  ▼
⛔ HUMAN EDIT GATE  ── PySide6 timeline+table editor (desktop/)
  │   review/fix text, register, line breaks, timing. NOTHING below runs until approved.
  ▼
TTS (pluggable, default edge-tts vi-VN) ─► tts/seg_0001.mp3 ...
  │
  ▼
FIT + MIX  ── place each segment at absolute start · atempo ≤1.3× · silence padding
  │           replace / mix / duck original audio
  ▼
RENDER  ── FFmpeg burns styled subs.ass (VI font, opaque box) ─► output.mp4
  │         also export editable vi.srt / subs.ass
  ▼
OUTPUT (output.mp4 + subtitles)
```

Each arrow is a **resumable stage**; all state lives in the job's `work_dir`
(`storage/<id>/`). Times are absolute seconds → no cumulative dub drift.

## Code map

| Concern | Module |
|---|---|
| Data types (`Cue`, `Job`) | [backend/app/models.py](../backend/app/models.py) |
| Settings / keys | [backend/app/config.py](../backend/app/config.py) |
| Subtitle (de)serialize (SRT/ASS) | [backend/app/pipeline/srt.py](../backend/app/pipeline/srt.py) |
| Stages | [backend/app/pipeline/](../backend/app/pipeline/) — `download`, `hardsub_detect`, `ocr`, `asr`, `translate`, `tts`, `assemble` |
| Provider interfaces | [backend/app/providers/base.py](../backend/app/providers/base.py) |
| Provider registry (UI source of truth) | [backend/app/providers/registry.py](../backend/app/providers/registry.py) |
| CLI (headless smoke path + gate) | [backend/app/cli.py](../backend/app/cli.py) |
| FastAPI app | [backend/app/main.py](../backend/app/main.py) |
| Job worker (P3) | [backend/app/jobs.py](../backend/app/jobs.py) |
| Desktop editor (primary UI — edit gate) | [desktop/](../desktop/) — PySide6, calls `app.*` directly |

## Backend shape (target, P3)

- **FastAPI** exposes each stage; `GET /api/providers` feeds the UI dropdowns.
- A single in-process **job worker** sequences stages, streams progress over
  **WebSocket/SSE**, and checks a **cancel flag** between stages.
- The pipeline **parks at `AWAIT_EDIT`**; the editor loads/saves `vi.srt`;
  `POST /render` resumes into TTS + assemble.

## Implementation status

- **Working now (P1)**: download (yt-dlp), translate orchestrator (passthrough + LLM providers), TTS orchestrator (edge-tts), FFmpeg assemble, SRT/ASS utils, CLI (`run`/`render`/`providers`), provider registry.
- **Working now (P3)**: async job worker (stages in a thread executor, progress pub/sub, cancel between stages); FastAPI endpoints — `/api/providers`, `POST /api/jobs`, `GET /api/jobs/{id}`, cancel, `GET/PUT /api/jobs/{id}/subtitles` (the edit gate), `POST /api/jobs/{id}/render`, artifact download, and `WS /ws/jobs/{id}` for live progress. Verified end-to-end over a live uvicorn server (job runs to the edit gate; subtitles load/save).
- **Working now (P4 — PySide6 desktop, the primary UI)**: `desktop/` — 3 screens (Input · Subtitle Editor · Render) + Settings dialog, ported from the "Dark Editor Design", calling `app.*` directly via a `QThread` `PipelineWorker` (progress + cancel). Subtitle editor = video (`QMediaPlayer`) + editable table + custom-painted timeline (ruler/waveform/cue blocks/playhead, drag + zoom), autosaving `vi.srt`. Verified: constructs + paints headlessly with the real backend. Run: `python -m desktop.main`.
- **Working now (P2 — ASR, no-hardsub path)**: `faster_whisper` provider (default; `pip install -e ".[asr]"`) — verified transcribe path (model load → segments → cues). `funasr_aligned` = FunASR Paraformer transcript fused with word-level timing (`.[asr-accurate]`); the legacy id `whisperx_funasr` is FunASR-only. So "paste link → auto Vietnamese subtitles" works (download → ASR → translate → edit gate), given ffmpeg + a translation key.
- **Working now (P5/M2 — hardsub path)**: `hardsub_detect` (RapidOCR detection over sampled bottom-third crops → auto OCR-vs-ASR routing) and `ocr` (RapidOCR frame sampling → cn.srt with per-cue confidence in `ocr_report.json`, optional suspect-only LLM proofread). Auto-detect (`--source auto`) and OCR route are wired in the CLI and desktop worker.
- **Stubbed / parked**: WhisperX word-level alignment refinement (funasr_aligned falls back to faster-whisper timing today); TTS providers FPT/Azure/Google; VieNeu-TTS local provider (parked). No React web editor exists (parked in docs/plan/D-backlog.md).

> ffmpeg/ffprobe resolve at runtime via `app.ffmpegutil` (not required on PATH). Translation needs one provider API key for real Vietnamese (passthrough is offline test-only). Hardcoded-Chinese-sub videos now go through OCR automatically; you can still supply a `.srt` to skip OCR/ASR.

See the build roadmap in the plan and per-stage notes in [research.md](research.md).
