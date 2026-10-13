# Workstream E — Models, translation quality, everyday UX (2026-10)  ← **ACTIVE WORKSTREAM**

Written 2026-10-13 against HEAD `00f2d04` (525 tests green). Supersedes the remaining items of
`C-workflow-ux.md`: **UX-2 shipped** (v2 inspector: ±50/200 ms nudges, `[ ]`, timing lock) and
**UX-1 (GUI batch queue) is folded into E-9** below. UX-7 (Douyin download fallback) stays in C.

**User's direction (2026-10-13):** "cái nào nên gọi API thì gọi model OpenRouter — tôi lo chi phí;
ưu tiên model Trung Quốc vừa rẻ vừa giỏi tiếng Trung; dùng thật tiện; tiện ích nên có; tối ưu bản
dịch." Cost is not the constraint; quality, speed and convenience are. Still prefer the cheapest
model that wins a benchmark — a 5-minute video should stay well under $0.05 of AI calls.

## Principles (apply to every task)

1. **One key, model per role.** Every AI call goes through OpenRouter. Each *role* (ASR, draft,
   refine, brief, QA, vision, …) gets its own model setting with a benchmarked default — never one
   global model for everything (today `OPENROUTER_MODEL` serves draft, refine, brief, shorten,
   terms). Providers stay pluggable (CLAUDE.md); roles are a layer above them.
2. **Measure before switching defaults.** Every model choice below that says *benchmark* must be
   backed by `backend/scripts/translation_bench.py`-style evidence on the real jobs in `storage/`.
   Verify ids/prices against `https://openrouter.ai/api/v1/models` (public, no key) — never memory.
3. **The gate stays the centre.** AI never bypasses approval; new AI checks *feed the "Cần xem lại"
   queue* (review flags in `app/pipeline/review.py`) with a reason and a one-click fix.
4. **Uploads are labelled.** Audio already goes to the cloud for ASR (opt-in via the engine choice).
   Anything that uploads **frames** is a separate opt-in toggle (same rule as Google-Lens OCR).
5. **No silent degradation.** If an AI step fails, the run continues with the previous behaviour and
   says so in the log (pattern: `snap.snap_srt`, `brief.build_brief`).

## Model roles (OpenRouter, verified 2026-10-13)

Prices are $/M tokens in/out from the live `/api/v1/models` list. The promo model
`stealth/space-bunny-alpha` is **gone** (expired 2026-10-05); the app already fell back to
`OPENROUTER_MODEL`. Keep the promo mechanism — new free models appear regularly.

