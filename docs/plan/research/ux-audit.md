# UX audit — desktop app, screen by screen (2026-10-29)

Research only: no code was changed. Scope: Bắt đầu (input), Sửa phụ đề (editor), Dựng video (render), Thư viện (library), Cài đặt (settings).
Judged for the owner's real job: reviewing a ~300-line episode fast (CapCut / Subtitle Edit / Premiere habits).

## How this was done (and its limits)

- Built the real `MainWindow` offscreen (`QT_QPA_PLATFORM=offscreen`) at **1366×768** and **1920×1080**, on a *copy* of the demo storage plus one synthetic project: 300 cues, 15:45, 9:16 test-pattern video, 83 lines flagged "cần xem lại". The real `storage/` was never touched.
- Offscreen has no CJK font, so I registered Microsoft YaHei in the screenshot script only (otherwise Chinese titles and the zh line were blank). The app itself is unchanged.
- Video frames are a test pattern, there is no audio/waveform detail, and nothing was clicked by a human. Anything that is about *timing or feel* is read from the code, and marked **(code)**. Anything that might be an offscreen artefact is marked **(verify)**.
- Screenshots: `docs/plan/research/shots/` (`NN-name_WxH.png`). Used below: `03b-editor-line120`, `04-editor-tab0..4`, `05-render`, `06-settings*`, `07-editor-find`, `08-shortcuts`, `10-input-error`, `01-input`, `02-library`.
- Effort: **S** < ½ day, **M** ~1 day, **L** multi-day.

What already works and must not regress: Enter = save + next line with text focused; ↑/↓ lines, ←/→ ±10 s, F full screen, F8 next flagged, Ctrl+Z/Y one step per gesture, 600 ms autosave with the "Đã lưu" chip, Ctrl+Enter approve, the "Cần xem lại" filter with its count badge, drag-to-scrub on the timeline.

---

## 1. Sửa phụ đề (editor) — where the owner spends 90 % of the time

Shots: `03b-editor-line120_1366x768.png`, `03b-editor-line120_1920x1080.png`, `07-editor-find_1366x768.png`.

