# vizsup — Ordered Development Plan

Single sequential workflow for one maintainer. Each task lists WHAT, the files/methods it
touches, effort (S/M/L), dependencies, the key risk, and how to VERIFY it. GUI drag can't be
unit-tested, so verification favours **headless construct/render checks** (build the data model
in Python, call `write_ass`/`assemble.style_preview_frame`, assert on the bytes/PNG) plus a
short manual smoke where a human gesture is unavoidable.

---

## Where we are

Phase A (subtitle styling moved into Screen 2 as a "Kiểu chữ" tab) and the Sprint-1 dub work
(overlap-free fit, rubberband, replace/mix/duck, per-line gain/mute, in-editor "nghe thử",
fit badge, LLM rút gọn) are **done and integrated end-to-end**: `style.json` + `overrides.json`
persist per job and flow `StylePanel.get_style()` → editor → worker → `assemble` →
`srt.write_ass(style=…)`. What remains on Screen 2 is **direct manipulation on the live video**
(Phase B — drag/resize the subtitle box, blocked today because `QVideoWidget`'s native surface
hides overlay children) and **per-line styles** (Phase C — the "Áp dụng cho: các câu đang chọn"
scope selector is a pure UI stub; the table is `SingleSelection`; `write_ass` emits a single
`Style: Default`). After Screen 2 is finished, the high-value roadmap is OCR/translate quality
upgrades and ASR/hardsub depth. Both Phase B and Phase C specs are verified against the real code
and are internally consistent (cue identity is **positional**: 0-based editor index = `Cue.index-1`
= `vi.srt` block order = `overrides` list position).

**Note on a stale audit claim:** the audit's flagged P0 bug — "rút gọn hardcoded to `gpt-4-turbo`
/ `ClaudeAPI`" — does **not** exist in the current tree. `_shorten` calls
`translate.shorten_line(text, provider=provider)` where `provider = self._ctx.get("translator")`,
and there is no `gpt-4-turbo`/`ClaudeAPI` reference anywhere under `desktop/`. No action needed;
it is folded into M0 only as a verification step.

---

## Milestone 0 — P0: stabilize the Phase A refactor (do first)

These are cheap correctness/regression closures from the Phase A move. They must land before any
Phase B/C work touches the same files (`write_ass`, `_snapshot`, the style flow).

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **P0-1** | **Backward-compat snapshot test for `write_ass`.** Lock current ASS output before Phase C rewrites it. Build 3 cues, call `write_ass(style={...})` and `write_ass()` (defaults); save the two outputs as golden files. | S | — | Future Phase C refactor silently changes bytes for the no-override path. | `pytest`/script: assert `write_ass(cues, p)` == golden, and that exactly one `Style: Default` + N `Dialogue` lines are emitted. This golden becomes the C-1 regression gate. |
| **P0-2** | **Undo loses no per-cue fields.** `_snapshot` (editor_view.py:386) clones `EditorCue(c.start,c.end,c.vi,c.zh,c.gain_db,c.mute)` — it already drops nothing today, but `_split`/`_merge`/add construct cues without carrying gain/mute. Audit + fix the merge/split policy now so Phase C's `.style` can ride the same path. | S | — | Split/merge silently reset gain/mute (and later style). | Headless: build cues with gain_db set, call the split/merge list ops directly, assert child cues keep the documented policy (merge=keep first, split=both inherit, add=default). |
| **P0-3** | **Verify style persists across job save/resume.** Manual: edit style → editor save (600 ms debounce) → close + reopen job → `StylePanel` shows identical controls; confirm `style.json` round-trips `{font,size,style}`. | S | — | Debounced save races a fast close. | Headless round-trip: write `style.json`, call `cuemodel.load_pair` + `editor._load_style`, assert dict equality. Plus one manual reopen. |
| **P0-4** | **Confirm `_shorten` provider routing (stale-audit closure).** No code change expected; just assert the live path. | S | — | Regression to a hardcoded model. | `rg gpt-4-turbo desktop/` returns nothing; read `_shorten` → confirms `provider=self._ctx.get("translator")`. Document "audit claim was stale" in the commit. |

**Definition of done (M0):** golden ASS snapshot committed and green; split/merge/add carry per-cue
fields per a written policy; style round-trips on resume (headless + one manual); `_shorten`
routing confirmed. No behavioural change visible to the user — this is a safety net for M2/M3.

---

## Milestone 1 — Phase B: draggable/resizable subtitle box on the live video

