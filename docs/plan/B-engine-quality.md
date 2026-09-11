# Workstream B — Engine Quality  ✅ SHIPPED (historical)

> **STATUS: all five tasks landed.** This file is now a record of *why* each engine feature was built,
> not a to-do list. The premises below were written on 2026-07-02 and describe the repo **before** the
> work — in particular every "does not exist" sentence is now false. Do not re-derive these tasks.
>
> | Task | Commit | What landed |
> |---|---|---|
> | ENG-1 | `388b78b` | `pipeline/terms.py` (116 lines) + glossary compliance report |
> | ENG-2 | `f32052a` | `pipeline/separate.py` (63 lines) — Demucs "keep music" dub mode |
> | ENG-3 | `f14ff32` | fused ASR prefers whisperX forced alignment, faster-whisper fallback |
> | ENG-4 | `b9601f9` | pure `group_frames()` + `rep_frame` in `ocr_report.json` + `ocr_eval.py` |
> | ENG-5 | `55c899b` | persisted hardsub band; blur covers it, not the whole bottom-third |
>
> Follow-up fixes to the above: `9b54d93` (three bugs from the workstream-B adversarial review).

Output-quality tasks that survived plan v2. Status as verified against code 2026-07-02 (grep-confirmed
absent unless noted) — **historical, see the banner above**. Every quality claim needs a committed measurement (use `backend/app/qa.py`:
`cer`, `srt_text_cer`, `timing_stats`), not vibes. Respect the 8 GB VRAM rule: heavy models run
inside `app.heavy.heavy_task()` and `unload()` after use.

---

## ✅ ENG-1 [M] Auto term-extraction pre-pass + glossary compliance report — SHIPPED `388b78b`

~~`backend/app/pipeline/terms.py` does not exist.~~ **It exists now** (landed `388b78b`). Original plan:
1. `extract_terms(cues, provider, context) -> dict[str, str]` — ONE chunked LLM call (reuse the
   selected draft translator; numbered-list reply, tolerant parser modeled on `parse_numbered`)
   over the full CN transcript, extracting recurring proper nouns/brands/personas with suggested
   VI renderings. Write `<job>/glossary.suggested.json`. Merge precedence: suggested < global
   (`<storage>/glossary.json`) < job (`<job>/glossary.json`) — job wins; wire into the effective
   glossary passed to `translate_cues`.
2. Opt-in checkbox "Tự trích xuất thuật ngữ" on Screen 1 (`desktop/views/input_view.py`),
   persisted like `ocr_proofread` (add to `store._UI_KEYS`).
3. Pure `check_glossary(cn_cues, vi_cues, glossary) -> list[{index, term, expected}]`: for each
   glossary source term present in a CN line, verify the VI target appears (case-insensitive,
   diacritics exact) in the paired VI line. Runs after translate; writes
   `<job>/glossary_report.json`; worker logs "⚠ N dòng lệch thuật ngữ — kiểm tra ở bước sửa phụ đề".
4. The N-23 glossary dialog (`desktop/glossary_dialog.py`) can then grow a third read-only scope
   showing `glossary.suggested.json` for curation (optional polish).

Risk: LLM-suggested renderings can be wrong — they stay suggestions + gate-time warnings, never
silently authoritative. Verify: pytest — compliance true/positive/negative table; parser robustness
(malformed reply → empty dict, never crash). Quality: violation count on a multi-name sample → ~0
with the merged glossary.

---

## ✅ ENG-2 [L] Demucs "keep the music" dub mode — SHIPPED `f32052a`

~~`backend/app/pipeline/separate.py` does not exist; `assemble` has replace/mix/duck only.~~
**Both exist now** — `separate.py` landed `f32052a`, `assemble` has `audio_mode="keep_music"`. Note:
`backend/pyproject.toml` `[audio]` extra ALREADY declares demucs — but it is NOT installed in the
venv; installing it is part of this task (watch the torch pin vs faster-whisper).
1. `separate.py::separate_background(job, *, model="htdemucs", log) -> Path` — run demucs
   two-stems **via subprocess** (`python -m demucs --two-stems=vocals -n htdemucs -o <job>/stems`)
   so torch state stays out-of-process; run inside `heavy_task()`; produce cached `<job>/bg.wav`
   (skip when bg.wav is newer than source.mp4 — re-renders free).
2. `assemble()` gains `audio_mode="keep_music"`: bg.wav as an extra ffmpeg input routed through the
   existing mix path as background (`orig_volume` applies to the instrumental), dub on top.