| # | Problem | Fix | Effort |
|---|---|---|---|
| E1 | **The list shows 3½ lines at 1366×768 (~5 at 1080).** Each row is ~77 px: time line + VI + zh + padding (`CueDelegate`, `PAD_Y`, `GAP`). 300 lines = ~85 screenfuls at 768. Subtitle Edit / CapCut show 12–20. The reviewer keeps scrolling instead of reading. | Add a "Gọn" density (remember it): index + time + VI on **one** row (~34 px), zh as a muted second line only for the selected row (or hover). Keep the current rich row as "Rộng". | M |
| E2 | **Ctrl+F eats ~100 px** (`07-editor-find`): find field, replace field and 4 buttons stack on two rows above the filter row, leaving **2 lines** of list at 768. | One row: `[find] [replace] Tiếp Thay Thay tất cả Aa ×`, count inside the field; or float the bar over the list header. | S |
| E3 | **Reviewing the 83 flagged lines needs the mouse.** "Đã xem" has no shortcut (not in the Phím tắt grid either) and Enter does *not* mark a flagged line as seen (`_commit_next` only moves on). Editing a flagged line changes its fingerprint, so it **stays pending** until clicked → click per line, 83×. | Enter in the VI box = save + mark reviewed + go to next *pending* line when the "Cần xem lại" filter is on (next line otherwise). Add a key for "Đã xem, không sửa" (e.g. `Ctrl+Enter` is taken by approve — use `Ctrl+D` or `R`) and list it. | S |
| E4 | **No way to change line from inside the text box without the mouse.** Plain ↑/↓ belong to the text field (ShortcutOverride, by design). Enter only goes forward; there is no "previous line" from the keyboard while typing. | `Ctrl+↑ / Ctrl+↓` (free) = previous / next line from within the box; Esc = leave the box, focus the list. | S |
| E5 | **Wasted width for portrait video.** `split_sizes` is a fixed 36/38/26 %. Douyin is almost always 9:16, so at 1366 the video is ~176 px wide inside a 450 px column; at 1920 ~350 px inside 670 px. Half the video panel is black bars while the list rows are cramped. | Make the video column follow the aspect: width ≈ panel height × 9/16 + margins; give the rest to the list. (Same fix helps the render page, R3.) | M |
| E6 | **No episode-level overview on the timeline.** The strip shows ~33 s of 945 s (3.5 %). Amber "flagged" borders exist only on the visible blocks; you cannot see where the 83 flags are, or which stretch has been reviewed. | 18 px overview strip above the ruler: whole episode, amber ticks = pending, green = reviewed, a window box for the viewport; click/drag jumps. | M |
| E7 | **Timeline drag has no snapping and no neighbour limit** **(code, `Timeline.mouseMoveEvent`)**: moving/resizing a block can overlap its neighbour and nothing snaps to the playhead or the next edge; overlaps are fixed later via ⋯ → "Gộp câu quá ngắn & gỡ chồng lấn". Premiere/CapCut snap to playhead and neighbours by default. | Snap to playhead and neighbour edges within ~6 px (hold Alt to bypass); clamp resize at the neighbour (or show the overlap in red while dragging). Verify plain mouse wheel scrolls the strip sideways (code ignores it unless Ctrl is held). | M |
| E8 | **Right-click does nothing** on rows or timeline blocks (no `contextMenu` anywhere in `cue_list.py` / `timeline.py`). Split / merge / insert / delete / mark seen / dịch lại live only in toolbar buttons, ⋯ and single-letter keys. | Context menu on a row and on a block: Tách tại đây · Gộp với câu sau · Thêm câu sau · Xóa · Đã xem · Dịch lại · Rút gọn. Reuses existing signals. | S |
| E9 | **Inspector "Câu" tab: 40 % of its height is the timing block** (two time fields, 8 nudge buttons, Đặt đầu/Đặt cuối, Tự canh, Khóa timing) at 1366×768; the things after it ("Lồng tiếng câu này", the AI-review suggestion) are below the fold. The reviewer's main field (VI text) is fine; the rest competes with it. | Collapse nudges behind one "Chỉnh giờ ▸" row (timeline drag + `[` `]` + Alt+←/→ already cover it); show the AI suggestion directly under the text box. | S |
| E10 | **Duplicate zh**: the inspector repeats the same Chinese line already shown under each list row (`zh_lbl`). | Drop it when the list row shows zh (Rộng), keep it when density = Gọn (E1). | S |
| E11 | Approve dialog says "Còn 83 câu cần xem lại. Bạn vẫn có thể duyệt…" but offers only [Quay lại sửa] [Duyệt phụ đề]. | Add a third button "Xem 83 câu" → closes, turns the filter on, selects the first. | S |
| E12 | **Phím tắt dialog clips its own text** (`08-shortcuts`): "Tua lùi / tới 10 giây (giữ để tua liên t…", "Tự canh câu / tất cả câu theo…", "Câu cần xem lại sau / trước" are cut by the key caps. Missing from it: "Đã xem", Delete, add line. | Wrap the description (`wrap=True`) or widen to ~760 px; add the missing keys (after E3/E4). | S |

Smaller: the "Phụ đề đã sẵn sàng" toast covers the project-bar language chip at 1366 for several seconds **(verify duration on a real screen)**; `Tự canh tất cả` sits next to `Khớp câu` in the timeline head and is one click from rewriting 300 times (undoable, but a count in the tooltip "300 câu" would help).

---

## 2. Dựng video (render)

Shots: `05-render_1366x768.png`, `05-render_1920x1080.png`.

