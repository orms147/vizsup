# Khảo sát đối thủ → đề xuất tính năng cho vizsup (2026-10-29)

Phạm vi: CapCut desktop, Adobe Premiere Pro, DaVinci Resolve, Descript, Subtitle Edit / Aegisub, công cụ lồng tiếng AI
(ElevenLabs Dubbing Studio, HeyGen, Rask). Đối chiếu với code hiện tại của vizsup (đã đọc CLAUDE.md, `docs/plan/*`,
`desktop/`, `backend/app/pipeline/`). **Chỉ nghiên cứu — chưa sửa code.** Quy trình của chủ dự án: dịch phim hoạt hình AI
Trung → Việt, nhiều tập ngắn, đăng lại cho khán giả Việt.

**Độ tin cậy nguồn:** phần lớn là blog/so sánh bên thứ ba và trang marketing; chỉ ElevenLabs docs, Netflix Timed Text
Style Guide, Aegisub manual, Adobe release notes là nguồn gần chính thức. Mỗi dòng ghi **[xác minh]** = đã đọc nguồn;
**[chưa xác minh]** = suy luận hoặc chỉ có nguồn marketing → cần thử tay trước khi xây.
Mọi nhãn effort: S (≤ nửa ngày) · M (1–2 ngày) · L (> 2 ngày).

## 1. vizsup đã có (không đề xuất lại)

Cổng sửa phụ đề trước TTS · glossary + trích thuật ngữ + kiểm tra tuân thủ · bộ nhớ dịch (TM) + "Phong cách của tôi" ·
"AI soát" + cờ "Cần xem lại" (quá dài / chồng / đọc không kịp / lồng tiếng trễ…) · tự canh lời (VAD) · tách vocals/nhạc
(Demucs, `separate.py`) · nhạc nền có duck (`music.py`) · loudnorm cho track lồng tiếng · bình luận (dừng hình/nói đè) ·
mẫu kiểu phụ đề dùng chung mọi dự án (preset) · kính mờ che chữ Trung · watermark người dịch · ghi nguồn + nhãn "Lồng
tiếng AI" + ghi chú quyền · ước tính chi phí · hàng đợi link, dựng hàng loạt, ghép tập có chapter · tìm/thay thế, undo/redo,
phím tắt, waveform thật, sửa tới từng 50 ms.

## 2. Top 15 (xếp hạng theo giá trị / công sức cho quy trình "series nhiều tập")

