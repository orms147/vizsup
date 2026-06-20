# vizsup — Đề xuất tính năng (tổng hợp research)

> Bản tổng hợp 4 đợt research (đối thủ/UX · engine&model 2026 · khoảng-trống-code · tương tác giọng lồng tiếng) + 4 yêu cầu trực tiếp của người dùng. Mục tiêu: một danh sách **nên làm + khả thi** trên máy cá nhân Windows, RTX 4070 **8GB VRAM**, có ưu tiên và chỉ rõ chỗ cắm trong code.
>
> Tài liệu liên quan: [research.md](research.md) · [architecture.md](architecture.md) · [providers.md](providers.md)

---

## 0. Trạng thái hiện tại

**Đã chạy thông end-to-end:** download (yt-dlp) → OCR (RapidOCR + hậu xử lý lọc tin cậy/gộp gần-trùng) **hoặc** ASR (faster-whisper, có CPU-fallback) → dịch (OpenRouter/qwen-plus, 2-pass được) → **cổng sửa tay** (video + bảng + timeline) → TTS (edge-tts vi-VN) → ghép (ffmpeg: dub theo timeline tuyệt đối + cháy `.ass`) → `output.mp4`. Dấu tiếng Việt cháy đúng (Be Vietnam Pro).

**Đã sửa/cải tiến trong các phiên gần đây:**
- Vá crash OCR (opencv-python → headless) · vá CUDA-fallback cho ASR · hậu xử lý OCR (107→86 dòng, hết rác Latin) · cảnh báo câu bị atempo cắt cụt · pre-flight kiểm tra bộ dịch (chặn "tiếng Trung giả vờ đã dịch") · sửa bug Split · **bước 3 hiện thanh phụ đề VI trong xem trước**.

---

## 1. Bốn yêu cầu của người dùng → kế hoạch

| # | Yêu cầu | Trạng thái | Thuộc trọng tâm |
|---|---|---|---|
| 1 | **Tăng/giảm âm lượng trong editor (bước 2)** — để tự tinh chỉnh khi tạo video; âm lượng gốc ở bước 3 tùy chọn | Chưa có | → **Trọng tâm B** |
| 2 | **Bước 3 không hiện phụ đề** | ✅ **Đã sửa** (truyền `editor.cues` vào preview → thanh caption VI hiện dưới video) | Xong |
| 3 | **Tùy chỉnh phụ đề: vị trí, khung nền, cỡ, font, màu, shape…** (bước 2 hoặc 3) | Chưa có (mới có font/cỡ/che-hardsub) | → **Trọng tâm A** ⭐ |
| 4 | **Giọng lồng tiếng đè 2 câu, không chỉnh được** | Chưa có (đây là **lỗi thiết kế** của bước ghép) | → **Trọng tâm B** ⭐ |

---

## 2. Trọng tâm A ⭐ — Tùy chỉnh kiểu phụ đề (full styling)

### Hiện trạng (code)
[srt.py `write_ass`](../backend/app/pipeline/srt.py) chỉ tham số hóa **font, cỡ chữ, margin_v, che-hardsub (BorderStyle 4/1)**. Mọi thứ còn lại **hardcode** trong `Style: Default`:
- màu chữ trắng `&H00FFFFFF`, viền đen, **hộp nền** `&H80000000` (đen mờ), **căn lề 2** (giữa-dưới), đậm=0, viền=2, đổ bóng=0.
[render_view.py](../desktop/views/render_view.py) chỉ phơi ra: font (3 lựa chọn), cỡ (12–32px), checkbox che-hardsub.

### Đề xuất
**A1. Mở rộng `write_ass`** thành style đầy đủ tham số (ASS hỗ trợ sẵn, chỉ cần truyền vào):

| Tùy chỉnh | Trường ASS | UI gợi ý |
|---|---|---|
| Màu chữ | `PrimaryColour` | color picker (RGB→`&HAABBGGRR`) |
| Màu viền | `OutlineColour` | color picker |
| Độ dày viền | `Outline` | slider 0–6 |
| Đổ bóng | `Shadow` | slider 0–4 |
| **Khung nền (shape)** | `BorderStyle` 1 (viền+bóng) / 3 (hộp khít chữ) / 4 (hộp mờ che-hardsub) + `BackColour`+alpha | dropdown "Kiểu nền" + color/alpha |
| Đậm / nghiêng | `Bold` / `Italic` | toggle |
| **Vị trí (9 ô)** | `Alignment` 1–9 (numpad) | lưới 3×3 chọn neo |
| Tinh chỉnh vị trí | `MarginL/R/V` | sliders |
| Giãn ngang/dọc | `ScaleX/ScaleY` | (nâng cao) |

