# Claude Design prompt — Redesign Screen 3 (Render / Export) of vizsup

> Paste the block below into the **"Dark Editor Design"** Claude Design project to redesign **Screen 3** only (keep Screens 1 & 2 as they are). It describes every control the screen must contain, the two concrete problems to fix, and the visual direction.

---

You are redesigning **Screen 3 — "Dựng video" (Render / Export)** of **vizsup**, a dark-themed desktop app (it is built in PySide6/Qt, so keep the design implementable with standard Qt widgets — no web-only effects). Screens 1 ("Nhập") and 2 ("Sửa phụ đề") already exist in this project; **match their dark theme, spacing, typography, and the top step-rail exactly**. Redesign **only Screen 3**.

This screen takes the approved Vietnamese subtitles and lets the user (1) style the burned-in subtitles, (2) choose the dubbing voice and how the audio is mixed, then (3) render the final MP4. The current build crams every control into one long, cramped vertical scroll on the left and the colours/contrast are poor. Fix the layout, hierarchy, and contrast.

## Two problems to fix (the reason for this redesign)
1. **The preview must show the subtitle rendered ON the video, in its real style and position (WYSIWYG)** — current build only shows the raw video with a plain caption bar underneath, so the user can't see their font/colour/box/position choices in context. The large preview pane (right side) must render the chosen subtitle **over the actual video frame**, at the chosen on-screen position, updating live as the user changes style controls (debounced is fine). Include a "before/after" feel: they should clearly see how the Vietnamese subtitle (and its opaque box, if used to cover the original Chinese hardsub) sits on the picture.
2. **Colour & contrast are broken / hard to read.** Design proper colour-swatch controls: a labelled row with a clear colour chip + the hex value in a readable pill (never dark text on a dark swatch). Ensure every label, slider value, and control has strong contrast on the dark background. (The OS colour-picker dialog itself is a native Qt dialog and is out of scope — just design the swatch row that opens it.)

## Layout direction
- Keep the **left control panel + large right preview** split, but make the left panel **organised and compact** instead of one long scroll. Group controls into clear **sections or two tabs**: **"Phụ đề" (Subtitle)** and **"Âm thanh & Giọng" (Audio & Voice)**. Use cards/dividers, generous-but-dense spacing, and a clear visual hierarchy so nothing feels cramped.
- The **preview pane (right)** is the hero: a large vertical-video frame (these are phone/Douyin clips, ~9:16) with the styled subtitle overlaid, plus a small transport bar (play/pause, scrub, time, volume).
- A prominent primary **"⧉ Dựng video"** button, a progress bar + collapsible log, and an **"📂 Mở thư mục"** button shown after render. A **"← Quay lại sửa phụ đề"** back link at top.

## Every control the screen must contain (design all of these)

**Mẫu kiểu (Style presets)** — a dropdown of saved presets + small **Lưu** / **Xóa** buttons.

**Phụ đề (Subtitle) section**
- **Phông chữ** (font dropdown: Be Vietnam Pro / Noto Sans / Arial)
- **Cỡ chữ** (slider 12–40 px, value shown)
- **Màu chữ** (colour swatch row — readable hex pill)
- **Màu viền** (colour swatch row)
- **Độ dày viền** (slider 0–6)
- **Kiểu nền** (dropdown: "Viền chữ (không nền)" / "Hộp mờ" / "Hộp đặc (che chữ Trung)")
- **Màu nền** (colour swatch row — for the box)
- **Đậm** / **Nghiêng** (two toggles)
- **Vị trí** — a 3×3 anchor grid (9 positions: top/middle/bottom × left/centre/right). Make it visual: a little frame with the 9 anchor points, the selected one highlighted.
- **Lề (cách mép)** (slider — fine vertical/edge offset)
- **🖼 Xem thử kiểu trên video** button — refreshes the live styled preview (note: there is a brief render; show a subtle busy state on the button).

**Âm thanh & Giọng (Audio & Voice) section**
- **Giọng** (voice dropdown, e.g. vi-VN HoaiMy / NamMinh)
- **Tốc độ nói** (slider 0.7×–1.4×, value shown)
- **Chế độ âm thanh** — a segmented/radio control: **Thay giọng gốc** / **Trộn cùng giọng gốc** / **Giảm nền tự động (duck)**
- **Âm lượng gốc** (slider 0–150%, only relevant when not "Thay")
- **Âm lượng lồng tiếng** (slider 50–150%)

**Action area**
- **⧉ Dựng video** (primary button, full width)
- Status text + **progress bar** + a small monospace **log** panel (hidden until render starts)
- **📂 Mở thư mục chứa video** (appears when done)

## Visual style
Dark, calm, professional media-tool aesthetic consistent with Screens 1 & 2: the same near-black panels, subtle borders/dividers, the violet/purple accent for primary actions and active states, monospace for numeric values (px, %, ×, timecodes), and a Vietnamese-diacritic-capable UI font throughout. Prioritise **legibility and clear grouping** over density. Show realistic Vietnamese sample text in the mockups (with full diacritics), and show the preview with a styled subtitle (e.g. an opaque box covering a Chinese hardsub) so the WYSIWYG intent is obvious.

## Deliverable
A polished mockup of Screen 3 in the project's dark theme: the reorganised left control panel (sectioned/tabbed, compact, readable colour swatches, visual 9-position grid) and the large right preview showing the subtitle styled over the video. Keep it implementable with standard Qt widgets (dropdowns, sliders, radio/segmented buttons, push buttons, a list/table, an image/video area).
