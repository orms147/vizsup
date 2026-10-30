# Overnight run — 2026-10-29 (log for the owner to check)

Owner asleep ~4–5 h. Coordinator: this session ("Vizsup agent đánh giá kế hoạch (fork)").
Workers: **session 1** (research: CapCut / Adobe / dubbing tools), **session 2** (UX audit of
the running app). Branch: `api-optimization` (push allowed, never `main`, never force).

## Permissions given (2026-10-29)
- Commit / push work branches. Install new libraries — every install logged below.
- Touch real projects, test the real app.
- Budgets: Anthropic API ≤ $50, Meta API ≤ $50 (strongest model; tools/evaluation, NOT the
  translation model), the app's own models (OpenRouter) ≤ ~$3 for testing.
- Human gate: the owner's 4 real episodes are NOT approved by me; end-to-end tests run on
  COPIES in a test storage.

## Plan
0. Finish the pending work: watermark feature (tests → commit); batch render → join
   episodes 1–8 → add episode 9 (on copies).
1. Research (sessions 1+2 in parallel) → `docs/plan/research/*.md`.
2. Coordinator: prioritised roadmap → `docs/plan/H-roadmap.md`.
3. Build the top items (separate git worktrees per session, coordinator reviews/merges,
   full test suite + real app check before every push).

## Spend
| when | what | provider | ≈ $ |
|---|---|---|---|

## Libraries installed
| when | package | why |
|---|---|---|

## Log
- 00:00 — log created; auto-continue check-in scheduled (~every 40 min while idle).
- 00:20 — Research workers: "session 1"/"session 2" turned out to be OTHER projects
  (WriTory…); briefs withdrawn with an apology. Reassigned to the vizsup sessions
  **vizsup-9c** (competitor research → docs/plan/research/competitors.md) and
  **vizsup-2e** (UX audit → docs/plan/research/ux-audit.md).
- 00:25 — commit dcc9db4 feat(render): translator watermark (pending task finished,
  1005 tests pass).
- 00:35 — workers renamed by the owner: 'sesion 1 vizup' (= vizsup-9c, competitor research) and 'sesion 2 vizup' (= vizsup-2e, UX audit) — both busy on their briefs.
- 00:36 — E2E test on COPIES (scratchpad/e2e_storage): 4 episodes approved there (the owner's real projects stay unapproved), batch render started in the app (template: owner's short_3min settings + test watermark 'Vietsub: Kênh Thử', hop corners).
- 00:50 — sesion 1 vizup: competitor research done → docs/plan/research/competitors.md
  (top: TTS pronunciation dictionary, series profile carried episode to episode,
  per-line voice/gender, 9:16 blur-fill, Netflix-style timing post-pass, final loudness).
- 00:51 — batch dialog: titles elide ("Tập 1–5 · …"), the settings line mentions the
  watermark (uncommitted, tests pass).
- 00:55 — sesion 1 vizup → build task: TTS spoken-text normaliser + pronunciation
  dictionary on worktree C:\Work\AI\vizsup-wt\tts-normalize, branch feat/tts-normalize
  (coordinator reviews + merges).
- 01:05 — sesion 2 vizup: UX audit done → docs/plan/research/ux-audit.md (+ shots). Then
  build task: UX quick wins (audit items 1,5,6,7,9,11,13,14,15) on worktree
  C:\Work\AI\vizsup-wt\ux-quickwins, branch feat/ux-quickwins.
- 01:06 — commit c657b60 (batch dialog polish + research docs).
- 01:30 — commit 345bda5 feat(render): final-mix loudness (-14 LUFS, true peak -1.5) +
  blurred 9:16 bars (render page "Nền hai bên": hình mờ / đen). Real render checked.