> "Shape" bo góc: libass không bo góc hộp gốc; nếu cần bo góc thật phải vẽ bằng `\p` drawing per-cue (phức tạp, để sau). 3 kiểu nền trên đủ 95% nhu cầu.

**A2. Panel "Kiểu phụ đề" + xem trước trực tiếp.** Đặt ở **bước 3** (hoặc panel mở từ bước 2). Live preview chuẩn nhất = **cháy thử .ass lên 1 khung hình** bằng ffmpeg (1 frame, &lt;0.3s) rồi hiển thị — thấy đúng y kết quả cuối (vị trí, màu, hộp, font). Rẻ hơn render cả video.

**A3. Presets kiểu phụ đề** — lưu/nạp bộ style (JSON) để khỏi chỉnh lại mỗi lần (vd "Vlog", "Phim", "Che-hardsub").

**A4. (Nâng cao) Kéo-thả vị trí** — kéo phụ đề trên khung preview → ghi `\pos()` override. Để sau A1–A3.

| Mục | Chỗ cắm | Công sức |
|---|---|---|
| A1 mở rộng write_ass | [srt.py](../backend/app/pipeline/srt.py) `write_ass` + [assemble.py](../backend/app/pipeline/assemble.py) truyền tham số | **S–M** |
| A2 panel + live preview (ffmpeg 1 frame) | [render_view.py](../desktop/views/render_view.py) + helper cháy-frame | **M** |
| A3 presets | render_view + JSON | **S** |
| A4 kéo-thả `\pos()` | video_panel overlay | **L** |

---

## 3. Trọng tâm B ⭐ — Lồng tiếng: hết đè nhau + chỉnh được

### Nguyên nhân "giọng đè 2 câu" (đã truy ra trong code)
[assemble.py](../backend/app/pipeline/assemble.py) đặt mỗi đoạn VI tại `cue.start` (`adelay`) và `amix` tất cả, nhưng tính tốc độ theo `slot = cue.duration` (**độ dài của riêng câu đó**), **không xét khoảng cách tới câu kế** và **không biết câu kế làm gì**. Khi 1 đoạn dài hơn `cue.duration` mà 1.3× vẫn không đủ, audio tràn qua `cue.end` → chồng lên đoạn kế → **2 giọng cùng phát**. Hiện không có cơ chế chống đè và không có chỉnh per-line.

### B1. Chính sách "fit" mặc định mới (làm đè trở nên **bất khả thi về cấu trúc**)
Đổi tư duy: **span khả dụng = `next_cue.start − cue.start`** (gồm cả khoảng lặng giữa câu), **không phải** `cue.duration`. Với mỗi đoạn, theo thứ tự, dừng ở bước đầu tiên vừa khít:

1. **Đặt nguyên** nếu `seg_dur ≤ span − carry` và không đè `prev_audio_end`.
2. **Tăng tốc thoải mái** `tempo = min(seg_dur/span, 1.15)` bằng **rubberband** (fallback atempo). Im lặng.
3. **Tăng tốc trần** `tempo = min(seg_dur/span, 1.30)` → cảnh báo hổ phách "đọc nhanh".
4. **LLM rút gọn câu** (nếu bật) → đọc lại, thử lại từ bước 2. *(Hoặc nút "rút gọn" cho người dùng tại cổng.)*
5. **Dịch chuyển cục bộ có giới hạn** — đẩy câu kế trễ đi đúng phần tràn, nhưng **kẹp tổng `carry` ≤ ~1.5s** và **reset `carry`=0 ở khoảng lặng ≥ ~0.7s** (re-anchor kiểu KrillinAI → **không trôi tích lũy**).
6. **Fade cắt đuôi** ≤ 0.4s (`afade=out`) như van an toàn cuối — thay cho cắt cụt thô hiện tại.
7. **Gắn cờ đỏ** "quá dài — rút gọn câu này". **Không bao giờ đè im lặng, không trôi vô hạn.**

> Mặc định (đồng thuận KrillinAI/VideoLingo): `accept=1.15`, `max=1.30`, `gap_reset=0.7s`, `max_carry=1.5s`, `trim_budget=0.4s`, ưu tiên rubberband. Đây là sửa tập trung trong `assemble.py`: thay vòng đặt từng cue độc lập bằng **một lượt tuần tự** tính start mỗi đoạn = `max(cue.start, prev_audio_end)` theo luật carry/gap-reset.