Replace `VideoPanel`'s `QVideoWidget` output with `QVideoSink` → `paintEvent`, so an interactive
box can be drawn **on** the frame. Live drag updates `StylePanel` margins/pos cheaply; mouse-release
re-burns an exact FFmpeg frame for WYSIWYG. Backend (`srt.py`, `assemble.py`) needs **no change** —
the drag math targets the existing `margin_l/r/v` + `pos=(nx,ny)` over the fixed 1920×1080 canvas.

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **B-1** | **`StylePanel` drag hooks.** Add `set_drag_margins(l,r,v)` (write 3 sliders under `blockSignals`, then one `_changed()`) and `clear_drag_position()` (`self._pos=None`). `set_drag_position` already exists (style_panel.py:212). | S | M0 | A slider write re-emits and forms a feedback loop. | Headless: instantiate `StylePanel`, call `set_drag_margins(80,80,120)`, assert `get_style()` returns those margins and `styleChanged` fired exactly once (spy). |
| **B-2** | **`VideoCanvas(QWidget)` + `SubBox` dataclass** (`desktop/widgets/video_canvas.py`, new). Owns frame paint (letterbox fit), approximate `drawText` subtitle, draggable/resizable box, hit-test (body/left/right/vert), coord mapping (widget↔normalized↔ASS), signals `marginsChanged`/`positionChanged`/`dragCommitted`, methods `set_frame/set_caption/set_style/set_burned_overlay`. | L | B-1 | `toImage()` slow/NV12/stride padding on the Windows FFmpeg backend → janky paint. | Headless coord-math test: instantiate `VideoCanvas`, set `_frame_native`+resize, feed synthetic box positions, assert `_emit_from_box` produces `margin_l=round(nl*1920)`, `margin_r=round((1-nr)*1920)`, `margin_v=round((1-ny)*1080)` and pos=centre. Manual: drag edges, watch sliders move. |
| **B-3** | **Swap `VideoPanel` output to sink+canvas** (`desktop/widgets/video_panel.py`). Drop `QVideoWidget`; add `QVideoSink`, `videoFrameChanged → _on_frame` (downscale ≤960px long-edge, pass NATIVE WxH for letterbox), `set_style`, `current_time`, `set_burned_overlay`; forward caption to canvas in `_refresh_caption`; keep the under-video caption bar as fallback. Re-expose `positionChanged`/`durationChanged`/`seek_seconds` unchanged. | M | B-2 | Paused player stops emitting frames; seek doesn't refresh the still. | Manual smoke: play/pause/scrub/seek/volume still work; paused frame stays editable; seek delivers one frame. Headless: `HAVE_MM`-false placeholder path still constructs. |
| **B-4** | **Wire editor ↔ canvas** (`desktop/views/editor_view.py`). In `__init__`: `video.marginsChanged→style_panel.set_drag_margins`, `video.positionChanged→style_panel.set_drag_position`, `video.dragCommitted→_reburn_preview`. In `_on_style_changed`: also `video.set_style(style_panel.get_style())`. In `load`: after `set_from`, `video.set_style(...)`. Add `_reburn_preview` (off-thread via `_run_async`, `assemble.style_preview_frame(at=video.current_time())`, push PNG to `set_burned_overlay`) with a `self._burning` guard. | M | B-3 | Canvas→panel→canvas loop fights an in-progress drag. | Manual: drag box → on release a burned PNG matching the playhead appears; sliders reflect the drag; no oscillation. Mitigation check: `set_style` ignores box re-sync while `_drag_mode is not None`. |
| **B-5** | **Fallbacks for the sink risk** (spec §f). Implement Fallback A (only pump/repaint the overlay when paused/seeking; cheap caption bar during playback) as the default behaviour; keep Fallback B (single FFmpeg still grab as canvas background) as a documented escape hatch if the sink never yields a usable frame. | S | B-3 | Low-end machine stutters while *playing* with the box shown. | Manual: confirm overlay is interactive when paused; playback stays smooth. No headless check (timing-dependent). |

**Definition of done (M1):** with a clip loaded and paused, the user can drag the subtitle body and
its L/R/vertical edges directly on the video; margins/pos sliders in the Kiểu-chữ tab update live;
on release an exact FFmpeg-burned frame replaces the approximation; play/pause/scrub/seek/volume and
the caption are unaffected; the headless coord-math + B-1 slider tests pass. The popup
"Xem thử kiểu" (`main._preview_style`) still works unchanged.

---

## Milestone 2 — Phase C: per-line styles + live "đang N dòng" badge

