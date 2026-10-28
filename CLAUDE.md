# vizsup — Chinese→Vietnamese video subtitle + dubbing tool

Personal, local tool. Paste a **Douyin / Bilibili** URL → download → generate **Vietnamese subtitles** → **edit them (human gate)** → **Vietnamese voiceover (lồng tiếng)** → render a final MP4. Source videos may or may not have hardcoded (burned-in) Chinese subtitles.

Decision record and tool rationale: **[docs/research.md](docs/research.md)**. Pipeline data flow: **[docs/architecture.md](docs/architecture.md)**. Provider matrix: **[docs/providers.md](docs/providers.md)**.

## Pipeline (each stage is resumable; state on an absolute timeline)

`input → download → hardsub-detect → (OCR | ASR) → translate(2-pass) → ⛔HUMAN EDIT GATE → TTS → fit+mix → render`

- **download** — `yt-dlp` (cookies, domain-routed). Fallback: BBDown (Bilibili), TikTokDownloader (Douyin).
- **split (long videos)** — > 45 min and "Chia video dài" on (default): `pipeline/split.py` cuts ~30-min parts with ffmpeg's segment muxer at keyframes inside voice gaps (VAD, silencedetect fallback; stream copy, exact). Each part is a normal job (`metadata.group`); the parent folder keeps `group.json` and is not a project. Parts run through the queue (review part 1 while part 2 runs), share the first part's brief, merged glossary suggestions and the subtitle style; `split.join` concatenates the rendered parts (copy, or re-encode when params differ) + a shifted vi.srt. Renders are pinned to the source length (`-t`, dub `apad`) so joins don't drift.
- **hardsub-detect** — RapidOCR over ~16 sampled bottom-third crops; enough frames with recognized Chinese text ⇒ hardsubs → OCR path, else ASR ("Tự động" in the GUI, `--source auto` in the CLI; manual override always available).
- **OCR** (hardsubs present) — RapidOCR (ONNX PP-OCRv5+) frame sampling → CN SRT with per-cue confidence (`ocr_report.json`) → optional LLM proofread (suspect lines only). Frames stream through a pipe (no PNG dump), progress/ETA, resumable via `ocr_frames.json`; Engines are providers (`providers/ocr/`): `rapidocr` local (default; on Windows the GPU via `onnxruntime-directml` — no CUDA, output identical to CPU, ~2.7× faster, ONE worker since parallel DirectML sessions segfault; CPU ~0.25–0.5 s/frame; opt-in "OCR nhanh" = 1 frame/s, 2× faster, fine for films, misses sub-second vlog captions) or `vision_llm` — an OpenRouter vision model (role `ocr`, qwen3.7-flash) reads numbered strips of the text band, opt-in because it uploads them; far more accurate on stylised captions, ~$0.1 / 30 min per 3-hour video (docs/providers.md).
- **ASR** (no hardsubs) — default **cloud** `audio_llm`: Qwen-Omni (OpenRouter) writes the transcript, Whisper word timestamps (cloud, or local faster-whisper if installed) time it. Local engines (`faster_whisper`, `funasr_aligned`) are optional extras and NOT installed on this machine (removed 2026-10-01). Benchmark: `docs/providers.md`.
- **snap ("tự canh lời")** — local Silero VAD (`pipeline/vad.py`, ONNX, model downloaded once to `storage/_models`) → each fresh CN cue edge moves to the nearest voice start/stop of its OWN voice within ~1 s (`pipeline/snap.py`). Runs before translate (opt-out: GUI option / `--no-snap`); editor: A / Shift+A, one undo step. Singing under loud music fools VAD; `vad.voice_spans` fills those gaps from the saved Whisper word track (`asr_words.json`; old jobs: "Đo lại thời gian" fetches it).
- **translate** — two-pass, provider-pluggable: CN-native draft → frontier refine. Strip timecodes locally; batch ~50 lines with context + glossary + video meta.
- **HUMAN EDIT GATE** — PySide6 timeline+table editor (desktop/). **Nothing downstream runs until the user approves.**
- **TTS** — provider-pluggable; default edge-tts vi-VN (needs edge-tts ≥ 7.3.1 — Microsoft refused 7.2.8 with HTTP 403 on 2026-10-16; Settings → "Cập nhật edge-tts").
- **commentary (F-4)** — the user's own notes (`pipeline/commentary.py`, `commentary.json`, editor tab "Bình luận"): text in a voice other than the dub's or their own recording; "pause" freezes the picture (ONE `tpad` per piece — a second chained tpad drops its padding) and shifts everything after it, "over" speaks over the video. Counts towards the approved revision; `output.srt` = subtitles as rendered (output timeline).
- **fit+mix** — place each VI segment at its absolute start, atempo to fit, pad silence; duck/replace original audio.
- **compile ("Ghép tập", `pipeline/compilation.py`)** — series episodes are translated, reviewed and rendered one by one (each postable alone); Thư viện → "Ghép video" joins rendered projects, split videos or earlier compilations into one MP4 (folder with `compilation.json`, no job.json; never an editable project). Order by episode number in the title (第12集 / EP12 / Tập 12, editable), one chapter per episode (embedded + `chapters.txt` for YouTube), merged `vi.srt`. A compilation is a source again ("Tập 1–50" + 51…100) with its chapters kept. Stream copy when every file's parameters match INCLUDING codec headers (x264's PPS changes with CRF); else only the odd files are re-encoded to the majority's format with its encoder/preset (all of them if headers still differ), then concat-copy.
- **render** — dub track built in ≤5-min / ≤100-line windows (`dub/win_###.flac`, cached, sample-exact; the final encode has 2–3 inputs, `-progress` %, real cancel; NVENC when a test encode works — `VIZSUP_VIDEO_ENCODER=auto|nvenc|cpu`). FFmpeg burns styled `.ass` (bundled fonts via `fontsdir`); "Xem nhanh" 640p proof first; frame: source or TikTok 9:16; "Chất lượng" = resolution (short side, scaled BEFORE the burn, never upscaled) × preset Nhẹ/Cân bằng/Nét (CRF 27/23/20, NVENC CQ 30/25/21); cover "glass" = style "Kính mờ trắng" (`pipeline/glass.py`: per-line rounded mask .ass → blur + white tint over the Chinese band, dark text); exports: named MP4 copy, `.srt`/`.ass`, soft-sub `.mkv` (`pipeline/export.py`). Posting (`pipeline/disclosure.py`): optional burned corner notes for the first 5 s (source credit, "Lồng tiếng AI") as `Tag` events in the same `.ass`, a per-project rights note (`rights.json`, warns on unknown, never blocks), and a post-description + checklist dialog.

## Hard constraints (learned from research — respect these)

- **Keep the human-edit gate AFTER translation and BEFORE TTS/render.** Never spend TTS credits or encode time on un-approved subtitles.
- **Providers are pluggable and UI-selectable.** Every translation/TTS/ASR/OCR engine implements the interface in `backend/app/providers/base.py` and registers in `registry.py`. Adding one = one file + register. Never hardcode a single provider in pipeline code.
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
  providers/          base.py, registry.py, translation/, tts/, asr/, ocr/
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
python -m app.cli join --group ../storage/<id>       # join a split long video's rendered parts
python -m app.cli compile <id> <id> ... [--title T]  # Ghép tập: join rendered episodes into one video
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