| # | Problem | Fix | Effort |
|---|---|---|---|
| R1 | **Mode cards draw title and description on top of each other** (all four, both resolutions): "Giữ nhạc nền & hiệu ứng" / "Bỏ giọng Trung…". `ModeCard` is a `QPushButton` with a child layout of word-wrapped `QLabel`s — a button's size hint ignores height-for-width, so the card stays ~36 px. The commit "mode cards that don't overlap" targeted this; it still shows offscreen **(verify on a real display)**. | Give the card a computed `setMinimumHeight` from the wrapped text (or paint it in `paintEvent`, or use a `QFrame` + click filter). | S |
| R2 | **Output-defining options are under the fold at 768**: only "Giọng" and "Âm thanh" are visible; "Che phụ đề Trung", "Khung hình", "Chất lượng" (with the ~223 MB estimate) need scrolling and there is no summary near the Dựng video button. | Sticky one-line summary above the buttons ("Hoài My 1.00× · giữ nhạc nền · 540×960 · Nét · ~223 MB"), and/or collapsible sections (open: Giọng, Âm thanh). | M |
| R3 | **Wrong proportions for portrait** (same cause as E5): options column ~410 px scrolling, preview column 880 px holding a 275 px-wide picture. | Preview column sized to the aspect; options column gets the rest (could show two card columns at 1920). | M |
| R4 | The speed warning is excellent ("ở 1.00× khoảng 25 % câu sẽ trễ hoặc bị cắt. Ở 1.30× còn 12 %") but is a dead end. | Link "Xem 75 câu" → editor with a "Quá dài" filter; "Dùng 1.30×" already one click. | S–M |
| R5 | Button names: "Dựng sau" is vague (it queues into the batch); "Xem nhanh" is clear. | "Thêm vào hàng dựng" (the batch-state label is already that) and keep one name for it everywhere. | S |

---

## 3. Thư viện (library)

Shots: `02-library_1366x768.png`, `02-library_1920x1080.png`.

| # | Problem | Fix | Effort |
|---|---|---|---|
| L1 | **Only name search + 3 sorts** (Mới nhất / Nặng nhất / Tên A–Z). The owner's real question is "which episodes still need review / render?" — answered only by scanning the Trạng thái column. A series (第1集…第50集) sorts by name as text. | Status chips with counts (Chờ duyệt · Đã duyệt chưa dựng · Đã dựng · Lỗi) and a "Tập" sort using the episode parser `compile` already has. Default to "Cần làm trước". | M |
| L2 | **No keyboard/double-click path** (no `keyPressEvent`, no double-click handler in library/recent list): double-click and Enter don't open, Delete doesn't offer the trash, Ctrl+A doesn't select. | Double-click/Enter = Mở, Space = Xem, Delete = Xóa… (existing confirm), Ctrl+A. | S |
| L3 | Row buttons "Mở" and "Xem" are ambiguous (edit vs play); the folder and ⋯ icons are unlabeled. | "Sửa" (opens editor) / "Xem video", tooltips on icons. Project rows with no output keep only "Sửa". | S |
| L4 | The bulk bar (Chọn tất cả · Ghép thành 1 video · Chỉ xóa file nặng · **red Xóa dự án đã chọn**) is always shown, and the destructive button looks live with nothing selected. | Show actions only when ≥ 1 selected, with "Đã chọn 3"; keep delete visually apart. | S |

---

## 4. Bắt đầu (input)

Shots: `01-input_1366x768.png`, `01-input_1920x1080.png`, `10-input-error_1366x768.png`.

| # | Problem | Fix | Effort |
|---|---|---|---|
| I1 | **Jargon / English in Vietnamese UI**: "Passthrough (no translation)", "edge-tts (vi-VN) — free · HoaiMy", "Trên máy (RapidOCR) — không gửi hình", "Tách vế theo nhịp nói", "OCR nhanh: 1 khung/giây". Dropdown values are also elided ("…không gửi hì…", "Qwen-Omni…) + timin…"). | Plain labels with the detail in the tooltip/popup ("Đọc chữ trên máy · không gửi hình đi"). Elide in the middle, never lose the verb. | S |
| I2 | When no translator key is configured the summary line quietly shows **Passthrough (no translation)** as the translator — a run would produce Chinese "translations". | If the chosen translator is passthrough, show an amber line "Chưa có khóa dịch → [Thêm khóa]" and keep Bắt đầu disabled. (The owner has keys; this bites the first run or after a key is cleared.) | S |
| I3 | The error card (`10-input-error`) is clear and actionable (cookie steps, retry), but it appears between the link box and the options and pushes the options below the fold at 768. | Keep, but collapse the options while an error card is shown, or put the card in the right column. | S |
| I4 | "Bắt đầu" is a flat grey disabled button before a link is pasted; the reason (no link) isn't stated. | Placeholder already says "https://www.douyin.com/video/…" — add a one-line "Dán link hoặc kéo thả video" hint only when disabled. Low priority. | S |

---

## 5. Cài đặt (settings)

