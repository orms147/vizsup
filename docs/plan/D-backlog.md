# Workstream D — Backlog (parked)

Do NOT start these without the user explicitly asking. Listed so nothing is lost.

- **Hardsub inpainting** (STTN/LaMa on the subtitle band) — the blur cover (N-16) is the pragmatic
  middle step; inpainting is a heavy-model rabbit hole. Prereq: ENG-5's persisted band rect.
- **Vision-LLM OCR proofread** (suspect lines + their frame crops) — opt-in upload only
  (CLAUDE.md rule). Prereq: ENG-4's `rep_frame` in ocr_report.json. Revisit only if text-only
  proofread + API-7's OCR-tolerant prompts still leave real errors at the gate.
- **Karaoke word-highlight captions** — needs word-level timing everywhere (ENG-3 first).
- **Real waveform + follow-playback timeline** — current waveform is synthetic; QAudioDecoder or
  ffmpeg-extracted peaks.
- **React web frontend** (`frontend/` does not exist despite old docs) — only if remote use is
  actually wanted; the FastAPI backend (UX-6) is the prerequisite. PySide6 desktop remains primary.
- **Packaging** — PyInstaller one-folder + ffmpeg download step + font bundling.
- **VieNeu-TTS provider** (Apache-2.0, more expressive than edge-tts, CPU) — one file + registry
  per the provider interface; verify the project's current state via web first.
- **FireRedASR-AED-L** — Mandarin accuracy ceiling; needs VAD chunking; 8 GB fit must be verified.
- **Voice cloning, Kuaishou/Xiaohongshu download** — explicitly out of scope (CLAUDE.md).
