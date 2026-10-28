# Architecture

Personal desktop tool: Douyin / Bilibili link → Vietnamese subtitles → **human edit gate** →
Vietnamese dub → MP4. The PySide6 app ([desktop/](../desktop/)) and the CLI
([cli.py](../backend/app/cli.py)) call the same `app.*` modules; the stage order below is the
one in [desktop/worker.py](../desktop/worker.py) (`PipelineWorker._gate_body`, `run_render`) and
`cmd_run` / `cmd_render` in the CLI. Hard constraints live in [CLAUDE.md](../CLAUDE.md).

## Data flow

```
INPUT (URL or local file)
  ▼
DOWNLOAD            download.py        yt-dlp (cookies, domain-routed) → source.mp4, metadata.json
  ▼ (> 45 min: split.py cuts ~30-min parts; each part is a project)
SUBTITLES (Chinese)
  ├ hardsub_detect.py → OCR path   ocr.py + providers/ocr/ (RapidOCR | vision_llm) → cn.srt, ocr_report.json
  ├ no hardsubs → ASR path         asr.py + providers/asr/audio_llm (Qwen-Omni text + Whisper words)
  │    separate.speech_stem()      Demucs vocals.wav first when a GPU is there (words are timed on it)
  │    cn.srt, asr_words.json (word track, so "Đo lại thời gian" never re-uploads), timing_report.json
  └ a given .srt
  ▼
clauses.py          one cue per spoken clause (ASR path only)
snap.py + vad.py    cue edges → nearest voice start/stop of their own voice (Silero VAD → vad.json)
timing_pass.py      2-frame gaps, chain gaps < 0.5 s, ≥ 20 frames; scene cuts opt-in (scenes.json)
series.py           link the episode to its series (series_link.json); copy style.json / render_opts.json
brief.py (+ speakers.py)   context brief + who speaks → context_brief.json, observed.json
terms.py            opt-in glossary suggestions → glossary.suggested.json
  ▼
TRANSLATE           translate.py       2-pass draft → refine; glossary layers, series sheet, last lines of
  │                                    the previous episode, TM (memory.py)  → vi.srt (+ vi.mt.srt copy)
vi_lint.py          safe typography fixes on vi.srt and vi.mt.srt
qa.py               AI review (never edits) → qa_report.json
voices.py           pitch → male/female tag per line → overrides.json ("voice")
  ▼
⛔ HUMAN EDIT GATE  desktop/ editor — text, timing, flags (review.py), CSV round-trip (subcsv.py),
  │                 timing / lint actions; Ctrl+Enter → approval.approve() → approved.json.
  │                 NOTHING below runs on an unapproved revision.
  ▼
batch.save_opts()   render_opts.json (the first render's settings also become the series' look)
separate_background()   bg.wav when "Giữ nhạc nền" (Demucs, before TTS)
TTS                 tts.py             speak.spoken() → what the voice says; per-line voice if
  │                                    "Giọng theo nhân vật"; tts/manifest.json (resume, cache key)
commentary.py       the user's own notes (pause / over), own TTS voice
  ▼
MIX + RENDER        assemble.py        dub in ≤ 5-min windows (dub/win_###.flac), atempo ≤ 1.3×,
  │   original audio replace / duck / keep-music; dub loudnorm −16 LUFS, final mix −14 LUFS
  │   video: scale (burn_size snaps near-16:9 sources, exact downscale before the burn) or 9:16
  │   fit with blurred / black bars → cover (box · blur · glass.py) → subs.ass burned (srt.write_ass:
  │   style, per-cue styles, watermark, credit + "Lồng tiếng AI" notes, episode chip + "Còn tiếp"
  │   card as Tag events) → x264 / NVENC → output.mp4, output.srt, render.json, fit_report.json
  ▼
batch.py ("Dựng hàng loạt") · compilation.py ("Ghép tập") · split.join · export.py · disclosure.py
```

Every stage is resumable: it skips what is already on disk, and all state lives in the job
directory `storage/<id>/`. Times are absolute seconds (no cumulative dub drift). Optional aids
(snap, timing pass, series, lint, voice guess) log and carry on if they fail.

## Sidecar files

**Approval** = part of the approved revision ([approval.py](../backend/app/pipeline/approval.py)):
changing it after approving revokes the approval. The editor-only keys `lock` and `reviewed` of
`overrides.json` are excluded.

