# Design brief — vizsup (visual/UX prompt for a design tool)

> Paste the block below into Claude Design. It focuses on **product/UX requirements**
> and leaves the **aesthetic** (color, type, motion, icons) to the design agent.
> Note: Claude Design renders web/React — treat the output as a **visual reference**
> to implement in the PySide6 desktop app; the look transfers, the code doesn't.

---

Design a **desktop application** (not a website) called **vizsup** — a personal, local tool that turns a Chinese short video (Douyin / Bilibili) into the same video with **Vietnamese subtitles + a Vietnamese voiceover (lồng tiếng)**. Single user, runs locally on their own PC. **Interface language: Vietnamese** (UI labels in Vietnamese; content shows the Chinese source alongside the Vietnamese translation).

This is a focused, single-purpose **pro media / subtitle-and-dubbing studio** — a tool someone spends a focused editing session in. Design it as a native-feeling **desktop app window** (dense, keyboard-friendly, windowed), not a landing page.

**You have full creative control over the aesthetic.** Decide the color palette, light/dark (or both), typography scale, spacing system, iconography, motion/animation, transitions between steps, hover/press feedback, loading states, micro-interactions — make deliberate, tasteful choices that make a long editing session feel calm and precise. Two hard requirements only: the type must render **Vietnamese diacritics** flawlessly, and **timecodes must be easy to scan** (a tabular/monospaced treatment is encouraged, but your call).

## Core principle — design around this
There is a **human edit gate**: the user reviews and fixes the Vietnamese subtitles **after** automatic translation and **before** any voiceover or video render. Make this the visual and emotional center of the app — nothing expensive (voiceover, rendering) happens until the user explicitly approves. Design a confident "Approve & Render" commit moment.

## Flow — 3 steps with a persistent step indicator (1 → 2 → 3)

### Screen 1 — Input / Setup
- Paste a video URL (Douyin / Bilibili), or choose a local video file.
- Optionally attach an existing Chinese `.srt` (skips auto-transcription).
- Four engine pickers (dropdowns); each option shows a name + a short cost/quality note; some options appear **disabled** with a hint ("cần API key"):
  - **Nhận dạng giọng nói (ASR):** "WhisperX + FunASR — local · chính xác tiếng Trung nhất", "faster-whisper — local · nhanh hơn"
  - **Dịch (bản nháp):** "DeepSeek-V4 Flash — ~$0.14 · mạnh tiếng Trung", "GLM-5.2 — open weights", "Qwen3 — rẻ"
  - **Dịch (tinh chỉnh, tùy chọn):** "Claude Sonnet 4.6 — tiếng Việt tự nhiên nhất", "Gemini 2.5 Flash", "— không dùng —"
  - **Giọng lồng tiếng (TTS):** "edge-tts (vi-VN) — miễn phí · HoaiMy / NamMinh", "FPT.AI — trả phí", "Azure vi-VN"
- A primary **"Bắt đầu"** action.
- A processing state: a progress indicator stepping through stages — **Tải video → Dò phụ đề cháy → Nhận dạng / Trích phụ đề → Dịch** — with a live log and a **Hủy** (Cancel). Design idle, in-progress, and per-stage states.

### Screen 2 — Subtitle Editor (THE GATE — design in the most detail)
- A **video player** (the source clip): play/pause, scrub bar, current-time readout.
- A **waveform** of the audio beneath the video, playhead synced to the video.
- A subtitle **timeline**: draggable cue blocks on a time axis aligned to the waveform — drag edges to retime, click to select + seek.
- A subtitle **table** synced to the timeline: columns `#` | `bắt đầu` | `kết thúc` | **Tiếng Việt (sửa được)** | `中文 (chỉ đọc, để đối chiếu)`. Inline-edit the Vietnamese text and the in/out times; selecting a row highlights its block and seeks the video.
- Editing tools: **tách dòng** (split), **gộp dòng** (merge), **thêm / xóa dòng**, and a subtle warning on lines whose Vietnamese is too long to be dubbed within its time slot.
- A prominent, reassuring **"Duyệt & Dựng video →"** (Approve & Render) button.
- Use the realistic sample data below so the mockup feels real.

### Screen 3 — Render / Export
- Subtitle styling: font, size, and a toggle **"Che phụ đề tiếng Trung gốc (khung mờ)"**.
- Voiceover **voice picker** (from the chosen engine) + optional speed.
- Audio handling (radio): **thay giọng gốc / trộn cùng giọng gốc / (giảm nền — sau này)**.
- A **"Dựng video"** (Render) action with progress.
- A **done** state: a preview of the finished video + buttons to open the output folder and to grab the exported `.srt` / `.ass`. Design the rendering and done states.

## Global
- A **Settings** area: API keys per engine (DeepSeek, Anthropic/Claude, GLM/Zhipu, Gemini, Qwen, DeepL, FPT, Azure) + a default engine per step.
- States to design on every screen: **empty/idle, loading/processing, success, error** (e.g. "Tải Douyin lỗi — cần cookie mới", "Thiếu ffmpeg"). Make errors calm and recoverable, not alarming.

## Sample content (use in the editor mockup)
1. `00:00.0 – 00:02.4` · 你好，欢迎来到我的频道 → "Xin chào, chào mừng đến với kênh của mình"
2. `00:02.4 – 00:05.0` · 今天教大家做一道家常菜 → "Hôm nay mình hướng dẫn mọi người làm một món ăn nhà"
3. `00:05.0 – 00:08.2` · 首先准备一些新鲜的蔬菜 → "Đầu tiên, chuẩn bị một ít rau củ tươi"
4. `00:08.2 – 00:11.0` · 记得点赞和关注哦 → "Nhớ like và theo dõi nha"

## Deliverable
- A small **foundations / design system**: colors, type, spacing, and the core components (button, dropdown, table row, timeline block, progress, tag/badge, dialog).
- The **three screens in high fidelity**, the editor screen most detailed, plus the key states above.
- **Motion**: propose tasteful transitions between the 3 steps and micro-interactions in the editor — your call.
- Present it as a **desktop app** (windowed, dense, keyboard-friendly), in **Vietnamese**.