### B2. Chỉnh per-line trong editor (bước 2) — bộ tối thiểu
| Điều khiển | Tác dụng | Ưu tiên |
|---|---|---|
| **Cờ "fit" + nút rút gọn** | xanh/hổ phách/đỏ theo tỉ lệ vừa khít; 1 click LLM rút gọn | **Phải có** (sửa gốc đè) |
| **Âm lượng per-line (dB)** | chỉnh to/nhỏ từng câu | **Phải có** (đúng yêu cầu #1) |
| **Đọc lại 1 câu** | tổng hợp lại TTS riêng câu đó | **Phải có** |
| **Tắt/bỏ 1 câu** | bỏ dub câu này (giữ phụ đề) | Nên có |
| **Nudge thời gian ± ms** | dời thủ công khi auto đoán sai | Nên có |
| **Khóa timing 1 câu** | loại khỏi auto-shift | Nên có |

Lưu dưới dạng dict override per-cue (`gain_db`, `speed`, `offset_ms`, `mute`) cạnh `vi.srt`; `assemble.py` đọc vào.

### B3. Âm thanh gốc (toàn cục)
| Chế độ | Khi nào dùng | ffmpeg |
|---|---|---|
| **Thay** (hiện có) | gốc chỉ là giọng nói, bỏ được | map `[voa]` |
| **Trộn tĩnh** | nhanh, có lẫn chút nền | `[0:a]volume=0.4`+`amix` |
| **Duck động** ⭐ *(mặc định khi giữ gốc)* | có nhạc/SFX đáng giữ → nền **chỉ nhỏ lại khi có giọng dub** | `sidechaincompress` |
| **Tách stem (Demucs/BS-RoFormer)** | chất cao nhất, không lẫn giọng gốc | tách → `amix` (chạy tuần tự, 8GB) |

Thêm **slider âm lượng gốc** (0–100%, 0 = thay hẳn) + **âm lượng dub tổng** ở bước 2. Đây chính là yêu cầu #1 phần "âm lượng gốc bước 3 tùy chọn".

### B4. Nghe thử dub trong editor — không cần render
1. **Nút "nghe" per-cue** — phát `tts/seg_N.wav` đã có sẵn (QMediaPlayer thứ 2). **S**
2. **Slider âm lượng live** — `QAudioOutput.setVolume()`, nghe đổi ngay. **S**
3. **Nghe trong ngữ cảnh** — seek video tới `cue.start` + phát dub đè lên audio gốc (đã hạ volume). **S–M**
4. **Nghe thử 1 đoạn cửa sổ** — render audio-only ~10–20s với đúng filter (duck/shift) ra wav tạm để nghe thật. **M**

| Mục | Chỗ cắm | Công sức |
|---|---|---|
| B1 chính sách fit mới (hết đè) | [assemble.py](../backend/app/pipeline/assemble.py) | **M** |
| B2 per-line controls + overrides | [editor_view.py](../desktop/views/editor_view.py), [subtitle_table.py](../desktop/widgets/subtitle_table.py), cuemodel | **M** |
| B3 duck + slider âm lượng gốc/dub | [assemble.py](../backend/app/pipeline/assemble.py) + [render_view.py](../desktop/views/render_view.py)/editor | **S–M** |
| B4 nghe thử per-cue + live volume | [video_panel.py](../desktop/widgets/video_panel.py)/editor | **S–M** |

---

## 4. Lộ trình hợp nhất (từ 3 research trước)

### 🥇 Quick win (S) — value/effort cao nhất
| Tính năng | Chỗ cắm | Ghi chú |
|---|---|---|
| **Nâng RapidOCR** `>=3.8` + PP-OCRv5 + `onnxruntime-gpu` | [ocr.py](../backend/app/pipeline/ocr.py) + pyproject | +2–5% chính xác Hán + tăng tốc GPU, 0 đau paddlepaddle. *Value/effort cao nhất.* |
| **atempo → rubberband** (fallback atempo) | [assemble.py](../backend/app/pipeline/assemble.py) | giọng rõ/ấm hơn; trùng với B1 |
| **2-pass dịch** DeepSeek-V4-Flash → Gemini 2.5 Flash | providers có sẵn | nuance CN→VI tốt hơn 1 lượt; Gemini Flash $0.30/$2.50 (đã verify) |
| **Sửa nhãn `Qwen3`→`Qwen3.5`** | [qwen.py](../backend/app/providers/translation/qwen.py) | đời hiện tại |

### 🥈 Nâng chất lượng (M)
- **Tự nhận diện hardsub** (auto chọn OCR/ASR) — hoàn thiện [hardsub_detect.py](../backend/app/pipeline/hardsub_detect.py) (đang stub); `Job.has_hardsubs` sẵn.
- **FunASR Paraformer (bản dịch) + WhisperX (timing)** — ~2× chính xác Hán so large-v3, Apache-2.0, CPU được.
- **Trích thuật ngữ cả video + 2-pass dịch-phản tư + ngữ cảnh ±vài dòng** (VideoLingo).
- **LLM rút gọn câu cho vừa khe + ước lượng độ dài TTS trước** (KrillinAI) — trùng B1/B2.
- **OCR: thêm SSIM frame-dedup + `max_merge_gap`** (mở rộng cái đã làm) + **LLM-vision proofread** (cloud-first, chỉ soát câu tin cậy thấp).
- **VieNeu-TTS** provider tùy chọn (Apache-2.0, biểu cảm hơn edge-tts, chạy CPU).
- **Glossary UI** (tham số đã xuyên suốt, chỉ thiếu UI).

### 🥉 Cược lớn (L)
Xóa hardsub bằng inpainting (STTN/LaMa, chỉ dải phụ đề → bỏ hộp che) · FireRedASR-AED-L (trần chính xác Hán ~3×, cần VAD chunk) · caption karaoke highlight từng từ · batch queue dừng ở từng cổng.

### 🚫 Giữ nguyên / Bỏ qua
Giữ **edge-tts** mặc định (miễn phí + timestamp từng từ — không đối thủ cho việc này). Bỏ: large-v3-turbo (sai đòn bẩy cho Hán), SenseVoice làm nguồn chính (timestamp yếu + weight non-OSS), FireRedASR-LLM-L (8.3B không vừa 8GB), PP-OCRv6 native (đau install Windows), LLM dịch SEA/VI chuyên biệt (cũ/niche), Spleeter, voice cloning (ngoài v1), diarization đa giọng.

### ⚠️ Cần verify trước khi tin (luật CLAUDE.md)
Web bị chặn 1 phần khi research → **chưa chắc**: id+giá API **DeepSeek V4** (+ ngày deprecate 2026-07-24), id+giá **GLM-5.2**, giá **Qwen3.5-Plus**, slug **OpenRouter**, giá TTS cloud (Azure/Google/FPT). *(Model có tồn tại trên HF — chỉ id-string/giá chưa verify.)* Giá **Claude & Gemini đã verify.**

---

## 5. Khuyến nghị thứ tự build

**Sprint 1 — đúng cái bạn cần nhất (Trọng tâm A+B lõi):**
1. **B1** chính sách fit mới trong `assemble.py` → **hết đè giọng** (gốc rễ của #4).
2. **B3** slider âm lượng gốc/dub + duck động → chỉnh được âm lượng (#1).
3. **A1+A2** mở rộng `write_ass` + panel kiểu phụ đề + live preview 1-frame → tùy chỉnh vị trí/màu/khung/cỡ/font (#3).

**Sprint 2 — chỉnh tinh per-line + nghe thử:**
4. **B2** per-line gain/đọc-lại/rút-gọn/mute + overrides.
5. **B4** nghe thử dub trong editor (không render).
6. **A3** presets kiểu phụ đề.

**Sprint 3 — chất lượng nền (Tầng 1 hợp nhất):**
7. Nâng RapidOCR PP-OCRv5+GPU · rubberband (nếu chưa làm ở B1) · 2-pass DeepSeek→Gemini · sửa nhãn Qwen3.5.

**Sau đó:** tự nhận diện hardsub · FunASR+WhisperX · glossary UI · (cược lớn) xóa hardsub inpainting.

---

## Phụ lục — nguồn chính

- **Đối thủ/UX & dub-fit:** VideoLingo, KrillinAI/KlicStudio, pyVideoTrans (đọc source), Subtitle Edit, Aegisub, CapCut/Submagic (khái niệm).
- **Engine/model:** FireRedASR, WhisperX, FunASR Paraformer, SenseVoice (ASR); edge-tts, VieNeu-TTS, viXTTS, F5-TTS (TTS); DeepSeek V4, GLM-5.2, Qwen3.5, Claude, Gemini (dịch); PaddleOCR PP-OCRv5/v6, RapidOCR, VideOCR (OCR); Demucs, BS-RoFormer/audio-separator, rubberband (audio).
- **Tương tác giọng:** KrillinAI `dubbing/{estimator,optimizer,fit,planner}.go`; pyVideoTrans `task/_rate.py`; VideoLingo `_8_2_dub_chunks/_10/_11`; ElevenLabs Dubbing; Premiere Auto-Ducking; Resolve Fairlight Ducker; ffmpeg `sidechaincompress`/`rubberband`/`afade` (verify trên 8.1.1).