| File | Written by | Read by | Approval |
|---|---|---|---|
| `source.mp4`, `source.wav`, `metadata.json` | download | everything | |
| `cn.srt` | ocr / asr / given .srt, then clauses, snap, timing_pass | translate, editor | |
| `vi.srt` | translate, vi_lint, editor | TTS, render, editor | **yes** |
| `vi.mt.srt` | translate (machine copy), vi_lint | memory.record_edits (learn from the user's edits) | |
| `overrides.json` — per cue `{gain_db, mute, voice, lock, reviewed}` | editor, voices.guess_job | tts, assemble, editor | **yes** (gain, mute, voice) |
| `style.json`, `style_overrides.json` | editor, series.inherit | write_ass, render | **yes** |
| `commentary.json` | editor | commentary, assemble | **yes** |
| `approved.json` | approval.approve | approval.is_approved, batch | — |
| `asr_words.json`, `timing_report.json` | asr (word track; guessed cues) | asr.retime, vad.voice_spans, review | |
| `vad.json`, `vocals.wav`, `bg.wav`, `stems/` | vad, separate | snap, voices, assemble | |
| `ocr_report.json`, `ocr_frames.json`, `hardsub_region.json` | ocr, hardsub_detect | review, assemble (cover band) | |
| `scenes.json` | timing_pass (opt-in) | timing_pass | |
| `context_brief.json`, `observed.json` | brief, speakers | translate, voices (singing) | |
| `glossary.json` (+ `<storage>/glossary.json`), `glossary.suggested.json`, `glossary_report.json` | user, terms, terms check | translate, review | |
| `pronounce.json` (+ `<storage>/pronounce.json`) | user, series dialog | speak.load_rules | |
| `qa_report.json` | qa | review (flag "AI nghi sai") | |
| `series_link.json`, `series_learned.json` | series | series, vi_lint, translate | |
| `tts/seg_NNNN.mp3`, `tts/manifest.json` | tts | tts (hash of provider · voice · rate · spoken text), assemble | |
| `music.json`, `music/` | editor | assemble | |
| `voice.json`, `render_opts.json`, `render.json`, `fit_report.json`, `usage.json`, `rights.json` | render page, render, usage, disclosure | batch, review ("Lồng tiếng trễ"), post dialog | |
| `subs.ass`, `output.mp4`, `output.srt` | assemble | export, compilation | |

Outside a job: `<storage>/_series/<slug>/series.json` (series profiles),
`<storage>/_cache/model_catalog.json` ([providers.md](providers.md#model-catalog)),
`<storage>/tm.json` (translation memory), `<storage>/_models/` (Silero VAD).

## Guessed timing: voice islands

Cloud ASR returns the text; [align.py](../backend/app/pipeline/align.py)
(`align_text_to_words`) anchors it on the Whisper word track by an in-order character alignment
(`_match_pairs`: only runs of ≥ 2 matching hanzi count). A sentence with no anchor — Whisper
mishears sung / chanted text, and a 1–3 character interjection can never form a run — is laid
into the gap between its anchored neighbours:

1. the VAD voice of the gap is merged into **islands** (gaps < 1 s; slivers < 0.25 s dropped);
2. `assign_to_islands` gives every line to ONE island, in order (dynamic programming: island
   duration vs characters at ~0.25 s/char; voice left over costs a little); lines of an island
   share it by character count — a line never bridges a silence;
3. a line **alone** on an island at a plausible pace (0.10–0.60 s/char, or a 1–2 character
   breath) has measured edges → `guess=False`. Everything else stays `guess=True`
   (`timing_report.json` → editor flag "Timing đoán"): lines sharing an island, runs with > 0.5 s
   of unused voice, or Whisper-heard time (`heard_spans`) > 1 s outside every island.

No island, or a huge run, falls back to the older proportional placement. Rejected after
measuring: fusing word spans into the voice, and homophone (pinyin) anchoring — see
[plan/research/pinyin-anchor-eval.md](plan/research/pinyin-anchor-eval.md).

## Code map

| Concern | Module |
|---|---|
| Data types (`Cue`, `Job`) | [models.py](../backend/app/models.py) |
| Settings / keys / `.env` | [config.py](../backend/app/config.py) |
| Subtitle (de)serialize (SRT / ASS / CSV) | [srt.py](../backend/app/pipeline/srt.py), [subcsv.py](../backend/app/pipeline/subcsv.py) |
| Stages | [pipeline/](../backend/app/pipeline/) — one module per stage (named above) |
| Review flags in the editor | [review.py](../backend/app/pipeline/review.py) (+ [vi_lint.py](../backend/app/pipeline/vi_lint.py) flags) |
| Series profile | [series.py](../backend/app/pipeline/series.py), dialog [series_dialog.py](../desktop/series_dialog.py) |
| Spoken text + pronunciation | [speak.py](../backend/app/pipeline/speak.py) |
| Per-line voice | [voices.py](../backend/app/pipeline/voices.py), [pitch.py](../backend/app/pipeline/pitch.py) |
| Post copy (title + hashtags, credits) | [postcopy.py](../backend/app/pipeline/postcopy.py), [disclosure.py](../backend/app/pipeline/disclosure.py) |
| Providers + model resolution | [providers/](../backend/app/providers/) — `base`, `registry`, `roles`, `presets`; [catalog.py](../backend/app/catalog.py) |
| Desktop (primary UI) | [desktop/](../desktop/) — calls `app.*` directly; `ui_thread.py` for background calls |
| CLI (headless) | [cli.py](../backend/app/cli.py) — `run`, `render`, `retime`, `join`, `compile` |
| FastAPI app | [main.py](../backend/app/main.py) — provided-SRT / ASR path only, no consumer (parked) |

## Threads

The pipeline runs on a `QThread` (`PipelineWorker`, cancel flag between stages, joined on close).
Short blocking calls (catalog check, AI title, connection tests, editor AI actions, library
thumbnails) run on **daemon threads** via [ui_thread.py](../desktop/ui_thread.py): results return
to the GUI thread through `connect_on_ui`, are dropped once the window is closing, and never delay
the exit. Heavy local models (Demucs, Whisper) run one at a time inside `app.heavy.heavy_task()`.

> ffmpeg / ffprobe resolve at runtime via `app.ffmpegutil` (not required on PATH). Per-stage
> research notes: [research.md](research.md); the plan: [plan/](plan/README.md).