Shots: `06-settings_1366x768.png`, `06-settings-ai_1366x768.png`, `06-settings-download_1366x768.png`.

Good: left menu with 5 groups, save/cancel footer with dirty guard on "Quay lại", model packages with cost ("Cân bằng ≈ $0.0016/phút"), key field masked with a test button. Problems:

| # | Problem | Fix | Effort |
|---|---|---|---|
| S1 | While Settings (or Library) is open the **step rail still highlights the previous step** (`_on_page` skips `set_active` for them) — shots show "Dựng video" lit on Settings. The Library nav button has an active state; "Cài đặt" does not. | Dim the rail on Settings/Library; give "Cài đặt" the same checked state as Thư viện. | S |
| S2 | The "AI và mô hình" page lists 10 role fields with raw model ids; fine for the owner, but there is no "Đặt lại theo gói" per field. | A small "↺ theo gói" on a field that was overridden. Low priority. | S |

---

## 6. Cross-app

| # | Problem | Fix | Effort |
|---|---|---|---|
| X1 | **Stale status-bar text**: "● Sửa phụ đề — duyệt khi xong" stays on Render, Settings, Library and Input (shots 05/06/10). `_on_page` resets the key hints but not the text. | `_on_page` sets a per-page default text ("Sẵn sàng", "Đã duyệt — chọn âm thanh…"). | S |
| X2 | **No app-level shortcuts at all** (no `QShortcut` in `main.py`): Ctrl+L library, Ctrl+, settings, Ctrl+1/2/3 steps, Esc = back out of Library/Settings. | Add them; show in the Phím tắt dialog under "Toàn ứng dụng". | S |
| X3 | Default window is 1380×880 (`MainWindow.__init__`, no screen check), larger than a 1366×768 screen. Pages scroll rather than grow now, so this is tolerable, but the first launch opens partly off-screen. | Clamp to `availableGeometry()` on first show. | S |

---

## Ranked top 15 (impact on the 300-line review ÷ effort)

| Rank | Item | Why | Effort |
|---|---|---|---|
| 1 | **E3** Enter/shortcut marks "Đã xem" and jumps to next pending | Removes ~83 mouse trips per episode; pure flow | S |
| 2 | **E1** Compact cue-list density | 3½ → ~10 lines per screen at 768 | M |
| 3 | **E5 / R3** Layout follows 9:16 video | Reclaims ~40 % of the width on every screen with a picture | M |
| 4 | **E6** Whole-episode overview strip with flags/reviewed | Review progress and navigation at a glance | M |
| 5 | **E2** One-row find bar | Ctrl+F no longer leaves 2 list lines at 768 | S |
| 6 | **E4** Ctrl+↑/↓ from the text box, Esc to list | Closes the keyboard loop | S |
| 7 | **E8** Right-click menu on rows and blocks | Habit from every editor; reuses signals | S |
| 8 | **E7** Timeline snap + neighbour clamp | Fewer overlap repairs afterwards | M |
| 9 | **E11** "Xem N câu" in the approve dialog | Last chance before TTS spend | S |
| 10 | **L1** Library status chips + episode sort | "What's left?" in one click across 50+ episodes | M |
| 11 | **R1** Fix overlapping mode cards | Visible defect on the render page **(verify)** | S |
| 12 | **R2** Sticky render summary / collapsible sections | The settings that decide size/quality are hidden at 768 | M |
| 13 | **X1 + S1** Stale status text, rail on Settings/Library | Small, confusing, trivial to fix | S |
| 14 | **L2** Double-click/Enter/Delete in the library | Standard behaviour, currently absent | S |
| 15 | **E12** Complete, unclipped Phím tắt dialog (+X2 app shortcuts) | Discoverability of everything above | S |

Quick wins in one sitting (all S): 1, 5, 6, 7, 9, 11, 13, 14, 15 — roughly 1.5 days and they change how a review session feels more than any single M item.

Checked OK: Enter→next flow, undo granularity (by code), autosave indicator, error card for Douyin cookies, Settings dirty guard (by code). Not covered: running-progress states (download/ASR/translate cards), batch/compile dialogs, the Kiểu chữ/Bình luận/Nhạc tabs beyond a glance, and anything needing real playback or audio.
