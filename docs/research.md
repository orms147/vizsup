# Research — Chinese→Vietnamese Video Subtitle + Dubbing Tool

> Source: 33-agent deep-research workflow (run `wf_25ab2a00-4ef`, 2026-06-19), adversarially fact-checked.
> This is the curated decision record. "CONFIRMED / REFUTED / UNCERTAIN" tags reflect independent fact-checks.

## Headline decision

**Build from scratch** (Python core + FastAPI + React web editor). **Do not fork.**

The pipeline plumbing (download → ASR/OCR → translate → TTS → mux) is thin wrappers over libraries. The two things that actually differentiate this project —
1. a **video-synced subtitle timeline editor that gates the run before any TTS/render spend**, and
2. an **OCR-first path for videos that already carry hardcoded Chinese subtitles** —
are exactly what no existing end-to-end project does well *together*, and are easier to build clean than to retrofit.

> Nuance: the dedicated "existing projects" agent actually recommended **forking pyVideoTrans** (most mature, ~18k stars, explicit Vietnamese, and — CONFIRMED — a genuine *edit-translated-subtitles-before-dub* pause node). The lead synthesizer overruled it toward build-from-scratch because (a) the decisive timeline editor only works well in web tech while pyVideoTrans is desktop PySide6/Qt, (b) the hardsub-OCR path is missing from *every* project anyway, (c) the user wants **UI-selectable providers per stage** (a clean abstraction is easier to build than to bolt on), and (d) GPL-3 copyleft (pyVideoTrans/KrillinAI) vs Apache-2.0 references. **Keep pyVideoTrans, VideoLingo, SoniTranslate as read-only references.**

## Confirmed user-specific scope (from clarifying Q&A)

- Hardware: **RTX 4070 laptop, 8GB VRAM** → local GPU models viable, run sequentially.
- Providers: user wants to **choose the API in the UI** → pluggable provider system is core.
- Voice: **stock Vietnamese voice is enough** → edge-tts default; no cloning in v1.
- Platforms: **Douyin + Bilibili only** (Kuaishou + Xiaohongshu dropped → removes the worst download problems).

## Per-stage findings