Keep the global `Default` style; layer **sparse positional overrides** on top via a new
`style_overrides.json` sidecar and an `EditorCue.style` field. Zero overrides ⇒ byte-identical to
today (gated by the P0-1 golden). Cue identity stays positional, so styles ride the `EditorCue`
object through split/merge/add/delete (the JSON is only a save-time snapshot keyed by list position).

### 2a — Per-line styles

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **C-1** | **`write_ass` multi-style.** Extract the dict→`Style:`-body + inline-prefix logic into `_style_fields(s,font,size,cover_hardsubs)`; split `_ASS_HEADER` so `[V4+ Styles]` injects N `Style:` lines; add `cue_styles: dict[int,dict] | None`. Merge each override onto base, dedup identical merged dicts by `json.dumps(sort_keys=True)` → names `Default,Sub1,Sub2…`; stamp each `Dialogue` with its resolved name + that style's inline `\pos`/`\fsp`. Apply `cover_hardsubs` opaque-box **floor inside `_style_fields`** so every style honours it. | M | P0-1 | Regress the `cue_styles=None` byte output; lose the inline-prefix per-line routing. | **C-1 is the gate:** `write_ass(cues, p, cue_styles=None)` must equal the P0-1 golden byte-for-byte. Then `write_ass(cues, p, cue_styles={1:{"primary":"#FFD400","bold":True}})` → assert exactly 2 `Style:` lines, line-2 `Dialogue` uses `Sub1`, dedup collapses two identical overrides to one Style. |
| **C-2** | **`EditorCue.style` + sidecar I/O** (`desktop/cuemodel.py`). Add `style: dict | None = None`. `save_vi` writes `{str(i): c.style for … if c.style}` to `style_overrides.json` (unlink when empty). `load_pair` hydrates via new `_load_style_overrides(job)` (`{}` if absent). | S | C-1 | Sidecar drifts from `vi.srt` order after edits. | Headless: build cues with `.style` on index 3+11, `save_vi`, assert JSON keys `"3","11"`; `load_pair` re-hydrates `.style` on the right objects; empty case removes the file. |
| **C-3** | **Carry `.style` through edit ops** (`editor_view.py`). Add `c.style` to the `EditorCue(...)` clones in `_snapshot` (386) and to `_split`/`_merge`/add. Policy: split → both halves inherit; merge → keep first cue's style; add → `None`. | S | C-2, P0-2 | Undo/split/merge drops the override (same class of bug as P0-2). | Headless: set style on a cue, run snapshot→split→merge list ops directly, assert policy holds; undo restores `.style`. |
| **C-4** | **Multi-row selection** (`desktop/widgets/subtitle_table.py`). `SingleSelection → ExtendedSelection` (line 31); add `selected_rows() -> list[int]` (`sorted({ix.row() for ix in selectionModel().selectedRows()})`); keep emitting `rowSelected(currentRow())` for the cue panel/seek/badge. | S | — | `currentRow()` ill-defined under extended selection. | Headless: load N rows, programmatically select a range, assert `selected_rows()`; confirm `currentRow()` still valid (drives single-row panel). |
| **C-5** | **Consume scope in the editor** (`editor_view.py`). `_on_style_changed`: if `style_panel.get_scope()=="selected"` → `_apply_style_to_selection()` (snapshot, copy `get_style()` onto each `cues[r].style`, `table.refresh_row`, `video.set_cues`) and **do not** clobber the global. Keep a cached `_global_style` updated only while scope=="all"; `get_style()`/render read it in selected mode. Build `cue_styles` from `c.style` at render/preview. | M | C-1,C-3,C-4 | **Global-vs-selected bleed** — selected-mode edits overwrite the global Default. | Headless: set scope=selected, apply yellow to line 3, render → assert line 3 `Sub1` yellow, line 4 still `Default` white, global `style.json` unchanged. This is the spec's named test. |
| **C-6** | **`assemble` reads the sidecar** (`backend/app/pipeline/assemble.py`). In `assemble` (works from `parse_srt(job.vi_srt)`, no style) load `style_overrides.json` → `cue_styles` and pass to `write_ass`. `style_preview_frame` (gets `EditorCue`s directly) builds `cue_styles` inline from `c.style`. `main._preview_style.work()` builds `cue_styles` from `editor.cues`. | S | C-1,C-2 | Sidecar not found at render time → overrides silently dropped. | Headless: write `style_overrides.json`, call `assemble`/`style_preview_frame`, assert the produced `.ass` contains the extra `Style:` and the right `Dialogue` names. |
| **C-7** | **Table override marker + reset** (`subtitle_table.py`, `editor_view.py`). In `_set_row`, if `getattr(c,"style",None)` tint the `#` cell + tooltip "Câu này dùng kiểu riêng". Add "Đặt lại kiểu câu này" (context-menu or button) → `cue.style=None` for selected rows. | S | C-4 | — | Manual: overridden rows show the marker; reset clears it and the next render drops the `Sub` style (re-run C-6 check after reset). |

