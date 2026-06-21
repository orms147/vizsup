# Claude Design prompts — vizsup Screens 2 & 3 (revised architecture)

> Decision: **all subtitle STYLING + POSITIONING moves into Screen 2** (the edit gate), so the user fixes content, timing, dub, look and on-screen position in one place. **Screen 3 becomes just voice + audio mix + render/export.** Paste each block below into the **"Dark Editor Design"** Claude Design project. Implemented in PySide6/Qt — keep designs to standard Qt widgets.

---

## PROMPT A — Screen 2 "Sửa phụ đề" (the edit gate — now the full editor)

You are redesigning **Screen 2 — "Sửa phụ đề"** of **vizsup**, a dark-themed desktop app built in PySide6/Qt (keep it implementable with standard Qt widgets — dropdowns, sliders, radios/segmented buttons, tables, an image/video area; no web-only effects). Match the existing dark theme, spacing, typography and top step-rail of the project. This is the **human review gate**: nothing is rendered until the user approves here, so this screen must let them perfect EVERYTHING about the subtitles.

This screen now owns four jobs that used to be split across screens: **(1) edit subtitle content & timing, (2) tune the Vietnamese dubbing per line, (3) style the subtitles, (4) position them on the video.** Organise it so it never feels cramped.

### Layout
- **Left = the video preview (hero), ~9:16 vertical phone clip.** It renders the Vietnamese subtitle **in its real style at its real on-screen position over the video frame (WYSIWYG)**. The subtitle is shown as a **draggable box with edge handles**: drag the **body** to move it anywhere (free placement), drag the **left/right edges** to set the text margins = the **wrap width** (narrowing the box makes a long line wrap to 2 lines; widening keeps it on 1), and optionally drag **top/bottom** to nudge the vertical bounds. Everything updates live. Below it: a transport bar (play/pause, scrub, current-time, volume). The original Chinese hardsub may still be on the frame, so show how an opaque box can sit over it.
- **Right = a tabbed control panel** with two tabs to keep it tidy:
  - **Tab "Nội dung & Lồng tiếng"**
  - **Tab "Kiểu chữ"**
- **Bottom = a timeline strip**: draggable cue blocks on a time axis; selecting/clicking a block selects the row and seeks the video. Over-long-to-dub blocks flagged.

