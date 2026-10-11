# vizsup — Redesign brief (desktop app, PySide6)

> Paste everything below the line into the design agent. Current-state screenshots to
> attach: `appdesign/screenshots/current/1-input.png`, `2-editor.png`, `3-render.png`,
> `4-settings.png` (1600×900). The previous visual design lives in `appdesign/*.dc.html`
> and the claude.ai/design project "Dark Editor Design".
>
> Updated 2026-10-01. Earlier versions of this file targeted a React web UI — that is
> parked; the product is the PySide6 desktop app.

---

You are redesigning **vizsup**, a personal **Windows desktop app** that turns a Chinese short
video (Douyin / Bilibili) into a Vietnamese version: Vietnamese subtitles **and** a Vietnamese
voice-over. One user, Vietnamese-speaking, using it many times a week. The current UI works but
the user's verdict is blunt: **the UX is bad and editing the subtitles against the video is
uncomfortable.** Your job is to make the editing step fast and pleasant, and to make every other
step need as few decisions as possible.

## 0. How your design will be built (read first)

- The app is **PySide6 / Qt Widgets** on Windows, hand-built from your design. You may output
  HTML/React mockups, but **everything must be buildable with Qt Widgets**: buttons, combo boxes,
  sliders, tabs, splitters, tables/lists, line/text edits, checkboxes, radio/segmented buttons,
  menus, dialogs, tooltips, plus two custom-painted surfaces that already exist (the **timeline**
  and the **video canvas** with a draggable subtitle box). Avoid web-only effects: backdrop blur,
  heavy CSS animation, glassmorphism, complex gradients, hover-only affordances with no fallback.
- **All UI copy is Vietnamese.** Diacritics must render perfectly — UI font **Be Vietnam Pro**,
  mono **JetBrains Mono**; the Chinese reference text needs a CJK fallback (Microsoft YaHei /
  Noto Sans SC). Never put a bare `&` in a label (Qt renders it as an underscore shortcut).
- **Dark theme**, existing accent purple `#8b5cf6`. You may refine the palette; keep it calm and
  dense, not flashy. Contrast must stay readable for long editing sessions.
- **Screens:** must work at **1366×768** with every primary action visible without scrolling, and
  scale up gracefully to 1920×1080 / 2560×1440.
- Videos are mostly **vertical 9:16** (Douyin), sometimes **16:9** (Bilibili); 15 s to ~10 min,
  roughly 20–300 subtitle lines.

## 1. The flow (hard constraints — do not change)

`Nhập link → tải video → lấy phụ đề tiếng Trung (OCR chữ cháy hoặc AI nghe audio) → dịch sang
tiếng Việt → ⛔ BẠN DUYỆT & SỬA → lồng tiếng (TTS) → dựng video MP4`

1. **The human edit gate is sacred.** Nothing is voiced or rendered until the user approves the
   subtitles. The editor is the heart of the app; ~80 % of the user's time is spent there.
2. **Engines are user-selectable per stage** (speech recognition, translation draft + optional
   refine, text-to-speech). Each option has a display name, a cost/notes line, and may be
   unavailable (missing key) — show why, never hide it.