| # | Tính năng đề xuất | Ai có | Giá trị cho vizsup | Effort | Phụ thuộc | Rủi ro |
|---|---|---|---|---|---|---|
| 1 | **Từ điển phát âm cho TTS** (thay chữ chỉ ở lúc đọc: tên riêng Hán-Việt, số, đơn vị, từ viết tắt "AI/5G", tiếng Anh) — theo dự án + theo "bộ phim" | ElevenLabs (pronunciation dictionary), Azure/SSML lexicon [chưa xác minh chi tiết] | Chất lượng giọng đọc: edge-tts hay đọc sai số/tên/ký hiệu; hiện **không thấy** bước chuẩn hoá văn bản đọc trong `tts.py`. Sửa 1 lần, hiệu lực mọi tập. Không đổi phụ đề hiển thị | S | không | Phải giữ đúng độ dài ước lượng (fit) sau khi thay chữ; manifest TTS khoá theo text → đổi từ điển phải làm mất cache đúng câu |
| 2 | **"Bộ phim" (series profile)**: một nơi giữ glossary + tên nhân vật/xưng hô + giọng + kiểu phụ đề + watermark + tuỳ chọn dựng + từ điển phát âm; tập mới tự kế thừa; dịch tập N nhận **tóm tắt/danh sách nhân vật của tập N-1** | Rask / HeyGen / ElevenLabs (project), CapCut (template/brand kit) [xác minh mức chung] | Giá trị lớn nhất cho series: nhất quán tên và xưng hô giữa các tập, bớt thao tác lặp. Nay "dựng hàng loạt" chỉ chép cài đặt từ một video mẫu; glossary là global, `memory.py` chỉ khớp câu giống hệt | M | không (tái dùng `brief.py`, `terms.py`, `memory.py`, preset) | Dữ liệu cũ sai lan sang tập mới → hiển thị rõ "kế thừa từ…", cho tắt từng mục |
| 3 | **Giới tính/người nói theo từng câu → chọn giọng** (nam/nữ; sau này nhiều giọng FPT.AI) | Rask, ElevenLabs, HeyGen: nhận diện người nói + giọng riêng từng người [xác minh ElevenLabs] | Phim hoạt hình có nhiều nhân vật; 1 giọng kể cho cả phim nghe đơn điệu và là điểm trừ khi so với tool khác. `pitch.py` (YIN, local, miễn phí) đã đo giới tính cho cả clip → áp **theo từng câu** trên `vocals.wav`; kèm LLM đoán từ ngữ cảnh. Chip "Giọng" trên mỗi câu để sửa tay | M (L nếu làm diarization thật) | không bắt buộc; thêm giọng = provider FPT.AI (đã có) | Giọng hoạt hình/lồng tiếng đã chỉnh có thể đánh lừa YIN; edge-tts chỉ có 2 giọng vi-VN → thực tế chỉ phân Nam/Nữ. Phải để người duyệt ở cổng |
| 4 | **Làm nền "mờ" cho khung 9:16** (blur-fill thay vì thanh đen) + đưa ra tuỳ chọn zoom-fit | CapCut (canvas blur), Premiere/CapCut Auto Reframe [xác minh có Auto Reframe; blur-fill là mặc định phổ biến] | Nguồn Bilibili ngang → 9:16 đẹp hơn rõ rệt, 1 filter ffmpeg (`split → scale+boxblur → overlay`). Đừng làm "reframe bám chủ thể" (cần detector, rủi ro cắt mất nhân vật) | S | ffmpeg (đã có) | Không dùng như mẹo lách dò trùng lặp (docs/plan/F: không xây). Kính mờ che chữ Trung nằm trên ảnh đã xử lý → kiểm tra thứ tự filter |
| 5 | **Hậu xử lý thời gian kiểu Netflix/Aegisub**: nối khoảng trống < 0,5 s, khoảng cách tối thiểu 2 khung, thời lượng tối thiểu 20 khung, bám mốc **đổi cảnh** (scene cut) | Aegisub Timing Post-Processor (lead-in/out, nối dòng gần nhau, snap keyframe) [xác minh]; Netflix timing guide [xác minh]; Subtitle Edit "Fix common errors" [xác minh tên] | Phụ đề không nhấp nháy qua các cut; dễ đọc hơn trên điện thoại; **kính mờ che chữ Trung khớp đúng lúc chữ gốc đổi** (ít lộ chữ Trung ở khung hình chuyển). Phát hiện cut bằng `ffmpeg select='gt(scene,0.3)'`, không cần thư viện mới | S–M | ffmpeg | Phim hoạt hình AI cắt nhanh → quá nhiều điểm snap; để mặc định chỉ "nối + khoảng cách", snap cut là tuỳ chọn. Ngưỡng Netflix là cho 24 fps, quy đổi theo fps video |
| 6 | **Cân âm lượng cuối cùng theo từng tập** (mục tiêu ~ -14…-16 LUFS, limiter true-peak) — ưu tiên cho tập sẽ **ghép** | Premiere (Essential Sound auto-match/ducking) [chưa xác minh bản 2026], Descript Studio Sound (làm sạch + cân) [xác minh mô tả chung], Resolve Audio Assistant [xác minh] | "Ghép tập" nối bằng stream copy: tập nào to/nhỏ khác nhau sẽ giật âm lượng khi xem liền. Hiện `loudnorm` chỉ áp lên track lồng tiếng (`assemble.py:824`), không phải bản trộn cuối | S | ffmpeg `loudnorm` 2-pass hoặc `alimiter` | Hai pass tốn thời gian dựng; làm một lần ở bước trộn, ghi LUFS đo được vào render.json |
| 7 | **Chip số tập + thẻ cuối ("Xem tập tiếp…") + ảnh bìa tập** (chọn khung hình + tiêu đề tiếng Việt) | CapCut (cover + text template), Premiere (Essentials/MOGRT) [chưa xác minh chi tiết từng sản phẩm] | Tăng nhận diện series, tỉ lệ bấm xem tập kế (tăng trưởng kênh). Dữ liệu đã có: số tập (đã tách từ tiêu đề cho "Ghép tập"), `disclosure.py` đã đốt chữ góc 5 s bằng `Tag` event trong `.ass` → thêm chip góc cố định + thẻ 2 s cuối cùng cơ chế đó | S (chip + thẻ) · M (ảnh bìa) | ffmpeg/ASS; font đóng gói sẵn | Thẻ cuối kéo dài video → ảnh hưởng join/`-t`; ảnh bìa đừng đè chữ vào mặt nhân vật |
| 8 | **Cắt đoạn (trim) trong editor**: bỏ đoạn đầu (recap/logo), đoạn cuối (thẻ quảng cáo gốc), hoặc khoảng lặng dài — mọi thứ phía sau dịch lên | Descript (xoá chữ → cắt luôn tiếng/hình) [xác minh], CapCut/Premiere (cắt thủ công), AutoCut silence removal [xác minh cho extension Premiere] | Nhiều tập series ngắn có đoạn tóm tắt/đầu-cuối không cần. Hiện chỉ có "dừng hình" (bình luận) làm *dài* video, chưa có cắt làm *ngắn*. **Chưa kiểm chứng** là video của chủ dự án có đoạn như vậy — nên xem 4 tập thật trước | M | không; tái dùng cơ chế dịch mốc của `commentary.py` (đảo dấu) | Rủi ro cao nhất ở danh sách: mọi sidecar (cue, nhạc, bình luận, watermark) phải dịch mốc nhất quán; làm sau #2, #5. Cắt có thể làm lệch "-t pin" nguồn của ghép tập |
| 9 | **Nghe thử 1 câu lồng tiếng ngay ở cổng** (+ lịch sử bản đã tạo, chọn lại) | ElevenLabs Dubbing Studio: tạo lại từng clip, "Clip History", Fixed vs Dynamic generation [xác minh] | Duyệt nhanh hơn: biết ngay câu có đọc kịp/đọc ngượng không mà không phải dựng cả tập. edge-tts miễn phí, 1 câu ≈ 1–2 s | S–M | không | **Chạm ràng buộc cứng**: "không tốn TTS cho phụ đề chưa duyệt". Chấp nhận được nếu (a) do người dùng bấm từng câu, (b) chỉ khi provider miễn phí hoặc có xác nhận chi phí, (c) không ghi vào manifest chính |
| 10 | **Phụ đề hoạt hình theo câu/chữ** (pop-in, fade, trượt; tuỳ chọn tô màu theo từng chữ) | CapCut caption templates (chữ nhảy/animated) [xác minh có template hoạt hình]; Premiere 26.3 "single-word captions" [xác minh]; Resolve 20 Animated Subtitles (bản Studio) [xác minh] | Kiểu phụ đề "đang thịnh" trên TikTok/Shorts; giữ chân người xem. Mức câu (`\fad`, `\t`, `\fscx`) là S và an toàn. Mức từng chữ cần mốc chữ: lấy từ giọng lồng tiếng là **chưa xác minh** (edge-tts có WordBoundary ở một số giọng; chưa có bằng chứng cho vi-VN) → có thể ước lượng theo số ký tự trong thời lượng câu | S (mức câu) · M (mức chữ) | libass (đã dùng) | `test_write_ass.py` khoá byte → mọi tuỳ chọn mới phải mặc định tắt. Chữ Việt có dấu + hiệu ứng scale dễ nhoè trong libass |
| 11 | **Vùng an toàn theo nền tảng** (TikTok / Reels / Shorts): lớp phủ trên preview + cài đặt lề dưới | CapCut hiển thị khung an toàn (safe zone) [chưa xác minh trong nguồn thu được] | Phụ đề/watermark khỏi bị che bởi nút và mô tả của app. F-2 đã chú ý "nằm dưới thanh trên của TikTok". Mở rộng thành preset 1 cú bấm | S | không | Kích thước UI nền tảng đổi theo thời gian → để preset sửa được |
| 12 | **Tiêu đề + mô tả + hashtag tiếng Việt do AI viết** (3 phương án tiêu đề hook, hashtag theo thể loại) | CapCut/Descript có tóm tắt/mô tả bằng AI [chưa xác minh chi tiết] | Rẻ (một lời gọi text, role đã có), tiết kiệm thời gian đăng mỗi tập. Hộp "Mô tả đăng bài" của `disclosure.py` đã có chỗ đặt | S | OpenRouter text role (đã có) | Hook "câu view" gây hiểu sai nội dung gốc; luôn cho sửa, không tự đăng |
| 13 | **Kiểm tra chính tả tiếng Việt + sửa lỗi phổ biến** (hai dấu cách, ngoặc lệch, dấu câu cuối dòng, dòng ngắn mồ côi) | Subtitle Edit: spell check (từ điển LibreOffice/Hunspell), "Fix common errors" [xác minh] | Chặn lỗi gõ lúc người dùng sửa tay (AI soát chỉ đọc bản dịch máy). Lỗi chính tả hiện ra trên màn hình rất lộ | S | từ điển Hunspell vi_VN (cần cân nhắc giấy phép + cách đóng gói) hoặc bỏ spellcheck, chỉ làm "Fix common errors" thuần regex | Từ điển bắt sai tên riêng Hán-Việt → chỉ gạch chân mềm, bỏ qua từ trong glossary |
| 14 | **Xuất/nhập CSV (số thứ tự, đầu, cuối, tiếng Trung, tiếng Việt, ghi chú)** để người khác duyệt trên Google Sheets | ElevenLabs Manual Dub CSV + xuất SRT/CSV [xác minh]; Rask: SRT in/out [chưa xác minh] | Nếu sau này có cộng tác viên duyệt dịch mà không cài app. Hiện đã có `.srt`/`.ass`/`.mkv` | S | `csv` stdlib | Nhập lại phải giữ định danh *theo vị trí* (ràng buộc: cue identity positional) → chỉ cho sửa text, không đổi số dòng |
| 15 | **Tách giọng "làm sạch" cho chế độ giữ giọng gốc** (khử ồn/nhấn lời) | Premiere Enhance Speech; Resolve Voice Isolation; Descript Studio Sound [xác minh mô tả chung] | Chỉ có ích khi chọn "Giữ giọng gốc"/thuyết minh với nguồn ồn. `vocals.wav` đã có; thêm mức "nâng giọng gốc" khi trộn. Thấp vì kênh chủ yếu lồng tiếng | S–M | Demucs (không cài trên máy này, theo docs) | Cảnh báo của review: bản "enhance" dễ thành "quá xử lý" nếu đã khử ồn trước |