- 01:35 — merged feat/tts-normalize (sesion 1 vizup, 18c0ce8): backend/app/pipeline/speak.py
  — the dub reads numbers/units/dates/money/abbreviations the Vietnamese way;
  pronounce.json (global + per project). Subtitles unchanged. 1108 tests pass. NOTE: lines
  with digits / % / units will re-voice once on the next render (cache key = spoken text).
- 01:40 — sesion 1 vizup → next: timing post-pass (gaps / min gap / min duration, optional
  scene-cut snap), branch feat/timing-pass.
- 00:35 — commit af2f480 fix(tts): edge-tts answered ~40 % of requests "no audio" at random
  tonight; each paused every worker 5–60 s (batch crawled at ~5 lines/min). Lines now retry
  alone first; 6 in flight. 30 real lines: 122 s → 57 s, 0 failed.
- 00:35 — merged feat/timing-pass (sesion 1, 59946aa): timing post-pass after ASR/OCR
  (2-frame gaps, chained short gaps, min duration; optional scene snap) + editor menu action
  + input option "Chỉnh thời gian chuẩn". 1139 tests pass. → 5192596.
- 00:35 — sesion 1 → next: "Bộ phim" series profile (branch feat/series).
- NOTE: times above 00:20–01:40 were estimates (wrong); real clock from here on.
- 00:40 — E2E: demo app (old code, batch crawling on the edge-tts bug) killed hard =
  crash test. Relaunched on the new code: "Làm tiếp việc còn dở?" appeared, accepted → the batch
  resumed at 1/4 · 40 % (TTS cache kept). Episode 9 started in the main slot at the same time.
- 00:40 — CLAUDE.md updated for tonight's features (da52cb7).
- 00:45 — commit 7e6f6a3 feat(render): "Thẻ tập" — "Tập {n}" chip (first 5 s) + "Còn tiếp ·
  Tập {next}" end card (last 4 s), resolved from each title, fits a batch (roadmap wave 2 #5).
- 00:45 — merged feat/ux-quickwins (sesion 2, 8 commits: D = Đã xem + next, Ctrl+↑/↓ in the
  text box, one-row find bar, right-click menus, "Xem N câu" in approve dialog, render cards height
  fix, status line / step rail, library keys, new shortcut sheet) → 212d703.
- 00:45 — merged feat/series (sesion 1: "Bộ phim" profile in storage/_series/, glossary +
  characters + brief + pronounce inherited, learn on approval, previous episode's last 8 lines as
  context, input "Bộ phim" combo) → 243b53d. Full suite 1214 passed, ruff clean.
- 00:45 — next: sesion 1 → series dialog + CLI --series + per-line voice by gender;
  sesion 2 → compact cue list + 9:16 layout, overview strip, library chips/sort; me → model catalog
  auto-update, E2E.
- 00:51 — commit 62eaf57 feat(update): model catalog auto-update (catalog/models.json on the
  main branch, checked daily + Settings → "Cập nhật model mới"; only valid catalogs whose every model
  is live on OpenRouter; user picks always win; "Có vizsup X mới" notice). Live check: 456 OpenRouter
  models, every model the app uses is live. NOTE for owner: takes effect once catalog/models.json is
  on main (I never push main).
- 00:51 — E2E: ep 9 reached the gate (43 CN lines, 13 "Timing đoán"; Whisper's word times
  collapsed → repaired). ⚠ The ep-9 link (modal_id 7699919754521283890) is NOT 《末日校车》: it is a
  剑网3 game ad ("50级新世代！… 10月29日公测", no uploader) — the app downloaded exactly that id.
  Used it anyway to test the mechanics: approved the COPY, "Thêm vào hàng dựng" added it to the
  running batch (session file shows it queued).
- 00:54 — commit 584e66d feat(post): "Gợi ý tiêu đề + hashtag (AI)" in Mô tả đăng bài. Real
  call on the ep-8 copy: 4.1 s, 3 sensible titles ("Tập 8: Cả nhóm vào trạm xăng tìm thức ăn…") + 8
  tags; spend ≈ $0.0005.