3. **Long stages run in the background and must never look frozen.** Typical times for a 5-minute
   video: download 2–10 s, speech recognition 60–90 s, translation 30–60 s, voice-over 30–120 s,
   render 20–60 s. Every stage reports progress lines (e.g. "AI đã nghe xong đoạn 1/2", "dịch lô
   2/4", "TTS 37/120"), and every run has **Hủy** (cancel).
4. Work is **resumable**: a project re-opened later skips finished stages. Edits **autosave**.

## 2. What is wrong today (from the screenshots + usage)

**Editor (`2-editor.png`) — the main complaint**
- The video is a ~300 px wide strip on the left; the table takes the rest. For editing *against*
  the video, the video and the current line must be the visual focus.
- The table is a spreadsheet: tall rows, a wide empty Chinese column, no sense of "where am I in
  the video". The current line does not follow playback.
- The toolbar is a row of ten same-looking small buttons (Tất cả, Bỏ tích, Tách, Gộp, Thêm, Xóa,
  Tìm, Thuật ngữ…) with no visible keyboard shortcuts.
- **Timing is hard to fix.** Speech recognition on music/songs can give a whole sentence a 0.3 s
  slot (see line 4: 00:39.3–00:39.6). Today the only fix is dragging block edges on the timeline
  or typing times. Cloud speech recognition is off by up to ~2 s on 5 % of lines.
- The timeline at the bottom is wide but shallow; cue blocks show truncated text; zoom buttons are
  tiny. The waveform is synthetic (a real one is planned).
- No way to see *which lines need attention* (too long to dub, glossary term missing, suspicious
  OCR) other than scanning row colors.
- Per-line dub controls (volume, mute, "Nghe thử", "Rút gọn") sit in a strip far from the line.

**Start screen (`1-input.png`)**
- A long scrolling form: recent projects, URL, four radio options, OCR region, three checkboxes,
  a context note, then engine pickers — and the **Bắt đầu** button is below the fold at 900 px.
- First-time Douyin use fails on **cookies** (Douyin refuses downloads without them); the fix
  (export a cookies.txt with a browser extension and pick it in Settings) is explained only in an
  error message.

**Render (`3-render.png`)** is fine structurally but feels empty; no result actions after render.

**Settings (`4-settings.png`)** is a long list of key fields followed by unrelated options.

## 3. Screens to design

### A. "Bắt đầu" — new project + recent projects
- **Hero:** one big field "Dán link Douyin / Bilibili hoặc kéo thả video" + **Bắt đầu**. Pressing
  Enter starts with the saved defaults. Nothing else is required.
- **Recent projects** as compact cards/rows: thumbnail, title, duration, status (đang chạy / chờ
  duyệt / đã dựng / lỗi), last opened; click to resume; ⋯ menu to delete / open folder.
- **"Tùy chọn" (collapsed by default, remembers its state):**
  - Nguồn phụ đề tiếng Trung: Tự động (khuyên dùng) · AI nghe audio · Đọc chữ cháy (OCR) · File .srt.
    OCR region only appears when OCR/Tự động is chosen.
  - Bộ dịch (bản nháp) + Tinh chỉnh (tùy chọn) · Nhận dạng giọng nói · Giọng lồng tiếng.
  - Toggles: Tự đọc ngữ cảnh video (on) · Tự trích thuật ngữ · AI sửa lỗi OCR.
- **Ngữ cảnh video (ghi chú):** a short free-text note for THIS video (who is talking to whom,
  how to address people, names). The AI expands it into a brief that steers translation; the
  user's facts always win. Keep it one click away from the hero — it is per-video and optional.
- **Running state:** the same screen turns into a progress view — a vertical stage list
  (Tải video · Lấy phụ đề · Đọc ngữ cảnh · Dịch) with the active stage animated, its live sub-line,
  elapsed time, an expandable raw log, and **Hủy**. When translation ends, go to the editor
  automatically.
- **Error state:** errors are actionable cards, not log lines. Design the **cookie** case
  explicitly: "Douyin cần cookie" → three numbered steps (install "Get cookies.txt LOCALLY",
  open douyin.com, Export) + a **Chọn tệp cookies.txt** button that fixes it inline + **Thử lại**.
  Also: missing API key → "Mở Cài đặt", network error → "Thử lại".

### B. "Sửa phụ đề" — the editor (most important; spend most effort here)
Goal: watch, read, fix, move on — mostly from the keyboard, with the video always in view.

**Layout (propose 1–2 alternatives, mark one recommended):**
- The **video is large** and the subtitle is drawn on it in its real style and position
  (WYSIWYG). The subtitle box can be dragged (position) and its sides dragged (wrap width) — keep
  this, it already exists.
- A **line list** that is denser than today: each row shows time range, the Vietnamese line (edited
  in place), the Chinese reference smaller/dimmer under or beside it, and small status chips.
  The **active line follows playback** (auto-scroll, highlight) and clicking a line seeks.
- A **timeline** with a real waveform, readable cue blocks, a playhead, and easy zoom
  (Ctrl+scroll, a zoom slider, "fit selection"). Dragging block edges retimes; dragging the body
  moves.
- A **"Câu đang chọn" inspector** close to the line: start/end with **nudge buttons (−200 / −50 /
  +50 / +200 ms)**, **Đặt đầu = playhead** / **Đặt cuối = playhead**, a **khóa** (lock timing)
  toggle, reading-speed / fit indicator ("vừa" / "quá dài để lồng tiếng"), dub volume + mute,
  **🔊 Nghe thử** (hear this line's voice-over), **✂ Rút gọn** (AI shortens to fit), and line-break
  control.
- **Kiểu chữ** (style) stays a separate tab or panel: font, size, colors, outline/box, position
  presets, "Áp dụng cho: Toàn bộ / Các câu đang chọn", **Xem thử kiểu** (exact rendered frame).

**Must-have interactions (show them in the design, with shortcut hints in tooltips/menus):**
| Phím | Hành động |
|---|---|
| Space | Phát / dừng |
| Ctrl+Space | Phát lại câu đang chọn (loop toggle) |
| ↑ / ↓ hoặc Tab / Shift+Tab | Câu trước / sau (seek theo) |
| Enter | Sửa chữ câu đang chọn · Enter lần nữa = lưu + sang câu sau |
| [ / ] | Đặt đầu / cuối câu = vị trí phát |
| Alt+← / Alt+→ | Lùi / tiến đầu câu 50 ms (Shift = 200 ms); Ctrl+Alt cho cuối câu |
| S | Tách câu tại vị trí phát |
| M | Gộp với câu sau |
| Ctrl+Z / Ctrl+Y | Hoàn tác / làm lại (đã có, phủ mọi chỉnh sửa) |
| Ctrl+F | Tìm / thay trong tiếng Việt (đã có) |
| Ctrl+Enter | Duyệt & dựng video |

**"Cần xem lại" navigator:** a filter/badge showing counts of lines that need attention and
**F8 / Shift+F8** to jump between them: quá dài để lồng tiếng · thiếu thuật ngữ (glossary) · chữ
OCR đáng ngờ · timing quá ngắn (< 0.8 s) or overlapping. A "Chỉ hiện câu cần xem lại" toggle.

**Context & terms:** show the AI's **brief ngữ cảnh** for this video (read-only, collapsible) and
the **📖 Thuật ngữ** (glossary) entry point; the user should understand *why* the translation
addresses people the way it does.

**Approve:** a single prominent **Duyệt & dựng video** action, plus a visible autosave indicator
("Đã lưu"). Approving must feel deliberate (it spends voice-over credits), but not nag.

### C. "Dựng video" — voice-over + render + result
- Giọng (voice) with **Nghe thử**, tốc độ nói; Âm thanh: Thay giọng gốc · Trộn · Giảm nền (duck)
  · Giữ nhạc nền (tách lời bằng AI); âm lượng gốc / lồng tiếng; Che phụ đề Trung gốc: không ·
  hộp đen · làm mờ.
- **Dựng video** → progress (TTS i/N, then render %) with **Hủy**.
- **Result state:** play the output, **Mở thư mục**, **Xuất .srt / .ass**, **Quay lại sửa** (re-render
  only re-voices changed lines — make that clear), file size/duration.

### D. "Cài đặt" — grouped, short
Sections (tabs or a left nav): **Khóa API** (OpenRouter first; per key: masked field, 👁, **Test
kết nối** with inline result) · **Tải video** (**tệp cookies.txt** with picker, status "còn hiệu
lực / có thể đã hết hạn", how-to link; BBDown path) · **AI** (model nghe audio, model dịch, a
"model khuyến mãi" with its **expiry date** and what it falls back to) · **Đường dẫn** (thư mục dự
án, ffmpeg). Show a one-line privacy note where audio is uploaded ("Âm thanh được gửi lên máy chủ
AI khi dùng 'AI nghe audio'").

## 4. States to design for every screen
Empty / first run (no API key, no cookies) · running (each stage) · error (actionable) · success.
Plus the editor with: 20 lines, 300 lines, a line selected, multi-select, a line too long, a line
with a glossary miss, search bar open.

## 5. Data the UI can rely on (so the design is buildable)
- **Engine option:** `name`, `display_name`, `cost_note` (one short line), `available` (bool),
  `unavailable_note`, `local` (bool); translation options also `can_refine`, `can_proofread`.
- **Subtitle line (editor):** `start`, `end` (seconds), `vi` (editable), `zh` (reference),
  `gain_db`, `mute`, `style` (optional per-line override). Line identity is its position.
- **Per-project files:** Chinese + Vietnamese subtitles, OCR confidence report (suspect lines),
  glossary compliance report (lines missing a term), context brief text, detected hardsub band,
  style + per-line style overrides, TTS cache (only changed lines re-voiced).
- **Progress events:** a stage name, a 0–1 fraction, and human-readable Vietnamese log lines.

## 6. Out of scope
Web/browser UI, multi-user, voice cloning, Kuaishou/Xiaohongshu download, mobile layouts.

## 7. What to deliver
Your design is the **source of truth for a rewrite of the Qt UI**: an engineer will port it
screen by screen into PySide6 and change the app's code to match it. So:
- Save the screens as **new files in this design project** (e.g. `v2 — Bắt đầu`, `v2 — Sửa phụ đề`,
  `v2 — Dựng video`, `v2 — Cài đặt`); do not overwrite the old v1 files — they are the reference
  for what exists today.
- Use real Vietnamese copy and realistic data (a 9:16 clip, ~40 lines, a few flagged lines), not
  lorem ipsum — the port copies text and spacing from your files.
- Name things consistently (one name per component, per state) so the port can map each to one
  Qt widget.

1. The four screens (A–D) at 1366×768 and 1920×1080, in their key states (§4).
2. The editor in detail: layout + the inspector + the timeline + the "cần xem lại" navigator,
   with the keyboard map visible somewhere in the UI (e.g. a "?" overlay).
3. A short component sheet: buttons (primary / secondary / ghost / danger), inputs, chips, list
   rows, progress, toasts/error cards — with spacing, radii, type sizes, colors as tokens.
4. Notes on behavior where a picture is not enough (what follows playback, what autosaves, where
   focus goes after each action).