## 3. Ghi chú theo tính năng

**1 — Từ điển phát âm.** Làm thành một lớp thuần `spoken_text(vi, rules)` chạy ngay trước khi gửi TTS, *sau* ước lượng độ dài.
Mặc định gợi ý: số → chữ ("15" → "mười lăm"), phần trăm, đơn vị, chữ cái viết tắt. Test: byte-khoá đầu vào cho TTS, manifest khoá theo
`(voice, text_spoken)`. Cần ví dụ thật từ các tập hiện có để biết lỗi nào hay gặp (xem `⚠ giọng đọc không phát âm được` trong log).

**2 — Bộ phim.** Gồm: `series.json` (tên, glossary, nhân vật {tên, giới tính, vai vế, cách xưng}, giọng, preset kiểu, watermark, tuỳ chọn dựng,
từ điển phát âm), liên kết tập → bộ phim. Khi dịch tập mới: nạp glossary + "nhân vật đã biết" vào brief, kèm 5–10 câu cuối tập trước làm ngữ
cảnh. Khi chốt tập: đề xuất đưa thuật ngữ mới vào bộ phim (người dùng bấm duyệt, đúng tinh thần "gợi ý, không tự quyết").
Có thể chia: (a) kế thừa cài đặt (S) → (b) kế thừa glossary/nhân vật (S–M) → (c) tóm tắt tập trước (M).