### 2b — Live "đang N dòng" badge

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **C-8** | **`estimate_line_count` helper** (`desktop/widgets/style_panel.py` or `desktop/widgets/_metrics.py`). `QFontMetrics.boundingRect` measured in **1920×1080 ASS space** (`setPixelSize(get_size_px()*3)`, `setBold`, `AbsoluteSpacing=letter_spacing`), wrap width `1920 - margin_l - margin_r`, honour `\n` hard breaks, `total += round(br.height()/lineSpacing())`. If `pos` set → wrap width = full frame (approximate). | S | — | Qt vs libass shaping divergence flips a line near the boundary; missing UI font → wrong metric. | Headless: feed a long string at known size/margins, assert plausible counts (1 short, ≥2 long); confirm UI fonts are registered via `QFontDatabase.addApplicationFont` so metrics match the burn. |
| **C-9** | **Wire the badge** (`editor_view.py`). Add `self.lines_lbl` next to `fit_lbl` in the cue panel; `_refresh_lines_badge()` recomputes for `self._sel` using the **effective** style (cue `.style` if present, else global); call from `_update_cue_panel`, `_on_style_changed`, and `table.edited`. Amber when `n > line_mode` cap. | S | C-8 | Badge stale after a fast keystroke/slider. | Manual: select a line, shrink margins / grow size / type text → badge updates live; "Xem thử kiểu" confirms the true count for boundary cases. |

**Definition of done (M2):** the no-override path is byte-identical to the P0-1 golden (C-1 gate
green); selecting rows + scope "Các câu đang chọn" applies a distinct ASS style to only those lines
(headless render proves line N styled, N+1 untouched, global preserved); overrides survive
save/resume and split/merge/undo; overridden rows are marked and resettable; `cover_hardsubs` opaque
box still wins on every style; the "đang N dòng" badge tracks text/size/margins live for the selected
line. All headless render/construct checks pass.

---

## Milestone 3 — Quick-win quality upgrades (high value, low effort)

Independent of M1/M2; can be picked up any time after M0. Verify any model id/pricing via web —
don't trust memory (CLAUDE.md hard constraint).

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **Q-1** | **Fix Qwen label** (`backend/app/providers/translation/qwen.py`, `registry.py`). The provider is named `Qwen3` / `display_name="Qwen3 (DashScope)"`; update to the current Qwen id/label after confirming via web. | S | — | Renaming the class breaks the `registry.py` import. | `python -c "from app.providers.registry import list_providers; print(list_providers())"` lists the renamed provider; discovery import clean. |
| **Q-2** | **Upgrade RapidOCR ≥3.8 + PP-OCRv5 + onnxruntime-gpu** (`pyproject.toml`, `backend/app/pipeline/ocr.py`). Bump deps, keep the bottom-third crop + confidence/similarity post-processing. | S | — | onnxruntime-gpu / CUDA mismatch on the 8 GB box. | `/run-stage` OCR on a real Douyin clip with hardsubs → `cn.srt` produced, CER not worse than before; GPU path falls back to CPU cleanly if libs absent. |
| **Q-3** | **2-pass translate live** (DeepSeek-V4-Flash draft → frontier refine, e.g. Gemini 2.5 Flash). Confirm `deepseek-v4-flash` id and the refine model id/pricing via web. Wire/verify the `draft`→`refine` chain in `translate.translate`. | S | — | Stale/legacy model ids (`deepseek-chat` deprecates 2026-07-24); pricing surprise. | Web-verify both ids; `/run-stage` translate on a sample `cn.srt` → `vi.srt` shows refine-pass improvement; provider list shows both engines selectable. |

**Definition of done (M3):** provider list shows correct labels; OCR runs on the upgraded stack
(GPU when available, CPU fallback intact); the 2-pass CN→VI chain is selectable and produces a
refined `vi.srt` on a real clip; all model ids/pricing web-verified and noted in the commit.