**Translation benchmark 2026-10-13** (app's own draft prompt + brief + glossary, 40 lines each of
job `2dc712c162d7` — a sung cover — and `bb15cbcac86c` — an OCR'd school vlog; judged by reading,
not yet by E-3's blind judge, so treat as preliminary):

| Model | $/M in/out | 40 lines | Verdict |
|---|---|---|---|
| `deepseek/deepseek-v4-pro-0813` | 1.32 / 3.96 (½ off-peak) | 17–22 s | right meaning (兑现 → "chưa kịp thực hiện", 清唱 → "hát mộc"); cleans OCR noise. (Its "cô ấy" for the singer was WRONG: three audio models hear a male singer and the line itself says 他 — corrected 2026-10-13, E-4) |
| `qwen/qwen3.7-plus` | 0.32 / 1.28 | 9–13 s | **≈ best**, cheaper, faster; missed the singer's gender ("cậu ấy") |
| `moonshotai/kimi-k3` | 0.73 / 13.5 | 13–18 s | good; slight drift ("hát live"); output price high |
| `qwen/qwen3.8-flash` | 0.15 / 0.47 | 8–11 s | fluent, a few meaning slips ("chưa kịp ngỏ") |
| `deepseek/deepseek-v4.1-flash` (**current default**) | 0.044 / 0.89 | 4–7 s | fast; meaning errors ("chưa kịp nói", "hát ừ một tiếng trên đầu họ"); leaves English banner text untranslated |
| `z-ai/glm-5.3-flash` | 0.026 / 0.90 | 4–17 s | **avoid**: reversed meaning (line 4), typos ("vườn chà"), passes OCR garbage through |
| `tencent/hy-mt2-30b-a3b` (dedicated MT) | 0.074 / 0.30 | 7–8 s | **avoid**: ~40% longer lines (11 vs 7–8 syllables) → bad for dubbing |
| `xiaomi/mimo-v2.6-flash` | 0.14 / 0.28 | 16–100 s | **avoid**: leaves Chinese characters in the output, slow |

Even the best model costs ≈ $0.01 per 40 lines (draft + refine), so quality decides.

**Blind judge (E-3, `z-ai/glm-5.3-prime`, 40 lines each)** — scores /10:

| Model | Talking vlog (ASR, fea1d38fb4d7) | Sung cover (2dc712c162d7) | Avg |
|---|---|---|---|
| `moonshotai/kimi-k3` | **8.68** | 8.15 | **8.42** |
| `qwen/qwen3.8-flash` | 8.40 | 8.20 | 8.30 |
| `deepseek/deepseek-v4.1-flash` | 8.47 | 8.07 | 8.27 |
| `qwen/qwen3.7-plus` | 8.05 | **8.35** | 8.20 |
| `deepseek/deepseek-v4-pro-0813` | 8.38 | 7.90 | 8.14 (53 s / 40 lines) |

Judge notes: Kimi "chuẩn nghĩa, tự nhiên, xưng hô nhất quán"; Qwen3.7 Plus rendered a name in
Hán-Việt ("Cát Tường Nữ"); V4 Pro one wrong pronoun. The spread is small — the reading-based
table above and the judge disagree on V4 Pro — so defaults put the strongest model where it
fixes the FINAL text and keep the first pass fast:

| Role | Default (shipped in `app/providers/roles.py`) |
|---|---|
| ASR transcript (audio in) | `qwen/qwen3.8-omni-flash` (unchanged, `AUDIO_LLM_MODEL`) |
| Draft · OCR proofread · terms | `deepseek/deepseek-v4.1-flash` (4–6 s / 40 lines) |
| Refine · brief · shorten / Dịch lại | `moonshotai/kimi-k3` |
| QA reviewer (E-5) | `qwen/qwen3.8-flash` — QA bench on 39 real pairs: found 3 real issues; V4 Pro (reasoning off) 0, Kimi / GLM-prime 1 |
| Vision (opt-in frames) | `qwen/qwen3.7-flash` — not benchmarked yet |


---

## P0 — correctness

### ✅ E-0 [S] OCR keeps static overlays as subtitles — DONE 2026-10-13

Shipped `ocr.drop_overlays` (after `group_frames`): a text re-read as similar flickers ≥6 times
with median ≤1 s → `overlay`; 豆包AI生成 / 抖音号 / @handle → `watermark`; one-frame Latin
fragments → `blip`. Dropped lines go to `ocr_dropped.json`. On bb15cbcac86c: 78 → 44 lines
(25 banner, 2 watermark, 7 blip); two other jobs untouched. Original spec below.


**Symptom** (job `bb15cbcac86c`): the first 40 "subtitles" are the channel's decorative banner
("School life Welcome to my channel", misread as "life lcomme Lo annel", "tife elcome…") cut into
0.5 s cues. Every model wastes effort on it and weak ones pass the garbage through.
**Approach:** in `ocr.py` grouping, drop text that (a) stays in the same box for most of the video
or repeats with near-identical content across many short cues, or (b) is Latin-only when the
source language is zh (keep an opt-out). Report dropped lines in `ocr_report.json`.
**Verify:** that job's cn.srt loses the banner lines; real dialogue lines unchanged.

### ✅ E-1 [M] Stacked / overlong ASR cues — DONE 2026-10-13

Shipped: in-order DP character alignment (`align._match_pairs`, replaces difflib — a repeated
chorus was paired out of order), collapsed Whisper timestamps dropped (`_drop_collapsed`),
unanchored sentences laid over VAD voice in their gap (`_place_on_voice`, natural length
without VAD, capped at 8 s / 0.6 s per char), overlong anchored cues cut to their densest
anchor run, `Cue.guess` → `timing_report.json` → review issue `guess` ("Timing đoán"),
filler-only lines with no voice → `novoice`, word track saved in `asr_words.json`,
`asr.retime(job)` + `vizsup retime --workdir` + editor ··· "Đo lại thời gian từ âm thanh"
(per-chunk acceptance: a chunk Whisper barely heard keeps its old times; `repair_runs`
re-lays stacked/overlong leftovers). Real job: 0 stacked starts, 0 overlaps, max 10.5 s
(was 76 s), 11 lines flagged for review. Original spec below.


**Symptom** (job `2dc712c162d7`, cn.srt): cues 33–39 all at `240.00–240.30`; cue 40 spans
`245.30–321.40` (76 s); cue 3 is 21 s. The editor can't fix this by snapping (no voice "inside").

**Root cause:** `audio_llm` uploads 240 s chunks (`CHUNK_SEC`). `align.align_text_to_words`
interpolates sentences between Whisper word anchors; sentences **after the last anchor** get
`last_end + chars × 0.22 s` and are then `min(hi, …)`-clamped to the chunk end → every trailing
sentence collapses onto `hi` (240.00) + MIN_CUE. It happens when Whisper hears no words over loud
music / singing at the end of a chunk. Repeated chorus lines also let difflib anchor one sentence's
first and last characters to different repetitions → a 76 s cue.

**Approach:**
- Persist the word track per job (`asr_words.json`, absolute times) so re-timing never re-uploads.
- In `align_text_to_words`: unanchored head/tail sentences are **distributed over the voice**
  (`vad.speech_spans`, already cached per job) inside the free window, weighted by character
  count; with no VAD, spread uniformly over the window instead of clamping. Cap a cue at
  `max(8 s, 0.6 s × chars)`; longer → split its span at internal anchors or re-spread.
- New review issue `"guess"` ("Timing đoán") for cues timed by interpolation/spreading, so they
  show in "Cần xem lại".
- Hallucination flag: a line with no voice under it and filler-only text (`呼呜…`, `啊啊`) → issue
  `"novoice"`.

**Invariants:** monotonic, no overlaps; anchored cues unchanged; never raises.
**Verify:** a fixture built from this job's sentences + a word track that stops at 200 s inside a
240 s chunk → no two cues share a start, none longer than the cap; real job re-timed from cached
words: cues 33–39 spread over 236–244 s voice.

### ✅ E-2 [S] Role-based model settings — DONE 2026-10-13 (`cb10cf8`)

Settings → "AI và mô hình" gets one row per role (combo of OpenRouter ids + "mặc định"), stored as
`MODEL_<ROLE>` in `.env`, read through one helper `app.providers.roles.model_for(role)`. Roles:
`asr_audio`, `draft`, `refine`, `brief`, `qa`, `shorten`, `terms`, `vision`. Defaults from the
table above. `OPENROUTER_MODEL` stays as the fallback for every text role (back-compat).
**Verify:** settings round-trip test per role; each pipeline call site asks `model_for`.

---

## P1 — translation quality

### ✅ E-3 [S] Commit the translation benchmark + a judge — DONE 2026-10-13

Shipped `backend/scripts/translation_bench.py` (blind letters, judge JSON, `storage/_bench/<stamp>/report.html`); results in the table above. Original spec:


`backend/scripts/translation_bench.py` (the 2026-10-13 run, see table): same draft prompt as the
app (brief + glossary + numbered lines) across N models on N jobs → JSON + an HTML side-by-side.
Add an LLM judge (strong model, rubric: meaning, naturalness, xưng hô consistency, names,
length-fit vs `SYL_PER_SEC` budget) that scores blind, plus the user's own pick. Re-run whenever a
new Chinese model ships; update the role defaults from it.

### ✅ E-4 [M] Xưng hô from who is actually speaking — DONE 2026-10-13

Shipped `app/pipeline/speakers.py`: the ASR audio model answers a speaker question on the
most voice-rich 60 s (VAD), optional frames (opt-in "AI xem khung hình", vision role) describe
setting only; cached in `observed.json`, fed to the brief as OBSERVED (audio wins on who
speaks — on the test job the frames suggested a woman singing, three audio models heard a
man, and the transcript says 他). `AudioLLM.ask()` for free-form audio questions. Original spec:


Vietnamese pronouns (anh/em/chị/cô/bạn/tớ/mình…) depend on speaker gender, age and relationship —
the single biggest naturalness lever, and the brief today only sees text.
- Ask the ASR audio model (`qwen3.8-omni-flash`, already listening) for a short **speaker sheet**
  per job: speakers, likely gender/age, who addresses whom, singing vs speaking. One extra cheap
  call on a ≤60 s voice-rich excerpt (pick it with VAD), not per chunk.
- Opt-in "AI xem khung hình" (uploads ~6 frames): a vision model (`qwen/qwen3.7-flash`, image
  input at ~$0.03/M) adds visual context (who is on screen, setting).
- `brief.build_brief` merges both into a structured "Nhân vật & xưng hô" section; the user's note
  still wins. Translator + refiner already read the brief.
**Verify:** brief contains the section; pronoun consistency judged in E-3 improves on the vlog job.

### ✅ E-5 [M] "AI soát" — a QA pass that feeds the review queue — DONE 2026-10-13

Shipped `app/pipeline/qa.py` → `qa_report.json` (time + the VI text it judged, so an edit clears it), review issue `ai`, inspector note + "Dùng gợi ý", GUI option "AI soát bản dịch" / CLI `--no-qa`. Original spec:


After translate (before the gate), a cheap model reads CN/VI pairs in batches and returns JSON
(`structured_outputs`) `[{i, kind: meaning|pronoun|name|omission|unnatural|too_long, note, fix}]`.
Each becomes review issue `"ai"` with the note; the inspector shows the note + **"Dùng gợi ý"**
(one undo step). Default on; ~1 call per 50 lines.
**Invariants:** never edits vi.srt itself; failure → no flags + log line.

### ✅ E-6 [M] "Dịch lại" in the editor (Alt+R) — DONE 2026-10-13

Shipped `translate.alternatives` (shorten role, ±2 lines + brief + optional request) and `RetranslateDialog`; inspector button "Dịch lại", Alt+R; the pick is one undo step. Real call: 3 options in 3.7 s.


Selected cue → 3 alternatives from the refine model with ±3 neighbouring lines as context:
*sát nghĩa / tự nhiên / ngắn gọn (vừa thời lượng)*. Pick one → replaces text (undo). Free-text
"yêu cầu thêm" box ("trang trọng hơn", "giữ chơi chữ").

### ✅ E-7 [M] Learn from your edits — DONE 2026-10-13

Shipped `app/pipeline/memory.py`: approval → `remember` (storage/tm.json, normalised CN → approved VI; `apply_tm` reuses exact matches after translation) and `record_edits` (vi.mt.srt vs approved → edits.jsonl); every 5 approvals `update_style` (brief role) rewrites storage/style_rules.txt, which rides in the translator prompt (`context["_style"]`) and is shown / editable in editor → Ngữ cảnh. Tests now run on a private temp storage (conftest).


On approval, diff machine VI vs approved VI per cue:
- **Translation memory** (`storage/tm.jsonl`): approved CN→VI pairs; exact / near-exact CN matches
  in later jobs reuse the approved line (no API) — covers repeated lyrics and catchphrases.
- **"Phong cách của tôi"**: every ~5 approvals, a cheap model condenses recurring corrections into
  ≤10 short rules (e.g. "người hát xưng em", "không dịch 'bảo bối' thành 'bảo bối'"); shown and
  editable in Settings; appended to the translator system prompt.

### ✅ E-8 [S] Songs & singing — DONE 2026-10-13

Shipped: the E-4 audio question ends with `HÁT: có / xen kẽ / không` (`speakers.singing`);
sung → `context["_lyrics"]` adds `LYRICS_HINT` to the translator system prompt (singable,
rhythm kept, chorus worded identically). Render page: 5th audio mode "Giữ giọng gốc" (no TTS,
original audio, subtitles only), preselected once per job when the video is mostly sung.
Per-line mixing (dub talk, keep singing) stays manual via per-line mute. Original spec:


Today's test job is a sung cover: lyrics get prose translations and the dub talks over singing.
- When the speaker sheet (E-4) / brief says *bài hát* or lines are tagged sung: lyric-style
  translation prompt (keep line rhythm/imagery, no explanations).
- Dub default for sung lines: **keep the original singing** (cue `mute`, subtitles stay) with a
  bulk toggle "Giữ giọng hát gốc" on the render page.

---

## P1 — everyday UX

### ✅ E-9 [M] Many links at once (supersedes UX-1) — DONE 2026-10-13

Shipped: 2+ links pasted in the link box (or several video files dropped) → `queueRequested`; `MainWindow._start_queue/_run_next_queued` runs them one by one with their own job (`_queue_job`, never `self.job`), notifies per video, and finished projects can be opened and edited meanwhile. Hủy also drops the rest of the queue; render waits until the queue is done.


Paste several links (one per line) or drop several files → a queue that runs each to the gate
**sequentially** (heavy work stays serial — 8 GB rule). The recent-projects list shows queue state;
open any finished one while the rest continue.

### ✅ E-10 [S] Clipboard pickup — DONE 2026-10-13

Shipped: `InputView.offer_clipboard` on app activation (Start page, idle) — chip "Dùng link vừa copy?" for Douyin/Bilibili links not already a project / not dismissed; one click fills + starts.


On window focus, a Douyin/Bilibili link in the clipboard that isn't a known job → chip under the
hero "Dùng link vừa copy?" (one click fills + starts). Never auto-starts.

### ✅ E-11 [S] Resume unfinished jobs — DONE 2026-10-13

Shipped: `store.list_jobs` also lists jobs with source/cn.srt + job.json but no vi.srt (`unfinished`, badge "Đang dở"); opening one re-runs `run_to_gate` with the saved options (every finished step is skipped).


`store.list_jobs` only lists jobs with `vi.srt`/`output.mp4`. Also list jobs with a source video
but no vi.srt as "Đang dở" with "Tiếp tục" → `run_to_gate` (already skips an existing cn.srt; make
download skip an existing `source.mp4` too).

### ✅ E-12 [S] Notifications — DONE 2026-10-13

Shipped: `QSystemTrayIcon` messages for gate reached (cue + review counts), render done, run/render failed — only when the window is not active; clicking focuses the app.


`QSystemTrayIcon` message when the gate is reached, a render finishes, or a run fails — only when
the window isn't active. Clicking it focuses the right page.

### ✅ E-13 [S] Tool upkeep in Settings — DONE 2026-10-13

Shipped (written by a Haiku 5.5 subagent, reviewed + applied): `app/tools.py` (`update_ytdlp`, `cookie_age_days`); Settings → Tải video: yt-dlp card with version + "Cập nhật yt-dlp", cookie-age line (amber > 20 days); new group "Kiểm tra" running `doctor.run_checks()` off-thread.


- "Cập nhật yt-dlp" (pip upgrade inside the venv, off-thread) — Douyin breaks extractors often.
- Cookie age: warn when `cookies.txt` is older than ~20 days, before a download fails.
- "Kiểm tra môi trường" page reusing `app.doctor` (ffmpeg, VAD model, keys, fonts).

### ✅ E-14 [S] Cost & time per job — DONE 2026-10-13

Shipped `app/usage.py`: the worker wraps each run in `usage.track(job_dir, phase=…)`; OpenRouter `_chat` and the audio model record `usage.cost` (always returned now — the `usage.include` param is deprecated) into `usage.json`; shown on recent projects and the render result ("AI $0.012 · 2 phút 05").


Request OpenRouter usage accounting (`usage: {"include": true}` → `usage.cost`) in `_chat`;
accumulate per job/role in `usage.json`; show "AI $0.004 · 1 phút 12" on recent projects and the
render page. Makes E-3 model choices concrete.

### ✅ E-15 [M] Hear the whole dub before encoding — DONE 2026-10-13

Shipped as a fast proof instead of audio-only: "Xem nhanh" on the render page (after approval) runs
the same TTS + mix + burn graph scaled to 640p, `-preset ultrafast -crf 30` → `preview.mp4`
(5.5-min clip: ~20 s vs minutes for the full encode); `output.mp4` is untouched and the real render
reuses the TTS cache. Side find: libass never saw the bundled fonts and burned every render in
Arial — the burner now gets `fontsdir=fonts` (bundled TTFs copied into the job dir), also for the
style-preview frame, so what you see is what's exported.


Render page (after approval): "Nghe thử bản lồng tiếng" = TTS (cached, needed anyway) + the
existing mix, audio-only, played over the source video. The full encode then reuses the TTS cache.
Catches pacing problems without waiting for an MP4.

### ✅ E-16 [S] Export conveniences — DONE 2026-10-13

Shipped: render page "Khung hình" (gốc / Dọc 9:16 TikTok 1080×1920 — fit before burning, pad
after; remembered; CLI `--frame tiktok`); result row "Lưu video…" (copy named from the project
title, hashtags stripped, last folder remembered) + "Xuất ▾" (.srt / .ass / **.mkv phụ đề rời**:
`app/pipeline/export.py` remuxes source picture + rendered audio + subs.ass + attached fonts,
0.2 s, no re-encode); "Mở thư mục" selects the file in Explorer. Quick previews write their own
`preview.ass` so subs.ass keeps matching output.mp4. Two old burn bugs found while checking real
frames: `WrapStyle: 2` (no wrapping → long lines ran off both edges of vertical videos) → 0, and
the ASS `[Events]` Format line lacked `Name` → every burned line started with a stray comma.


Output named from the project title; "Xuất cho TikTok" preset (1080×1920, pad); optional
soft-subtitle MKV/MP4 (no burn) for re-editing elsewhere.

### ✅ Follow-ups to the caveats — DONE 2026-10-13

- **Singing under loud music** (VAD ~0.3–0.5 on whole choruses): `vad.voice_spans` adds the
  saved Whisper word track (`asr_words.json`) where VAD heard < 30 % of a word span; VAD edges
  still win where it heard the voice. 329 s livestream: 3 more sung lines snapped (15 → 12
  "no voice"; the rest had broken pre-E-1 timings). 40 % of Whisper words there were
  zero-length → dropped. Shift+A hints "Đo lại thời gian" when ≥ 3 lines have no voice and no
  word track exists.
- **Speaker gender**: checked on known-gender clips (edge-tts zh male / female / husband–wife
  dialogue / female under loud noise) → the listener was right 4/4. Added a local YIN pitch
  check (`pipeline/pitch.py`, numpy, 0.2 s) that only speaks up for talking clips — when it
  contradicts the listener (→ "CHƯA CHẮC", neutral pronouns) or fills in an unnamed gender.
  Brief rules: 他/她 are homophones in ASR text (not evidence of male); song pronouns follow
  the lyrics' persona; uncertain gender → mình / bạn.
- **"AI xem khung hình"** (qwen3.7-flash) checked on 3 real videos against the frames
  themselves: settings, people, a blurred second face and an inserted alien image all right,
  5–8 s each.
- **Notifications in front**: in-app toast. **Soft-sub .mkv**: re-applies the render's blur.

---

## Long videos (several hours) — 2026-10-14

Measured on this machine before the work: a 3-hour video (~4,000 lines) could not be rendered
at all (one ffmpeg input per line → 80,182-char command line > Windows' 32,767 past ~1,600 lines;
mixing time ∝ lines × length, ~2 h extrapolated); OCR 0.7 s/frame at 2 fps → ~4 h and ~5.4 GB of
PNGs; a failed ASR chunk lost (and re-bought) every finished one; VAD/waveform decoded the whole
track into RAM (~1 GB). Translation (batches + checkpoint), editor (4,000 lines: reload 0.14 s,
edit 0.16 s) and edge-tts (3-way batch) were already fine.

- ✅ **Render**: windowed dub (`assemble._windows/_render_dub`), cut in the silence before a line,
  each window concatenates its lines padded to the exact sample of the next start → 4,000 lines
  over 3 h: 49 s (36 windows), cached re-run 0.1 s, one edited line 4.8 s, command line 352
  chars; real job: every checked line at lag 0 samples. Final encode: `-progress` %, Hủy kills
  ffmpeg, NVENC auto (RTX 4070 here; 5.5-min 720×1566 job: 23 s vs 31 s libx264), CPU fallback.
  Segment durations cached + probed in parallel (29 s first, 2.5 s after). Side fix: in "duck"
  mode the original audio stopped at the last dub line (sidechain key now padded).
- ✅ **ASR per-chunk cache** (`_cloud.chunk_cache`, `asr_cache/`): a failed chunk no longer
  re-buys the finished ones (Haiku 5.5 subagent, reviewed).
- ✅ **Streamed audio decode** (`ffmpegutil.pcm_blocks`): VAD reads ~10 s blocks instead of the whole track (3 h ≈ 1 GB as float32), the waveform is vectorized too; on a real job both are identical to the old results (waveform 0.07 s).
- ✅ **OCR plumbing**: frames stream from ffmpeg as BMP through a pipe (no PNG dump — ~5.4 GB
  for 3 h), progress with ETA, cancel between frames, checkpoint `ocr_frames.json` every 50
  frames → resume. Real 137 s video: 97/97 cues identical to the PNG path.
- ✗ **OCR speed on CPU — not solved, measured**: 0.25–0.5 s/frame; 92 % of it is text
  DETECTION because `limit_type=min, 736` upscales the 240-px strip ~3x. Tried: 2–6 workers
  (≤15 %, onnxruntime already uses the cores; kept 2), downscaling (no gain), skipping
  unchanged frames (vlog backgrounds move: same-subtitle frames differ 24 % median, even
  inside the text boxes it missed 11 real changes at a 5 % threshold), coarser detection
  resize (~3x faster but drops real characters, e.g. 我们再把校服换上 → …换). The real fix is
  the GPU: onnxruntime-gpu 1.27 is installed but needs CUDA 13 + cuDNN 9, which the pip
  extras can't provide on Windows (`nvidia-cuda-nvrtc-cu13` has no real wheel) → a system
  install; `_preload_cuda()` then picks it up. Until then a 3-hour hardsub video ≈ 1.5–4 h of
  OCR (or use the ASR path).
- Optional: split a long video into ~30-min parts through the queue, join at the end.

## P2 — later (keep in mind, don't start yet)

- Multi-voice dubbing per speaker (needs E-4 speaker labels per line; edge-tts has 2 vi voices).
- Vision OCR proofread for suspect OCR lines with `qwen3.7-flash` (cheap; D-backlog item).
- VAD-aware ASR chunking: cut uploads at voice gaps and skip music-only stretches (fewer
  hallucinated lines, less audio uploaded).
- Better Vietnamese TTS voices (FPT.AI provider exists; VieNeu in D-backlog).

## Recommended order

E-1 → E-0 → E-3 (+ E-2 with its defaults) → E-5 → E-4 → E-8 → small UX in between (E-10, E-12,
E-11, E-13, E-14) → E-6 → E-9 → E-7 → E-15 → E-16.
