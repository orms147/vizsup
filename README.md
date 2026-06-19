# vizsup

Personal tool to translate & dub Chinese short videos (Douyin / Bilibili) into Vietnamese — **subtitles + voiceover (lồng tiếng)** — with a **subtitle-editing step before the final render**.

```
paste URL → download → detect hardsubs → OCR or ASR → translate (CN→VI, 2-pass)
          → ✍️ edit subtitles (you review/fix) → Vietnamese voiceover → render MP4
```

## Status

Phase 1 scaffold. The **headless CLI smoke path** runs end-to-end on the cheap stack
(yt-dlp download → edge-tts Vietnamese voice → FFmpeg burn). ASR/OCR, the two-pass
translator, the FastAPI server and the React timeline editor are scaffolded with TODOs.
See the roadmap in [the plan](../../Users/Admin/.claude/plans/) and [docs/architecture.md](docs/architecture.md).

## Quick start

Prereqs: **Python 3.11+**, **Node 18+**, **ffmpeg** (with libx264; rubberband recommended), and **yt-dlp** on PATH.

```bash
# backend
cd backend
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -e .

# headless smoke path (download → placeholder VI subs → edge-tts → burn)
python -m app.cli "https://www.bilibili.com/video/BVxxxxxxxx"

# list available providers (proves the UI-selectable abstraction)
python -c "from app.providers.registry import list_providers; print(list_providers())"
```

```bash
# frontend (UI shell)
cd frontend
npm install && npm run dev
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