### Tab "Nội dung & Lồng tiếng" (content + per-line dub)
- A **subtitle table**: columns `#` · `bắt đầu` · `kết thúc` · **Tiếng Việt (editable)** · `中文 (tham chiếu, chỉ đọc)`. Editing the VN text or times updates the model; double-click a row seeks the video. Rows flagged when too long to dub (amber) and when muted (🔇 icon).
- A **toolbar**: **Tách · Gộp · Thêm · Xóa** (with an undo affordance).
- A **"Câu đang chọn" panel** for the selected row:
  - **Trạng thái fit** (✓ vừa / ⚠ quá dài để lồng tiếng)
  - **Âm lượng (dB)** slider for this line · **Tắt tiếng** toggle (keeps the subtitle, drops only this line's dub)
  - **🔊 Nghe thử** (play this line's dub without rendering) · **✂ Rút gọn** (LLM shortens an over-long line to fit its time slot)
  - A read-only **line-count badge** (e.g. "đang 2 dòng") + an optional **"↵ Chèn / bỏ ngắt dòng"** button for a manual break. **Do NOT add a 1/2/3 line chooser** — line count is controlled by dragging the subtitle box width on the video (see Vị trí).

### Tab "Kiểu chữ" (subtitle style)
- **Áp dụng cho**: selector for which lines this style affects — **Toàn bộ** (all, default) or **Các câu đang chọn** (rows selected in the content table). Different lines can have different styles.
- **Mẫu kiểu (dùng lại cho dự án khác)**: presets dropdown + **Lưu** / **Xóa**. Saved styles are a **cross-project library** — apply a saved style to other video projects later.
- **Phông chữ** (dropdown), **Cỡ chữ** (12–40px slider).
- **Màu chữ** / **Màu viền** / **Màu nền** — colour-swatch rows: a label + a clear colour chip + the hex in a **readable** pill (never dark text on a dark swatch; pick a contrasting label colour). Opening the OS colour picker is a native dialog (out of scope — just design the swatch row).
- **Độ dày viền** (0–6 slider). **Kiểu nền** dropdown: *Viền chữ (không nền)* / *Hộp mờ* / *Hộp đặc (che chữ Trung)*.
- **Đậm** / **Nghiêng** toggles.
- **Giãn chữ** slider (letter spacing −20%…+40%) and **Chế độ dòng** (Tự động / 1 dòng / 2 dòng) — fit 1–2 lines by stretching/wrapping instead of just shrinking the font.
- **No "Vị trí" controls in this tab.** Positioning is done entirely by **dragging the subtitle box on the video** (left preview): drag the body to move, drag the left/right edges to set the wrap width (which drives 1 vs 2 lines), drag top/bottom for vertical bounds. Keep only the hint under the video ("Kéo phụ đề trên video để đặt vị trí").

### Visual style
Dark, calm, professional, consistent with the other screens: near-black panels, subtle borders/dividers, a violet accent for primary/active states, monospace for numeric values (px/%/dB/×/timecodes), a Vietnamese-diacritic-capable UI font. Prioritise legibility and clear grouping. Show realistic Vietnamese sample text with full diacritics; show the preview with a styled subtitle (e.g. an opaque box covering a Chinese hardsub) and the drag affordance.

### Prominent commit action
A clear **"🛡 Duyệt & Dựng video →"** button (top-right, the commit point that advances to Screen 3). Make it visually obvious this is the gate.

**Deliverable:** a polished Screen 2 mockup — left video preview with a draggable styled subtitle, a tidy two-tab right panel (content+dub / style), and the bottom timeline; readable colour swatches and a visual 3×3 position grid. Keep it implementable with standard Qt widgets.

---

## PROMPT B — Screen 3 "Dựng video" (voice + audio + render only)

You are redesigning **Screen 3 — "Dựng video" (Render / Export)** of **vizsup**, a dark-themed PySide6/Qt desktop app. Match the existing dark theme, spacing, typography and step-rail. **All subtitle styling now lives on Screen 2 — remove every style control from Screen 3.** This screen is now small and focused: pick the dubbing voice, choose how audio is mixed, render, and export.

### Layout
- A compact **left control panel** + a **large right preview** (the ~9:16 video). The preview plays the source (and, after render, the finished MP4). Keep it clean and uncramped — there are far fewer controls now.

### Controls (all of them)
- **Giọng lồng tiếng** (voice dropdown, e.g. vi-VN HoaiMy / NamMinh).
- **Tốc độ nói** (slider 0.7×–1.4×, value shown).
- **Chế độ âm thanh** — a segmented/radio control: **Thay giọng gốc** / **Trộn cùng giọng gốc** / **Giảm nền tự động (duck)**.
- **Âm lượng gốc** (slider 0–150%, relevant when not "Thay").
- **Âm lượng lồng tiếng** (slider 50–150%).
- *(Optional, read-only)* a tiny **"Kiểu phụ đề"** summary chip showing the style chosen on Screen 2 (font + a colour dot + position), with a hint that styling is edited on Screen 2 — so the user can confirm before rendering.
- **⧉ Dựng video** — primary, full-width button.
- Status text + **progress bar** + a small monospace **log** (hidden until render starts).
- **📂 Mở thư mục chứa video** (appears when done).
- **← Quay lại sửa phụ đề** back link (top).

### Visual style
Same dark, professional aesthetic as the other screens; strong contrast; monospace for numeric values; violet accent for the primary action. Calm and minimal — this is the final confirm-and-render step.

**Deliverable:** a clean Screen 3 mockup with the voice/speed controls, the audio-mode segmented control + two volume sliders, the prominent render button with progress/log, and the large video preview. Standard Qt widgets only.

---

## Notes for implementation (me, after the designs land)
- The draggable box maps cleanly to ASS **margins**, which `write_ass` already supports: dragging left/right edges sets `margin_l`/`margin_r` (libass wraps text to `PlayResX − marginL − marginR`, so the box width naturally drives 1 vs 2 lines), dragging vertically sets `margin_v` (with the alignment as anchor). No discrete line-count needed. `letter_spacing` (\fsp) and `pos` (free point-placement) also exist as options; the box/margin model is the primary mechanism.
- `style_preview_frame` burns one frame for WYSIWYG; the drag canvas re-burns on release for an exact preview.
- Moving the style panel + burn-preview + drag canvas into Screen 2; Screen 3's render call already accepts the style.
