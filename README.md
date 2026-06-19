# vizsup

Personal tool to translate & dub Chinese short videos (Douyin / Bilibili) into Vietnamese — **subtitles + voiceover (lồng tiếng)** — with a **subtitle-editing step before the final render**.

```
paste URL → download → detect hardsubs → OCR or ASR → translate (CN→VI, 2-pass)
          → ✍️ edit subtitles (you review/fix) → Vietnamese voiceover → render MP4
```

## Status

The **PySide6 desktop app** (primary UI) is built: 3 screens (Input · Subtitle Editor · Render)
matching the "Dark Editor Design", wired to the backend via a `QThread` worker — download →
subtitles → translate → **edit gate** → TTS → render. The two-pass translator and the
cheap path (yt-dlp → edge-tts → FFmpeg) work; **ASR/OCR are stubs** (supply a Chinese `.srt`
for now). A FastAPI server + React scaffold also exist for optional web/remote use.
See [docs/architecture.md](docs/architecture.md).

## Quick start

Prereqs: **Python 3.11+**, **ffmpeg** (with libx264; rubberband recommended), and **yt-dlp** on PATH.

```bash
# install backend + desktop deps into a venv (Windows)
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[desktop]"
cd ..

# run the desktop app (from the repo root)
python -m desktop.main
```

Headless / scripting alternatives:

```bash
cd backend
# CLI smoke path: download → translate → (edit vi.srt) → render
python -m app.cli run "https://www.bilibili.com/video/BVxxxxxxxx" --srt cn.srt
python -m app.cli render --workdir ../storage/<id>
# list available providers (proves the UI-selectable abstraction)
python -c "from app.providers.registry import list_providers; print(list_providers())"

# optional web/remote UI
uvicorn app.main:app --reload      # API
cd ../frontend && npm install && npm run dev
```

## Configuration

Copy `.env.example` → `.env` and fill in only the API keys for providers you want to use.
Everything runs on the **free local default** (edge-tts + yt-dlp + ffmpeg) with no keys.

## Docs

- [docs/research.md](docs/research.md) — tool research & decision record
- [docs/architecture.md](docs/architecture.md) — pipeline data flow
- [docs/providers.md](docs/providers.md) — provider matrix (models, keys, cost)
- [docs/ui-design-prompt.md](docs/ui-design-prompt.md) — prompt to hand to Claude for the UI design

For personal use. Respect each source platform's terms and each model/voice license (some local voices are non-commercial).