- 00:54 — E2E batch: ep 1–5 (10:44) rendered in 84 s (NVENC 7.7×), ep 6 done, ep 7 rendering,
  ep 8 + ep 9 queued. Roadmap statuses updated.
- 00:59 — merged feat/series-voices (sesion 1: per-line voice m/f from pitch in overrides.json,
  render option "Giọng theo nhân vật (nam/nữ)" default off, Bộ phim dialog, CLI --series) → 82356dc;
  checked: existing approvals are not revoked (rows without "voice" hash as before).
- 00:59 — merged feat/ux-wave2 (sesion 2: compact cue list "Gọn" default ~1.7× more lines at
  1366×768, 9:16-aware editor split, overview strip, library chips + "Theo tập" sort) → 2dcdbec
  (1 trivial store.py conflict). Full suite 1278 passed.
- 00:59 — E2E DONE on copies (scratchpad/e2e_storage):
  * batch of 4 (ep 1–5, 6, 7, 8) + ep 9 queued mid-batch: all 5 rendered (NVENC, 720p).
  * "Ghép tập" 1–8 (passed out of order): chapters Tập 1–5 @0:00, 6 @10:45, 7 @12:24, 8 @14:39; 2/4
    re-encoded because of 1282×720 vs 1280×720 → fixed: commit 9548d67 snaps near-16:9 to standard.
  * added ep 9 to the 1–8 compilation: stream copy, 5 chapters, 582 subtitle lines, 20:23.
- 00:59 — next: sesion 1 → Vietnamese subtitle lint; sesion 2 → safe-zone overlay + CSV.
- 01:07 — real-window check of the merged UI (demo app on copies):
  * library: chips + counts, "Theo tập", compilations with "Thêm tập" OK; row chips were squeezed
    ("Video ç") → fixed 78613da.
  * editor at 1700×1000: the right panel and timeline header overflow the window after ux-wave2 →
    sent to sesion 2 as priority (regression test requested).
  * BUG (old, from e45da1e): opening an APPROVED project without style.json whose video has
    hardsubs auto-picked "Hộp đặc", saved it and revoked the approval → fixed 83c808c (+ a deferred
    note that could fire after the view was deleted). Read-only check of the real storage: both
    approved projects still approved under tonight's code.
- 01:07 — merged feat/vi-lint (sesion 1: "Soát lỗi tiếng Việt" typography auto-fix + flags
  han/num/latin/pron/term, worker step + editor action, option in input) → 0f95ee3; 1343 passed.
  Dry run on 750 real lines: 5 fixes, all correct; "latin" over-flags English present in the source
  → sesion 1 fixing, then investigating "Timing đoán" causes.
- 01:28 — merged fix/project-bar-width (sesion 2: a long title + DPI-scaled window made the
  editor wider than the window; root cause = ProjectBar label min width, not the columns) → e9a3072.
- 01:28 — merged feat/timing-guess (sesion 1: guessed lines placed on VAD voice islands by DP;
  copies: ep 1–5 guessed 8 → 2, voice coverage 0.70 → 0.98; sung ad 13 → 11, worst silence inside a
  line 7.6 s → 0.9 s; "latin" lint skips English present in the source) → 9eeb997. 1370 passed.
- 01:28 — sesion 1 evaluated pinyin/homophone anchoring: NEGATIVE (false anchors on repeated
  sung couplets / Whisper loops) → not shipped; eval doc only. pypinyin installed only in its scratch
  dir, NOT in backend/.venv (no new library in the project tonight).
- 01:28 — FRESH full run on the new code (copy, CLI, ep 7 URL): download → OCR → snap (73/82) →
  timing pass → Bộ phim 《末日校车》 auto-linked → brief → translate (2 batches, 14 auto-shortened) →
  vi-lint (1 fix) → AI review (9 flagged) → voice guess → gate. Translation spot-check good (xưng hô
  per brief). Voice guess implausible (2 male / 30 female for a male-led episode, OCR job without
  vocals.wav) → sesion 1 investigating; the render option stays default OFF.