**3 — Giọng theo câu.** Thứ tự an toàn: (1) đo giới tính từng câu bằng `pitch.py` trên vocals — chỉ *gợi ý* màu chip; (2) người dùng đổi hàng loạt
("tất cả câu của nhân vật A") — chờ `speakers.py`/series có danh sách nhân vật; (3) mới nghĩ đến diarization. Tránh hứa lipsync (xem F-roadmap).
Clone giọng: ngoài phạm vi v1 — chỉ ghi nhận Descript Overdub/ElevenLabs/HeyGen có (xác minh: Descript cần ≈ 60 s mẫu giọng; nguồn blog).

**4 — Khung 9:16.** Hiện `tiktok_canvas()` tính khung 1080×1920 (`assemble.py:513`); cần kiểm tra phần đệm hiện là đen hay khác. Nếu nguồn Douyin
vốn dọc thì tính năng này chỉ ảnh hưởng nguồn Bilibili/ngang.

**5 — Hậu xử lý thời gian.** Số liệu Netflix (24 fps, đã đọc trang chính thức): tối thiểu 20 khung; khoảng cách tối thiểu 2 khung; khoảng
trống 3–11 khung phải đóng về 2 khung; khoảng ≥ 12 khung (0,5 s) được giữ; ngắt trước cut ít nhất 12 khung; nếu lời bắt đầu ≤ nửa giây
sau cut thì đặt đúng khung đầu của cảnh. Aegisub: lead-in/out, nối dòng gần nhau, snap keyframe. Phải đi **sau** "tự canh lời" và **trước** dựng;
một bước "một cú bấm" trong editor, 1 mức hoàn tác như `A`.

