# vizsup — Chinese→Vietnamese video subtitle + dubbing tool

Personal, local tool. Paste a **Douyin / Bilibili** URL → download → generate **Vietnamese subtitles** → **edit them (human gate)** → **Vietnamese voiceover (lồng tiếng)** → render a final MP4. Source videos may or may not have hardcoded (burned-in) Chinese subtitles.

Decision record and tool rationale: **[docs/research.md](docs/research.md)**. Pipeline data flow: **[docs/architecture.md](docs/architecture.md)**. Provider matrix: **[docs/providers.md](docs/providers.md)**.

## Pipeline (each stage is resumable; state on an absolute timeline)

`input → download → hardsub-detect → (OCR | ASR) → translate(2-pass) → ⛔HUMAN EDIT GATE → TTS → fit+mix → render`

- **download** — `yt-dlp` (cookies, domain-routed). Fallback: BBDown (Bilibili), TikTokDownloader (Douyin).
- **hardsub-detect** — RapidOCR over ~16 sampled bottom-third crops; enough frames with recognized Chinese text ⇒ hardsubs → OCR path, else ASR ("Tự động" in the GUI, `--source auto` in the CLI; manual override always available).
- **OCR** (hardsubs present) — RapidOCR (ONNX PP-OCRv5+) frame sampling → CN SRT with per-cue confidence (`ocr_report.json`) → optional LLM proofread (suspect lines only).
- **ASR** (no hardsubs) — default **cloud** `audio_llm`: Qwen-Omni (OpenRouter) writes the transcript, Whisper word timestamps (cloud, or local faster-whisper if installed) time it. Local engines (`faster_whisper`, `funasr_aligned`) are optional extras and NOT installed on this machine (removed 2026-10-01). Benchmark: `docs/providers.md`.
- **snap ("tự canh lời")** — local Silero VAD (`pipeline/vad.py`, ONNX, model downloaded once to `storage/_models`) → each fresh CN cue edge moves to the nearest voice start/stop of its OWN voice within ~1 s (`pipeline/snap.py`). Runs before translate (opt-out: GUI option / `--no-snap`); editor: A / Shift+A, one undo step. Weak on singing under loud music — such cues are left alone.
- **translate** — two-pass, provider-pluggable: CN-native draft → frontier refine. Strip timecodes locally; batch ~50 lines with context + glossary + video meta.
- **HUMAN EDIT GATE** — PySide6 timeline+table editor (desktop/). **Nothing downstream runs until the user approves.**
- **TTS** — provider-pluggable; default edge-tts vi-VN.
- **fit+mix** — place each VI segment at its absolute start, atempo to fit, pad silence; duck/replace original audio.
- **render** — FFmpeg burns styled `.ass` (bundled fonts via `fontsdir`); "Xem nhanh" 640p proof first; frame: source or TikTok 9:16; exports: named MP4 copy, `.srt`/`.ass`, soft-sub `.mkv` (`pipeline/export.py`).

## Hard constraints (learned from research — respect these)

- **Keep the human-edit gate AFTER translation and BEFORE TTS/render.** Never spend TTS credits or encode time on un-approved subtitles.
- **Providers are pluggable and UI-selectable.** Every translation/TTS/ASR engine implements the interface in `backend/app/providers/base.py` and registers in `registry.py`. Adding one = one file + register. Never hardcode a single provider in pipeline code.
- **ASR: timing ≠ accuracy.** WhisperX for word timestamps, a Chinese-native model (FunASR Paraformer) for the transcript. Don't use Whisper alone for Mandarin (CER 3–5× worse).
- **OCR: crop to the bottom third** before detection/recognition. Keep Google-Lens mode opt-in (it uploads frames).
- **Dub timing**: build VI audio on an **absolute timeline** with exact silence padding (no cumulative drift). Cap `atempo` at **~1.3×** (it skips samples above 2×); prefer shorter VI translations and `rubberband` over heavy speed-up.
- **Fonts**: subtitles need a **Vietnamese-diacritic Unicode font** (Be Vietnam Pro / Noto Sans). Use `BorderStyle=4` opaque box to cover leftover CN hardsubs.
- **Model ids**: use `deepseek-v4-flash`/`deepseek-v4-pro` (legacy `deepseek-chat`/`deepseek-reasoner` deprecate 2026-07-24). **GLM-5.2** is the current GLM. Verify any model id/pricing via web — don't trust memory.
- **8GB VRAM**: run heavy models (WhisperX, FunASR, Demucs) **sequentially**, not concurrently.
- **Out of scope (v1)**: Kuaishou & Xiaohongshu download (no reliable OSS path); voice cloning (stock voice is enough).

## Layout

```
backend/app/          pipeline + providers + FastAPI (Python) — UI-agnostic core
  pipeline/           one module per stage
  providers/          base.py, registry.py, translation/, tts/, asr/
desktop/              PySide6 (Qt) GUI — PRIMARY, built (calls app.* directly, no HTTP)
storage/              per-job working dirs (gitignored)
samples/              test clips
docs/                 research, architecture, providers, dev-plan; docs/plan/ = active handbook
.claude/              skills, agents, settings
```
(No `frontend/` exists — a React web UI is parked in `docs/plan/D-backlog.md`.)

## Commands

```bash
# backend (venv: backend/.venv)
uv pip install -e .            # or: pip install -e .  (from backend/)
python -m app.cli URL          # headless: download → subs → translate → gate; then `vizsup render`
python -m app.cli retime --workdir ../storage/<id>   # re-time an old job's lines to the audio (text kept, backups made)
uvicorn app.main:app --reload  # FastAPI server (SRT/ASR path; OCR route desktop/CLI-only)
python -m pytest -q            # from repo root (pytest.ini) — full headless suite

# checks
python -m app.doctor           # environment self-check: ffmpeg/OCR/ASR/GPU/fonts/keys (or: vizsup doctor)
python -c "from app.providers.registry import list_providers; print(list_providers())"
ffmpeg -filters | findstr rubberband
```

## Conventions

- Python: type hints, `pathlib`, no global state in pipeline modules; each stage is a pure-ish function `stage(job: Job) -> Job` reading/writing the job dir.
- Subtitles in memory = a list of `Cue {index, start, end, text}` (see `pipeline/srt.py`). SRT/ASS are serialization formats only.
- Long work streams progress over WebSocket/SSE and checks a cancel flag between stages.
- Match surrounding code style; keep provider files small and uniform.

## Skills & agents (this repo)

- `/add-provider` — scaffold a new translation/TTS/ASR provider per the interface.
- `/run-stage` — run/debug one pipeline stage on a sample clip.
- `/ui-design-prompt` — (re)generate the prompt to hand to Claude for UI design (output: `docs/ui-design-prompt.md`).
- Subagents: `pipeline-debugger` (ffmpeg/whisper/torch/CUDA errors), `translation-reviewer` (CN→VI subtitle QA).