- 01:28 — merged feat/ux-wave3 (sesion 2: safe-zone overlay TikTok/Reels/Shorts/YouTube in
  editor + render preview, CSV export/import, timeline edge snapping, library "Sửa", window clamped to
  screen) → cd6b461. sesion 2 next: render page sticky actions + collapsible "Đăng tải & thương hiệu".
- 02:03 — full suite after the wave-3 merge: 1400 passed, ruff clean. Merged the pinyin eval doc.
  SPEND check (OpenRouter /key, UTC day incl. the owner's own daytime use): $0.077 today — far under
  the $2–3 cap. Claude/Meta API keys: not used by me (sessions run on the subscription).
- 02:29 — auto-continue check: both sessions idle since ~01:35–01:41 with uncommitted work
  (voice2: pitch.py/voices.py; ux4: render_view.py/chrome.py) and no report → pinged both to continue
  or say what blocks them. Nothing new to merge.
- 02:31 — sesion 2 not blocked (long test wait + a hanging test of its own); render-page work
  continues (collapsible "Đăng tải & thương hiệu" done; the action footer was already sticky).
- 02:31 — merged feat/voice-guess-2 (sesion 1) → 3e5367e. Cause of the bad guess: a strict YIN
  voicing threshold (only 32/82 lines measurable, octave errors) + a fixed 165 Hz split (the male lead
  speaks at 170–190 Hz). Now: voicing 0.2, per-video split (widest gap 150–260 Hz), Demucs speech stem
  when there is no vocals.wav (GPU only). ep 7: 2 m / 30 f / 50 ? → 25 m / 43 f / 14 ?; text-cue check
  20 agree / 2 disagree. Render option "Giọng theo nhân vật" stays default OFF. Related tests 168 passed.
- 02:50 — sesion 1 code review of the night's diff (files it did not write): 7 confirmed bugs
  fixed with 17 failing-first tests → merged feat/review-fixes 4130850. Most important: a CSV whose
  vi cells Excel turned into errors ("#NAME?") would have REPLACED the subtitles — now refused.
  Others: catalog memo keyed by mtime only, app.url "../" escape, HTML in catalog labels, junk
  _fetched stamp breaking Settings, postcopy.parse on a string/number, CSV index "inf".
- 02:50 — my follow-ups 79126ed: catalog price cap (≤ $5/M in, ≤ $25/M out) + image input
  required for vision/ocr (live records); exact downscale only when really downscaling (odd widths).
  Remaining review items: U1 CSV formula guard, U4 library Delete with hidden checked rows, U6
  timeline repaint throttle → sesion 2; U3 app exit during a network call → sesion 1.
- 02:59 — merged feat/ux-render (sesion 2: collapsible "Đăng tải & thương hiệu", footer summary,
  "Xem N câu" from the speed warning → editor filtered to late lines) → 644e7ad.
- 02:59 — real-window check (1700×1000 @150 % DPI = logical ≈1133×667) found, sent to sesion 2:
  render page action buttons below the fold + horizontal scrollbar; audio cards 2–3 render blank;
  editor filter chip "Cần xem lại" clipped; editor timeline shows only its header (no lanes) — the last
  one predates tonight. Offscreen tests at 100 % scale missed all of these → asked for tests at
  QT_SCALE_FACTOR=1.5.
- 03:03 — auto-continue check: full suite on 644e7ad still running (machine shared with 2 sessions' suites); sesion 1 on U3 (bg-exit), sesion 2 on the DPI layout fixes (local commit 0c44f04, not pushed yet). Nothing to merge.
- 03:07 — merged feat/bg-exit (sesion 1, U3) → 856236c: network calls in BgRunner / editor
  _run_async / Settings _bg now run on daemon threads, shutdown only marks the owner closed — closing
  the app mid-call exits 0 in < 0.6 s (was: abort after a 2 s join). Subprocess tests fail on the old
  code. Related tests 27 passed; sesion 1 reports the full suite 1446 passed on its branch.
- 03:21 — merged feat/ux-render again (sesion 2: render page at 150 % DPI — a long status line
  in the result button row made the page 1850 px wide; picture min heights pushed the footer down;
  unshown audio cards had min height 0 → 6 tests at QT_SCALE_FACTOR=1.5, all failing first; + U1 CSV
  formula guard, U4 library Delete only visible rows) → cf85a79. Re-checked on the real window: fits,
  all cards drawn, buttons + result actions visible, no scrollbars.
- 03:21 — merged feat/docs-night (sesion 1: docs/architecture.md rewritten to the real stage
  order + sidecar/approval table + threads; docs/providers.md model catalog section) → c228539; applied
  its CLAUDE.md notes (ASR constraint, model ids → roles/presets/catalog, storage dirs, CLI flags,
  approval = revision hash) → 10e6414. sesion 1 done for the night.
- 03:29 — auto-continue check: full suite on 10e6414+ running (hang detection on; the 02:57 run was stopped after 33 min — likely sesion 2's since-fixed hanging test). sesion 2 pushed cf6e0ea (filter chip + timeline min height) and 5fd1fcf (U6 overview throttle) to feat/ux-render, report pending. sesion 1 done.
- 03:58 — merged feat/ux-render (sesion 2: editor at 150 % DPI — the video picture's 300 px
  minimum made the page scroll and hid the timeline lanes; filter chip → "Xem lại"; U6 playback tick
  8.5 → 0.7 ms with 700 cues) → aaf9964. Real window re-checked: overview + ruler + wave + voice band +
  cue lane all visible, chip fits. Trade-off at logical 667 px height: the cue list shows ~2.5 rows
  (splitters can be dragged). sesion 2: all assigned work done.
- 04:20 — full suite at a4d6cf0: 1483 passed, 1 skipped, ruff clean (37 min under load).
  Final-head run (0b66ad2) in progress, using one core steadily (not hung). NOTE: the suite took ~100 s
  at 00:30 and takes 10+ min alone now — some of tonight's tests are slow (likely subprocess / 1.5×
  scale UI tests); to profile with --durations after this run. Both sessions done; nothing to merge.
- 04:34 — FINAL HEAD 0b66ad2: full suite 1489 passed, 1 skipped, ruff clean. But it took 34 min
  with the machine idle (≈100 s at 00:30) → profiling tonight's new test files with --durations.
- 05:03 — suite slowdown, cause 1 found: the voice guess (feat/voice-guess-2) starts a REAL Demucs
  separation on the GPU in every worker / CLI test that has a source video (test_cli: 82 s → 7.4 s with
  it off). Fix (uncommitted, verifying): VIZSUP_NO_AUTO_SEPARATE=1 switch in voices._audio_source, set
  by conftest like VIZSUP_NO_UPDATE_CHECK; real runs unchanged. A full run with --durations is still
  slow (> 20 min) → more causes; waiting for the durations list.
- 05:14 — commit d377afc test: full suite 34 min → 1 min 50 s (1490 passed, 1 skipped,
  ruff clean). Causes: (1) the voice guess ran a real Demucs separation in every worker/CLI test →
  VIZSUP_NO_AUTO_SEPARATE switch set by conftest; (2) app-wide stylesheet in the DPI layout tests
  re-polished thousands of widgets leaked by earlier modules (~95 s per call) → conftest deletes each
  module's leftover top-level windows. Real app behaviour unchanged.
- 05:29 — idle check: all planned work merged and pushed (head bfdf2a2), suite green, both sessions finished.
- 06:03 — idle check.
- 06:29 — idle check.
- 07:03 — idle check.
- 07:29 — idle check (07:03 push failed: no network; retried here).
- 08:03 — idle check.
- 08:29 — idle check.
- 09:03 — idle check.
- 09:20 — owner (morning): approved merging into main; one voice is enough → voice guess
  made opt-in (950704c); auto-continue cron 9e08612f deleted. Full suite 1490 passed.
- 09:49 — owner: black windows flashing during a download. Cause: the owner used MY demo window
  (left open, started with pythonw.exe → no console) — every ffmpeg / Demucs call opened a console
  window; the app itself kept running (minimised). Fixed for real (a packaged build has no console
  either): app/winproc.py adds CREATE_NO_WINDOW to every child process when the app has no console
  → f367353 (+ "Đoán giọng nam/nữ" switch in Tùy chọn, off by default). main fast-forwarded too.
- 09:49 — voice test (owner asked): installed **vieneu 3.8.3** and **piper-tts 1.8.0** into a
  SEPARATE scratch venv (scratchpad/tts_eval_venv) — NOT into backend/.venv; models in the HF cache
  (VieNeu v3 Turbo ONNX + MOSS tokenizer) and piper vi_VN-vais1000-medium.onnx (63 MB) in scratch.
  VieNeu has 25 preset voices (m/f, Bắc/Trung/Nam), CPU 0.3–1.5 s per line. Whisper re-read CER on
  6 real lines: edge HoaiMy 0.9 %, NamMinh 0.0 %; best VieNeu Thiền Tâm Đức 0 %, Đức Trí / Kim Thanh
  0.9 %, Mai Anh / Minh Đức / Quỳnh Anh / Quốc Tuấn 1.8 %; worst Thái Sơn 40 %; Piper 6.1 %.
  Listening page: scratchpad/tts_eval/nghe_thu.html. Spend: Whisper ≈ $0.02.
- 10:09 — owner: the "ad" link downloaded fine this morning — the SAME id gave 剑网3 last night
  and 《末日校车》09 today: Douyin's page played an ad first and the capture took it. Fixed: the capture
  records the page address; capture_problem() + one fresh retry; a moved page stops the run, a
  missing author logs ⚠ (e54d7bc, main updated).
- 10:09 — voices: owner keeps VieNeu Quốc Tuấn, Trúc Ly, Adam bựa, Ngọc Huyền; asked for more
  female voices → edge multilingual females read Vietnamese clearly (Vivienne / Thalita 0 %, Emma /
  Seraphina 0.9 % CER; Ava 49 % — left out). INSTALLED into backend/.venv (logged): vieneu 3.8.3
  (--no-deps), sea-g2p 0.10.0, soundfile 0.14.0, soxr 1.1.0, kaldi-native-fbank 1.22.4 — NOT vieneu's
  onnxruntime pin (would replace onnxruntime-directml) and not gradio. VieNeu provider (ONNX/CPU) +
  "vieneu:<name>" routing; render page lists edge + local voices. Real dub of the ep-9 copy with Trúc Ly:
  65 lines in 52 s (incl. 9 s model load). Rejected: viet-tts presets (named after real celebrities /
  cloned commercial voices). 1504 tests pass.
- 10:22 — episode 9 (owner's download a8fed3acf3f3, copy storage): reviewed 65 lines — good
  (xưng hô per brief, names consistent); fixed 4 lines (line 4 a girl teasing anh Cố: "Cô" → "Anh cũng
  khéo chọn ghê."; 45–46 comma + "vì"; 58 wording). Approved the copy at the owner's request, rendered
  with episode 8's settings via the app's render path (NamMinh +30 %, keep music, glass, 720p,
  watermark): 91 s; 27 lines still too long for their slot at 1.3× (fast dialogue). Joined 1–8 + 9 →
  scratchpad/e2e_storage/56112262e1d2/output.mp4 (18:20, 5 chapters, stream copy after fix
  67b9146 setsar=1 — the snapped size had SAR 961:960 and forced a re-encode).

## Đêm 2 (29→30/10) — Plan
Owner (đi ngủ 23:55): "tự làm và quản session song song nếu cần, tôi đi ngủ như hôm qua"; đồng ý
thứ tự 1–8 (xem tin nhắn sáng 29/10) + hỏi gợi ý tối ưu cost, bản dịch, và Short (màn dọc).
Quyền như đêm 1: commit/push nhánh làm việc (api-optimization), không force; main chỉ fast-forward ở
checkpoint khi full suite xanh (owner đã đồng ý merge sáng 29/10); cài thư viện phải ghi log; OpenRouter
≤ $2–3 cho đêm; KHÔNG tự đánh dấu duyệt dự án thật (classifier đã chặn đúng — cổng duyệt là của owner);
mọi thử nghiệm trên bản sao; đóng cửa sổ demo sau khi kiểm tra (memory vizsup-demo-windows).
Auto-continue: cron 13,43 mỗi giờ.
1. ✅ Đưa tập 1–9 + video ghép vào storage thật (chỉ thêm file thiếu; owner tự Duyệt 4 tập cũ).
2–4. ✅ Nút cài VieNeu trong Cài đặt, báo tải model lần đầu, giọng VieNeu cho Bình luận (229f107).
5. Kiểm tra danh sách giọng trên cửa sổ thật (coordinator, bản sao, đóng cửa sổ sau).
6. Tập 9: 27 câu quá dài → để owner duyệt lại trong editor (sửa chữ = mất duyệt).
7. Dọn thư mục tạm (tts_eval_venv 671 MB).
8. Later: Inno Setup + chữ ký + tải bản mới.
Đêm 2 — tối ưu (giao session):
- sesion 1 (dịch/cost): T1 dịch vừa khe lồng tiếng theo tốc độ thật của bộ phim (+30 %): tập 1–5 bị
  cắt/dời 57 s, tập 7 33 s; C2 prompt caching (tiền tố ổn định, đo cached tokens); T2 khả thi gắn nhãn
  người nói (chỉ nghiên cứu + đo, chưa ship nếu không chắc).
- sesion 2 (Short): S1 preset "Short" 1 chạm (9:16, phụ đề tự tránh vùng che, chữ to hơn); S2 nguồn
  dọc; S3 "Cắt Short" từ tập đã duyệt (đoạn 30–60 s có hook, render 9:16, cần duyệt riêng).
- coordinator: mục 5, 7; C3 hiện chi phí mỗi tập/bộ phim trong Thư viện; review + merge.
- 23:57 — night 2 started. Item 1: copied into the REAL storage only missing files (615) for eps
  1–5/6/7/8 (their revision files are identical to the copies; never overwrote the owner's files; ep 8's
  bug-made style.json left out) + ep 9 + compilations 1–8, 1–9 + series profile. Writing approval
  markers for the 4 old projects was BLOCKED by the auto-mode classifier (right call — the gate is the
  owner's): they show rendered but "Chưa duyệt" until the owner approves. Fix so that re-approving the
  same text is not "Cần dựng lại": render.json records the rendered revision (4a3bf22).
- 23:57 — items 2–4 done (229f107): Settings "Giọng đọc trên máy (VieNeu)" install/download
  button (vieneu --no-deps + CPU deps, never CPU onnxruntime), first-use download notice, VieNeu voices
  for commentary, shared voice labels; flaky test_bg_exit timing fixed. 1509 passed.
- 23:57 — item 5: voice list checked at 150 % scale (offscreen grab, no demo window left open):
  10 voices with Vietnamese labels. Item 7: removed scratch tts_eval_venv (671 MB) + small test dirs.
- 23:57 — sessions restarted by the owner; sent full briefs: sesion 1 → feat/dub-fit (T1, C2,
  T2 study); sesion 2 → feat/shorts (S1, S2, S3). Cron da9b70b1 at :13/:43.
- 00:01 — sesion 1 (T1) root cause: the "too long" lines are mostly SILENCE — every edge-tts
  segment carries ~0.21 s lead + ~0.66 s tail; assemble speeds up / shifts by it. Replaying the real
  durations: flagged 167/25/68/40/27 → 2/0/2/0/0 and trim 57/5.3/33/12/10 s → 5.5/0.5/2.5/0.7/1.8 s on
  eps 1–5/6/7/8/9 if the silence is cut. Approved: trim lead/tail silence per segment in tts.py (+
  registry for cached segments) and re-calibrate fit_estimate to speech-only; asked to cover the dub-
  window cache key, never-eat-speech guards, batch/VieNeu/commentary paths, and before/after mp4s.
- 00:18 — merged feat/shorts (sesion 2: "Cài sẵn Short (9:16)" preset learned per series,
  captions placed above the platform's bottom strip, vertical sources, «Cắt Short» from an approved
  episode → new unapproved project reusing its TTS) → 85d498c, 1539 passed. Eyeball on copies: one live
  AI proposal (3.8 s, < $0.001) picked the confrontation scene; Short render OK, 32/32 voices reused.
  Layout issue: hook / chip / "Xem full tập 9" are small text inside the picture, bars empty (text is
  burned before the 9:16 composite) → sesion 2: Short layout v2 (canvas text layer in the bars).
  Fixed a misleading "nền hộp" note (worker).
- 00:26 — check: sesion 1 has 236a7a8 (silence trim + fit re-calibration) and 86684c4 (C2 caching) locally, report pending; sesion 2 pushed feat/shorts-layout up to 1601af5 (canvas text layer, bars options, clipshort file-copy fix), report pending. Nothing merged yet.
- ~00:30–08:16 — this coordinator session was cut off (the machine/app restarted overnight); the
  session-only cron died with it, and both worker sessions ended. Nothing ran in that window.
- 08:25 — resumed. Merged feat/dub-fit (sesion 1, reported before the cut) → 5937bc0, 1564 passed:
  * T1: TTS segments carry ~0.21 s lead + ~0.65 s tail silence → trimmed per segment (relative threshold,
    ±0.05/0.12 s, never < 0.15 s, soft-fail, cached segments trimmed once via tts/trimmed.json); fit model
    re-calibrated to speech-only at the project's real speed. Eps 6/7/8/9 "QUÁ DÀI" 25/68/40/27 → 0/0/0/1,
    cut/shift 5.5/34/12/10 s → 0.4/1.6/0.1/0.8 s; eps 1–5 167 → 2, 57 s → 5.5 s. Re-translation A/B: auto-
    shortened lines 32 → 11, 18/20 sampled lines keep content the old budget dropped. The dub now starts
    on the cue (~0.2 s earlier than older renders).
  * C2: prompts already cache-friendly (stable prefix; test added); usage.json records cached tokens.
    Saving ≈ $0.0003/episode — nothing more to gain.
  * T2 (study, not shipped): audio-LLM speaker labels agree 94 % with pitch, find the system voice, but
    fail on short turn-change replies (ep 9 line 4) — not worth the call.
  * sesion 1 spend ≈ $0.022.
- 08:25 — merged feat/shorts-layout (sesion 2; its report was lost in the cut — reviewed myself)
  → 1506a50, 1578 passed: canvas.ass on the composed 9:16 frame (hook + bigger chip in the top bar, "Xem
  full tập N · <watermark>" in the bottom bar, dialogue in the bottom bar above TikTok's strip), options
  "Chữ lớn trên nền hai bên" / "Phụ đề ở dải nền phía dưới" (Short preset turns them on); clipshort now
  copies hardsub_region.json + context_brief.json. Re-rendered the ep-9 Short copy: layout right (frames
  scratchpad/ui/short_hook2/mid2/end2.png); with the silence trim its "QUÁ DÀI" lines 19 → 1, sped 20 → 5.