> **✅ DONE (commits `01336ba`, `2260bbc`).** Q-1: `Qwen3`→`Qwen`, labelled "Qwen-Plus (DashScope)"
> by the rolling `qwen-plus` alias. Q-2: OCR prefers `rapidocr` v3 (PP-OCRv5, CUDA via
> onnxruntime-gpu) with GPU→CPU→legacy-package degradation; `_to_lines` normalizes both result
> APIs; `[ocr]` extra bumped to `rapidocr>=3.0`. Q-3: already wired end-to-end; `deepseek-v4-flash`/
> `-pro` and `gemini-2.5-flash` web-verified current → no id change. **Bonus:** fixed the refine pass
> (it re-ran CN→VI on the VI draft) → new `TranslationProvider.refine_texts(cn, vi_draft)` + a
> dedicated `REFINE_SYSTEM_PROMPT` editor pass (chat providers override; DeepL/passthrough no-op).
> All headless-verified; a real-clip OCR/translate smoke still wants the heavy deps + a hardsub clip.
> Adversarial review (pipeline-debugger) on the OCR change found no confirmed defects. **Not yet
> verified on-device:** whether the CUDA path actually engages (needs rapidocr v3 + onnxruntime-gpu
> installed → check nvidia-smi/timing; a wrong param key degrades to CPU silently).

---

## Milestone 4 — Engine depth (medium effort, deferrable)

Larger features from the proposal roadmap; pluggable, so they slot in via the provider interface
(`/add-provider`) without touching pipeline wiring. Sequence by value; none block M1–M3.

| # | Task | Effort | Dep | Key risk | Verify |
|---|------|--------|-----|----------|--------|
| **E-1** | **Auto hardsub detection** (`backend/app/pipeline/hardsub_detect.py` — currently raises `NotImplementedError`). PP-OCRv5 detection-only over bottom-third samples; stable recurring box ⇒ OCR path, else ASR. Wire into `desktop/worker.py:111` so the OCR/ASR branch is auto-chosen with manual override. | M | Q-2 | False positive/negative routes to the wrong transcription path. | Headless: run on one hardsubbed and one clean sample clip; assert it returns OCR vs ASR respectively; worker still honours an explicit `subtitle_source` override. |
| **E-2** | **FunASR Paraformer + WhisperX timing** (new ASR providers via `/add-provider`; fallback chain in `asr.py`). Paraformer transcript + WhisperX word timestamps; respect 8 GB VRAM → run sequentially, CUDA→CPU fallback. | M | — | Concurrent heavy models OOM the 8 GB GPU (CLAUDE.md constraint). | `/run-stage` ASR on a no-hardsub clip → CN SRT with better CER than faster-whisper-large alone; confirm models load sequentially, CPU fallback works. |
| **E-3** | **LLM proofread OCR (confidence-thresholded, opt-in)** (`ocr.py` + a provider). Batch low-confidence lines → vision LLM → corrected `cn.srt`; gate behind a budget/confirmation dialog. | M | Q-2 | Cloud cost blow-up; uploads frames (privacy — keep opt-in per CLAUDE.md). | `/run-stage` OCR with proofread on a noisy clip → corrected lines; confirm it only fires on sub-threshold reads and is off by default. |

**Definition of done (M4):** hardsub detection auto-routes OCR vs ASR on sample clips with a manual
override; FunASR+WhisperX selectable and improves Mandarin CER while staying within VRAM; OCR LLM
proofread is opt-in, budget-gated, and corrects low-confidence lines. Each verified headless via
`/run-stage`.

---

## Backlog (research/design needed — not scheduled)

Per-line timing nudge (±ms) + lock; hardsub inpainting (STTN/LaMa); glossary UI; batch
queue with pause-at-gate; voice cloning (legal/ethical gate). Pull into a milestone only when a
concrete spec exists.

---

## Working rules

- **Headless first.** Every backend/data-model change ships with a Python construct/render check
  (`write_ass` bytes, `assemble.style_preview_frame` PNG, `cuemodel` round-trip) before the manual
  GUI smoke. GUI drag/paint gets a manual smoke only.
- **Guard the golden.** The P0-1 ASS snapshot is the regression gate for all of Phase C; never edit
  `write_ass` without re-running it.
- **Cue identity is positional** — index = `Cue.index-1` = `vi.srt` order = override list position.
  Never introduce a UUID key; styles ride the `EditorCue` object, the JSON is a save-time snapshot.
- **Providers are pluggable** — new engines = one file + `registry.py`; never hardcode a provider in
  pipeline code.
- **Verify model ids/pricing via web**, run heavy models sequentially on the 8 GB GPU, keep the
  human-edit gate before any TTS/render, and keep frame-uploading OCR modes opt-in.
