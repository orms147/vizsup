# vizsup — Implementation Handbook (docs/plan/)

**Audience: the model/session that will WRITE THE CODE** (fresh session, no memory of prior work).
Everything you need is in this directory + `CLAUDE.md`. Each task spec is self-contained: premise,
approach, invariants that must not break, and headless verification.

> ⚠ **Premises in A/B were verified on 2026-07-02 and are now HISTORICAL** — those workstreams have
> shipped. Statements there of the form "`x.py` does not exist" describe the repo *before* the work
> landed. Only `C-workflow-ux.md` and `D-backlog.md` are forward-looking. Re-verify any premise
> against the code before acting on it.

## Files in this directory — read in this order

| File | What | Priority |
|---|---|---|
| `README.md` (this) | Status, environment, working rules | read first |
| `A-api-optimization.md` | ✅ **SHIPPED** — API-1…API-10 landed (`bc7eda1`…`069a25c`). Historical record of the latency/token work | reference only |
| `B-engine-quality.md` | ✅ **SHIPPED** — ENG-1…ENG-5 landed (`388b78b`…`9b54d93`). Historical record | reference only |
| `C-workflow-ux.md` | Mostly shipped (UX-2…UX-5; v2 UI 2026-10). UX-1 → folded into E-9; UX-7 still open. **UX-6 is PARKED — read its note before touching the API** | reference |
| `E-roadmap.md` | **Active workstream (2026-10-13)**: model-per-role on OpenRouter (benchmark table), ASR timing bug (E-1), OCR overlay filter, translation QA / xưng hô / songs, everyday UX | **do next** |
| `D-backlog.md` | Parked ideas — do not start without the user asking | never by default |

History: `docs/dev-plan.md` is the previous plan (v2) with DONE annotations for milestones M5–M8;
it remains the record of what was built and why. This directory is the forward-looking plan.

## Status snapshot (2026-10-13, HEAD `00f2d04`)

Since 2026-09-11: v2 UI (all four screens rebuilt from `appdesign/v2`), durable approval
revisions, review flags + "Cần xem lại", real waveform, cloud ASR default (`audio_llm`),
"tự canh lời" (local Silero VAD snap), `make run`. **525 tests green**, ruff clean.
Next: `E-roadmap.md`. The older snapshot below is kept for history.

### Previous snapshot (2026-09-11, HEAD `9b54d93`)

The app **works end-to-end** and has been verified with a real CLI job (hardsubbed clip → auto-detect
→ OCR → edit gate → edge-tts dub → MP4). Test suite: **235 headless tests green** (`pytest -q` from
repo root), ruff clean, GitHub Actions CI on every push.

Done: M5 (correctness/data-safety), M6 (long-run reliability: translate checkpoints, TTS manifest
resume, render lock/cancel, long-video ffmpeg), M7-light (heavy-model lock, fused ASR provider, OCR
suspect-only proofread, pre-gate auto-shorten, cover off/box/blur, dub loudness), M8-light (glossary
dialog, CLI batch, BBDown fallback), **workstream A** (API-1…API-10: transport reuse, refine
checkpointing, batch autoshorten, OCR-tolerant prompts, parallel draft+refine, parallel edge-tts),
**workstream B** (ENG-1…ENG-5: term extraction + glossary report, Demucs keep-music, whisperX forced
alignment, pure `group_frames`, persisted hardsub band).

**Known and deliberate gaps** (do not "fix" without reading the rationale):
- The FastAPI surface (`backend/app/main.py`, `jobs.py`) serves the provided-SRT/ASR path only — no
  OCR/auto route, no `cover_mode`/`style`/`size`/`burn_cues`. It has **no consumer** (no `frontend/`;
  desktop and CLI import `app.*` directly). Parity work is PARKED — see UX-6 in `C-workflow-ux.md`.
- `samples/` is gitignored on purpose (copyrighted source clips). Reference fixtures go in
  `samples/refs/*.srt` (text only); generate media with `backend/scripts/make_fixtures.py`.

## Dev environment (this exact machine — do not rediscover)

- **Python venv:** `backend/.venv` (Python 3.12.10). ALL backend deps AND PySide6 live there.
  CI runs **3.11** (the floor in `pyproject.toml`), so don't use 3.12-only syntax.
- **Run tests:** from repo root: `backend\.venv\Scripts\python -m pytest -q` (pytest.ini sets
  testpaths). Expect 235+ passing, ~1-2s. GUI tests use `QT_QPA_PLATFORM=offscreen` and
  skip cleanly when PySide6 is absent (CI).
- **Lint:** `backend\.venv\Scripts\python -m ruff check backend/app desktop` (config: root
  `ruff.toml`). Must stay clean.
- **ffmpeg/ffprobe are NOT on the shell PATH.** They resolve at runtime via `app.ffmpegutil`
  (WinGet Gyan full build, has rubberband). In scripts, get the path from
  `app.ffmpegutil.ffmpeg_bin()` — never call bare `ffmpeg` in a shell.
