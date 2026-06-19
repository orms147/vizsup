# Architecture

## Pipeline data flow

```
INPUT (Douyin/Bilibili URL or local file)
  │
  ▼
DOWNLOAD ───────────► source.mp4 + metadata.json
  │   yt-dlp (cookies, domain-routed); fallback BBDown/TikTokDownloader
  ▼
HARDSUB DETECT  ── PP-OCRv5 detection-only on bottom-third crop
  │
  ├── hardsubs present ─► OCR (VideOCR/PP-OCRv5, SSIM dedup) ─► cn.srt ─► (LLM proofread)
  │
  └── no hardsubs ──────► ASR (WhisperX timing + FunASR transcript) ─► cn.srt
  │
  ▼
TRANSLATE (two-pass, pluggable) ── strip timecodes · batch ~50 · context+glossary
  │   pass1 CN-native draft → pass2 frontier refine → re-attach timecodes
  ▼
vi.srt
  │
  ▼
⛔ HUMAN EDIT GATE  ── React timeline+table editor
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
| Frontend (editor) | [frontend/](../frontend/) |

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
- **Working now (P2 — ASR, no-hardsub path)**: `faster_whisper` provider (default; `pip install -e ".[asr]"`) — verified transcribe path (model load → segments → cues). `whisperx_funasr` = FunASR Paraformer accuracy option (`.[asr-accurate]`). So "paste link → auto Vietnamese subtitles" works (download → ASR → translate → edit gate), given ffmpeg + a translation key.
- **Stubbed (with TODOs)**: hardsub detect (P5/M2), OCR (P5/M2), WhisperX word-level alignment refinement, TTS providers FPT/Azure/Google, VieNeu-TTS local provider (parked), the React web editor (optional).

> ffmpeg must be on PATH (audio extract + render). Translation needs one provider API key for real Vietnamese (passthrough is offline test-only). For hardcoded-Chinese-sub videos with little speech, OCR (M2) is still pending — supply a `.srt` for those today.

See the build roadmap in the plan and per-stage notes in [research.md](research.md).