**6 — Âm lượng.** Đo trước: cùng 2 tập thật, đo LUFS trên bản dựng hiện tại để xem độ lệch. Nếu < 1–2 LU thì hạ xuống ưu tiên 12.

**7 — Chip/thẻ/ảnh bìa.** Chip số tập: kiểu `Tag` trong `.ass` như disclosure; chỉ bật khi dự án thuộc series. Ảnh bìa: chọn khung hình trên timeline
→ chữ tiêu đề (font đóng gói) → xuất PNG 1080×1920 + 1280×720. Không thêm logo/mẹo "chống trùng lặp".

**8 — Cắt đoạn.** Gợi ý làm theo hai bước: (a) cắt **đầu/cuối** cố định (đơn giản: `-ss`/`-t` + dịch mốc một lần), (b) cắt giữa theo cue (khó, sau).
Nên hỏi chủ dự án trước: các tập thật có đoạn thừa không?

**9 — Nghe thử.** Nếu chủ dự án muốn giữ nguyên ràng buộc, bản thay thế rẻ hơn: *ước lượng* "đọc có kịp không" đã có (`fit_estimate`, cờ "Quá dài").

**10 — Hoạt hình chữ.** Mức câu trước; mức chữ chỉ khi có mốc chữ đáng tin (WordBoundary từ TTS hoặc căn chỉnh theo nguồn). Karaoke đã nằm trong `D-backlog.md`.

**Không đề xuất (và vì sao):**
- Lip-sync — ElevenLabs free không có, Rask tính thêm phí (≈ $3/phút, nguồn so sánh bên thứ ba, độ tin cậy thấp), bị phàn nàn nhiều; đã loại ở F-roadmap.
- Dịch phụ đề song ngữ (CapCut hỗ trợ [xác minh]) — không phục vụ khán giả Việt xem phim; chỉ cân nhắc nếu làm kênh học tiếng Trung.
- Phổ âm (spectrogram) kiểu Aegisub/Subtitle Edit — hữu ích cho hát/karaoke, ít dùng khi canh lời bằng VAD + waveform đã có.
- Reframe bám chủ thể (Premiere/CapCut Auto Reframe) — cần detector và dễ cắt nhân vật; blur-fill (mục 4) đạt 80% giá trị.
- Xoá watermark/logo, mẹo chống dò trùng lặp — chính sách của F-roadmap.
- Sinh video/đoạn dài bằng AI (Premiere Generative Extend, Generative Media Tool) — lệch hướng công cụ.
- Chỉnh sửa "text-based" kiểu Descript cho toàn bộ video — phụ đề là văn bản có timecode, cổng sửa hiện tại đã làm đúng việc đó.

## 4. Thứ tự gợi ý

1. **Nhanh, rủi ro thấp:** #1 từ điển phát âm → #6 đo & cân âm lượng → #4 blur-fill 9:16 → #5 hậu xử lý thời gian (bản "nối + khoảng cách").
2. **Giá trị cho series:** #2(a→b) bộ phim → #7 chip/thẻ/ảnh bìa → #12 tiêu đề/hashtag.
3. **Chất lượng giọng:** #3 giới tính theo câu (gợi ý) → #9 nghe thử (nếu chủ dự án đồng ý).
4. **Sau cùng / cần quyết định:** #8 cắt đoạn · #10 hoạt hình chữ · #11 · #13–15.