- **.env lives at the REPO ROOT** and is anchored there in code (`backend/app/config.py::_ROOT`)
  — launch dir no longer matters. It contains OPENROUTER_API_KEY (+model), WHISPER_MODEL,
  YTDLP_COOKIES_FROM_BROWSER. Storage: `<root>/storage` (gitignored).
- **Old job dirs in `backend/storage/`** contain 5–44-byte dummy "videos" — useless for media
  tests. Generate real clips instead (recipe below).
- **Console is cp1252** — Python scripts that print Vietnamese need
  `sys.stdout.reconfigure(encoding="utf-8")` (cli.py already does this).
- **anthropic and google-genai are NOT installed** in the venv — the Claude/Gemini providers are
  dormant (registry shows them unavailable). Don't add tests that import those SDKs.

### E2E smoke recipe (re-run after risky changes)

```powershell
# 1) make a hardsubbed test clip (drive colon must be escaped in filter args)
backend\.venv\Scripts\python - <<'PY'
import subprocess, sys; sys.path.insert(0, r"backend")
from app.ffmpegutil import ffmpeg_bin
dt = ("drawtext=fontfile='C\\:/Windows/Fonts/msyh.ttc':text='你好世界 欢迎观看这个视频':"
      "fontsize=28:fontcolor=white:borderw=2:x=(w-text_w)/2:y=h-text_h-14")
subprocess.run([ffmpeg_bin(),"-y","-f","lavfi","-i","testsrc2=size=640x360:rate=24:duration=10",
    "-vf",dt,"-c:v","libx264","-pix_fmt","yuv420p","smoke.mp4"], check=True)
PY
# 2) run to the gate (auto-detect must choose OCR), passthrough translator = no API keys needed
backend\.venv\Scripts\python -m app.cli run "local://smoke" --file smoke.mp4 --source auto --translator passthrough --workdir storage\smoke
# 3) simulate the gate: overwrite storage\smoke\vi.srt with 2 Vietnamese lines, then
backend\.venv\Scripts\python -m app.cli render --workdir storage\smoke
# expect: real edge-tts segments + output.mp4 (needs network for edge-tts)
```

## Working rules (inherited — do not relax)

1. **Headless-first verification.** Every backend change ships with pytest coverage before any
   manual GUI smoke. Construct-level tests (monkeypatched probes/clients) are the norm.
2. **Hard invariants** (breaking any of these is a rejected change):
   - The **human edit gate** stays after translate, before TTS/render.
   - **Providers stay pluggable**: one file + `registry.py`; capabilities are *declared* class
     attributes (`can_refine`, `can_proofread` pattern), never runtime-discovered.
   - The **count guard** holds: `translate_cues` output length == input cue count, or a loud error.
   - **Checkpoint semantics**: a hard failure/cancel always leaves completed work on disk; the
     draft checkpoint (`translate.checkpoint.json`) is a whole-batch PREFIX in v1 — if you change
     the format, migrate the loader in the same commit (see API-9).
   - **TTS manifest** (`tts/manifest.json`) stays crash-safe: an interrupted run must resume
     without re-buying finished segments.
   - **Cue identity is positional** (index = vi.srt order = sidecar position). No UUID keys.
   - **8 GB VRAM**: heavy models run inside `app.heavy.heavy_task()` and `unload()` after use.
   - `parse_numbered` fallback semantics unchanged: a missing line falls back per-line, never
     blanks or shifts.
3. **Model ids / third-party CLIs: verify via web at implementation time** — never from memory.
4. **Byte-locked goldens**: `tests/test_write_ass.py` locks ASS output; `test_srt_roundtrip.py`
   locks time formatting. Re-bless deliberately or not at all.
5. **Commit per task** with a message explaining premise → change → verification (see git log
   for the house style). Run `pytest -q` + `ruff check` before every commit.
6. **Vietnamese UI strings** — all user-facing log/UI text is Vietnamese; keep that.

## Suggested execution order

Workstreams A and B are done. What remains, in order:

1. **API honesty** (small, no refactor): propagate `cancel=` from `jobs.py` into `translate`/`tts`;
   drop the dead branch in the `main.py` WS loop. Explicitly *not* a unified-runner refactor.
2. **Test the uncovered edges**: `cli.py` (0 tests today), `download.py` (`normalize_url`,
   `platform_of`, BBDown fallback), the `main.py` subtitles routes. Reuse the monkeypatch +
   `inspect.signature().bind` pattern from `tests/test_api_contract.py`.
3. **CI: add a `windows-latest` matrix job** with the `desktop` extra, so the PySide6 tests actually
   run instead of skipping silently.
4. `C-workflow-ux.md` → **UX-4** (find/replace), then **UX-3** (undo gaps + redo), then **UX-5**
   (settings fields). These are what the user touches every job.
5. `D-backlog.md` only when the user explicitly asks.