3. Wiring: render-view audio-mode radio gains "Thay lời + giữ nhạc (Demucs)"; `worker.run_render`
   calls `separate_background` before TTS when picked (post-gate, sequential); CLI
   `--audio-mode keep-music`; `doctor.py` checks demucs importability.
All post-gate — the human edit gate untouched.

Verify (headless): `scripts/duck_eval.py` on a speech+music sample — speech-band (200–4000 Hz
bandpass + volumedetect) mean volume of bg.wav drops ≥10 dB vs source while a music-only section
stays within 3 dB; bg.wav duration == source ±0.1 s; construct test asserts the keep_music filter
graph. One manual listen.

---

## ✅ ENG-3 [L] Finish the fused ASR — SHIPPED `f14ff32`

Done: `funasr_aligned` provider + pure `align.fuse_transcript_timing` (tested) with faster-whisper
word timestamps as phase 2. Missing (grep-confirmed): whisperx path, `[asr-align]` extra,
`scripts/asr_timing_eval.py`, hand-labeled reference clip.
1. Add `[asr-align]` extra (whisperx) to pyproject; in `funasr_aligned._word_timing`, prefer
   `whisperx.align` with a Chinese wav2vec2 alignment model when whisperx is importable, else the
   existing faster-whisper fallback (guaranteed baseline — provider must never regress below
   FunASR-only). **Web-verify the whisperx zh alignment model id at implementation time.**
2. `backend/scripts/asr_timing_eval.py`: compare `faster_whisper` / `whisperx_funasr` /
   `funasr_aligned` via `qa.timing_stats` against `samples/refs/<clip>.cn.ref.srt` (~20 cues,
   one-time hand labeling — ask the user to label or supply the clip).
Acceptance: fused median |Δstart| ≤ FunASR-only AND ≤ 200 ms on the reference clip.
Note: `funasr` itself is not installed in the venv — the provider shows unavailable until the user
installs `.[asr-accurate]`; code + tests must not require it (fusion tests are synthetic).

---

## ✅ ENG-4 [M] Finish OCR refactor — SHIPPED `b9601f9`

Done: per-cue confidence + suspect classifier + `ocr_report.json` + suspect-only proofread.
Missing: the grouping state machine is still inline in `extract_hardsubs`
(`backend/app/pipeline/ocr.py` ~:227-250), no `rep_frame` in the report, no `scripts/ocr_eval.py`.
1. Extract pure `group_frames(per_frame, *, sim, fps, min_dur) -> list[(cue, conf)]` — MUST be
   byte-for-byte behavior-preserving (subtle start-time handling on group break): write tests
   against the extracted function first, then diff a real sample's cn.srt before/after.
2. Add `rep_frame: "frames/00042.png"` to each `ocr_report.json` entry (frames already persist).
3. `scripts/ocr_eval.py` using `qa.srt_text_cer` vs a hand-fixed reference SRT; acceptance:
   suspect-only proofread CER within 0.5 pt of full-proofread CER while LLM-sent lines drop ≥50 %.

---

## ✅ ENG-5 [M] Persist the detected hardsub band; blur uses it — SHIPPED `55c899b`

Done: cover_mode off/box/blur + GUI combo + CLI `--cover`; blur band is HARDCODED bottom-third.
Missing: `hardsub_region.json` (grep-confirmed absent).
1. `hardsub_detect.py`: accumulate recognized-text boxes across sampled frames (RapidOCR returns
   boxes; currently discarded), compute the stable union band, map crop-local → full-frame
   normalized `{x, y, w, h}` via a PURE helper, persist `<job>/hardsub_region.json`.
2. `assemble._build_render` blur branch: use the persisted rect when present, else the current
   bottom-third fallback.
Verify: pytest for the coord-mapping/band-union helper; construct test that a region file changes
the crop/overlay params in `filter_complex.txt`; `cover_eval` idea from plan v2 (OCR the output
band → CJK count ~0 for box, ≤10 % of baseline for blur) is optional.

---

## Note on N-6 (per-line prompt budgets in the DRAFT prompt)

The remaining half of N-6 is now **mostly superseded by API-8** (budgets folded into the *refine*
prompt, where the money is). Only add budgets to the *draft* prompt if measurements after API-8
still show many overlong drafts on single-pass (no-refine) runs — then reuse the exact `_budgets`
mechanism from API-8 in `build_user_prompt`.