## 5. Nguồn

**ElevenLabs** — Dubbing Studio (đã đọc): https://elevenlabs.io/docs/product/dubbing/studio · https://elevenlabs.io/docs/capabilities/dubbing
· giới thiệu: https://www.maginative.com/article/elevenlabs-unveils-dubbing-studio-professional-video-localization/ (Studio ở chế độ bảo trì, chỉ sửa lỗi nghiêm trọng).
**CapCut** — https://capcut.com/resource/dub-the-video · https://www.capcut.com/help/how-to-recognise-subtitles · https://typito.com/blog/capcut-auto-captions-add-subtitles-in-capcut/ · https://bigvu.tv/blog/capcut-online-desktop-editor-review · AutoCut: https://capcut.com/resource/auto-cut-premiere-pro (nguồn của CapCut/bên thứ ba, không phải release notes).
**Adobe Premiere** — https://helpx.adobe.com/premiere-pro/kb/fixed-issues.html (release notes; 26.3 "single-word captions", 26.5 Generative Media) · https://community.adobe.com/announcements-727/premiere-pro-v25-6-firefly-integration-smarter-search-and-more-1546583 · https://photofocus.com/software/adobe-updates-premiere-pro-with-remix-3x-faster-speech-to-text/ (Remix, Speech to Text offline — bản cũ 22.2). *Không tìm được nguồn xác nhận Auto Ducking/Enhance Speech cho 2026.*
**DaVinci Resolve 20** — https://www.newsshooter.com/2025/05/27/davinci-resolve-20-final-release/ · https://www.provideocoalition.com/blackmagic-introduces-davinci-resolve-20/ · https://cined.com/davinci-resolve-20-released-with-handful-of-ai-assisted-features (IntelliScript, Dialogue Matcher, Animated Subtitles/Voice Isolation — một số là bản Studio).
**Descript** — https://vidpros.com/descript-review/ · https://descript.com/es/features/remove-filler-from-audio · https://aiproductionguy.substack.com/p/video-editing-in-a-google-doc-sounds (review bên thứ ba; giá/gói chưa xác minh).
**Subtitle Edit** — https://www.neowin.net/software/subtitle-edit-510/ (changelog 5.1.0: auto-translate với engine OpenAI-compatible, AI review/assistant, waveform + spectrogram) · https://www.techspot.com/downloads/3425-subtitle-edit.html (spell check bằng LibreOffice dictionaries, Fix common errors). *Trang chính thức không tải được (HTTP 403 qua fetch).*
**Aegisub** — https://aegisub.org/docs/latest/timing/ · https://docs.aegisub.org/3.0/Timing_Post-Processor/ (đã đọc).
**Netflix Timed Text Style Guide** — https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines (đã đọc, số liệu ở mục 3) · https://backlothelp.netflix.com/hc/en-us/articles/215758617 (General Requirements).
**HeyGen / Rask / so sánh** — https://videodubber.ai/compare/heygen-vs-rask-ai/ · https://www.heygen.com/blog/best-ai-dubbing-tools (đều là trang so sánh/marketing — chỉ dùng cho bối cảnh, không dùng làm bằng chứng tính năng).
**Tách giọng/nhạc cho lồng tiếng** — https://www.gaudiolab.com/blog/241 · https://www.audioshake.ai/case-studies/dubverse · https://www.lalal.ai/blog/lalal-ai-for-localization-and-dubbing/ (vendor, không có benchmark độc lập).
**edge-tts** — https://github.com/rany2/edge-tts (tham chiếu docs; chưa xác minh WordBoundary cho vi-VN — cần thử `Communicate(..., boundary="WordBoundary")` trên NamMinh/HoaiMy).

## 6. Việc cần làm tay trước khi quyết (không thể làm bằng web)

- Đo LUFS 2 tập đã dựng (mục 6) · xem 4 tập thật có recap/thẻ cuối không (mục 8) · lấy danh sách câu TTS hay đọc sai (mục 1) ·
  thử WordBoundary cho vi-VN (mục 10) · kiểm tra phần đệm 9:16 hiện tại (mục 4).