### 1. Download
- **yt-dlp** is the primary engine — covers Douyin + Bilibili (and XHS) in one CLI with clean automation (`--dump-json`, `-f`, `--cookies`) and ffmpeg muxing.
- **Douyin is cookie-fragile**: CONFIRMED it breaks even with *fresh valid* cookies (extractor-side; yt-dlp issues #9667/#13382/#16803/#16831). Wire a fallback + cookie-refresh step.
- **Bilibili**: yt-dlp is solid; **BBDown** (MIT) is best-in-class for hi-res/premium + native subtitle/danmaku export.
- **Douyin fallback**: JoeanAmier/**TikTokDownloader** (watermark-free).
- Out of scope now: Kuaishou (CONFIRMED **no** working OSS downloader — no yt-dlp extractor, issue #14010; Evil0ctal API has no Kuaishou crawler in source), Xiaohongshu (extractor unpatched, migrated to rednote.com, issue #16519).

### 2. ASR (no-hardsub path)
- **Timing ≠ accuracy.** Use **WhisperX** for VAD + wav2vec2 forced alignment (best Whisper-family word timestamps) as the *timing* layer.
- Use a **Chinese-native model for the transcript**: **FunASR Paraformer-Large** (CONFIRMED ~1.7% CER, CPU-viable ~1–2GB — ideal for a single-user tool). Whisper Mandarin CER is **3–5× worse** (CONFIRMED).
- GPU upgrade: **FireRedASR2-AED** (CONFIRMED ~0.57% CER, native word timestamps, Apache-2.0, ~4.7GB, needs GPU).
- WhisperX Chinese sentence segmentation is weak (no Chinese in NLTK) — line breaks get fixed in the human edit step anyway.

### 3. Hardcoded-subtitle detection + OCR
- No OSS tool has a turnkey "does this video have hardsubs?" flag → build a cheap **PaddleOCR PP-OCRv5 detection-only** pass over a **bottom-third crop** across sampled frames; a stable recurring text box ⇒ hardsubs present. Cropping is the single biggest accuracy+speed lever (also removes Douyin watermarks/logos).
- Extraction: **VideOCR** (PP-OCRv5, SSIM frame dedup, crop-region, direct SRT, MIT, actively maintained). Fallback: YaoFANGUK/video-subtitle-extractor (also ships v5), RapidOCR (CPU).
- Keep VideOCR's **Google-Lens hybrid mode opt-in only** (CONFIRMED it uploads frames to Google via an unofficial endpoint — privacy/ToS-gray).
- PP-OCRv5 accuracy is Baidu self-reported → validate on real stylized Douyin samples; add an **LLM proofread pass** for stylized/colored-outline subs.

### 4. Translation CN→VI
- **Two-pass design**: CN-native model drafts (best comprehension of slang/idioms) → frontier model refines Vietnamese register/fluency + enforces ~CN-matched line length.
- **GLM-5.2 is REAL and the latest GLM** (CONFIRMED — the "GLM-4.6 is newest / no GLM-5" aggregator claim was REFUTED). ~744B MoE, MIT open weights, ~$1.4/$4.4 per M tokens, 1M context — strong, self-hostable, China-native option.
- Other providers: **DeepSeek-V4** (CONFIRMED ids `deepseek-v4-flash` $0.14/$0.28, `deepseek-v4-pro` $0.435/$0.87; legacy `deepseek-chat`/`deepseek-reasoner` DEPRECATE **2026-07-24** — use v4 ids), **Claude Opus/Sonnet 4.x**, **Gemini 2.x/3.x**, **Qwen3**, DeepL/Google (gist only).
- Prompt rules: strip timecodes locally (never send to the model), batch ~50 lines with surrounding context + glossary of recurring names/terms + video metadata, keep VI line length ≈ CN.
- No public CN→VI subtitle benchmark exists (CONFIRMED) → run a small head-to-head on real clips before locking the default.

### 5. Subtitle editing (the human gate)
- Must sit **after translation, before any TTS/render** so no credits/encode time are spent on un-approved subtitles.
- Only **web tech** does a true video-synced timeline well: Gradio `gr.Video`/Streamlit `st.video` only **display** `.srt`/`.vtt` (CONFIRMED, no off-the-shelf timeline component).
- Build: HTML5 `<video>` + **wavesurfer.js** (waveform + timeline plugin) + **react-timeline-editor** (draggable cues) + a per-line text/timing **table**.
- External fallback: export SRT → edit in Aegisub/Subtitle Edit → re-import (SRT-as-input, like SoniTranslate).

### 6. Vietnamese TTS / dubbing
- **edge-tts** vi-VN voices **HoaiMy** (female) / **NamMinh** (male) are CONFIRMED free neural voices — the v1 default (user confirmed stock voice is enough). Caveat: occasional HTTP 403s.
- Pluggable alternatives: FPT.AI, Azure vi-VN, Google Cloud TTS.
- **Voice cloning deferred** (not needed for v1). If wanted later: ElevenLabs (VI in Flash/Turbo v2.5 + v3 alpha, *not* Multilingual v2; IVC vs PVC quality UNCERTAIN for VI) or local **viXTTS / F5-TTS-Vietnamese** (CONFIRMED non-commercial licenses; need GPU; viXTTS weak on <10-word inputs).

### 7. Audio + video assembly / sync
- Build the VI track on an **absolute timeline**: place each segment at its start time, insert exact silence (numpy) between segments to avoid cumulative drift.
- Fit each segment to its slot with **`atempo`** capped **~1.3×** (CONFIRMED: atempo accepts 0.5–100× single-instance since FFmpeg 4.1+, but **skips samples above 2×** → cap low, chain instances, or use `rubberband`). The 1.3× intelligibility cap is a soft heuristic — validate with the chosen voice; lean on shorter VI translations upstream.
- CONFIRMED: gyan.dev + BtbN Windows FFmpeg builds **include librubberband** by default (still verify with `ffmpeg -filters | findstr rubberband`).
- Mix: replace audio, or duck original with `sidechaincompress`+`amix`, or **Demucs** stem-separation to keep BGM and drop the CN voice.
- Render: burn styled **`.ass`** via the `ass=` filter, **Vietnamese-diacritic font** (Be Vietnam Pro / Noto Sans), `BorderStyle=4` opaque box to cover leftover CN hardsubs, `libx264 crf 18–22` + aac. Also export the editable `.srt`/`.ass` next to the MP4.

### 8. GUI / architecture
- **Recommended: Python + FastAPI backend + React/Vite/Tailwind** (run locally, open in browser).
  - Python ML integrates natively (the backend *is* Python — no sidecar packaging).
  - WebSocket/SSE → clean cancellable long-job progress (beats Streamlit's rerun model and Gradio's mount/queue footguns).
  - Web tech is the only stack where the video-synced subtitle editor is both feasible and easy for Claude to design.
- **Rejected**: Gradio/Streamlit (display-only subtitles), Tauri/Electron + PyInstaller sidecar (bundling torch/CUDA/WhisperX is materially harder — 4–5GB artifacts, cuDNN8-vs-9 DLL conflicts — for zero benefit on a single-user local tool).

## Key risks (carry into the build)
- Douyin extractor breaks even with fresh cookies → fallback + cookie-refresh.
- Stylized Chinese hardsubs are the hardest OCR case → tight crop + LLM proofread + human gate.
- Dub timing: VI longer than CN → length-controlled translation + fit-to-slot + silence padding; keep atempo ≤~1.3×.
- 8GB VRAM → run heavy models (WhisperX, FunASR, Demucs) sequentially.
- China-based providers (DeepSeek/GLM/Qwen) + ElevenLabs/Lens send media to third parties → prefer local stack for sensitive content.

## Reference projects (read-only)
- pyVideoTrans — https://github.com/jianchang512/pyvideotrans (GPL-3; edit-before-render, Vietnamese, edge-tts)
- VideoLingo — https://github.com/Huanshere/VideoLingo (Apache-2.0; WhisperX subtitle segmentation)
- SoniTranslate — https://github.com/R3gm/SoniTranslate (Apache-2.0; SRT-as-input dubbing)
- KrillinAI — https://github.com/krillinai/KrillinAI (GPL-3; Chinese-platform download config)
- VideOCR — https://github.com/timminator/VideOCR · video-subtitle-extractor — https://github.com/YaoFANGUK/video-subtitle-extractor
- Editor building blocks — react-timeline-editor (https://github.com/xzdarcy/react-timeline-editor), wavesurfer.js
