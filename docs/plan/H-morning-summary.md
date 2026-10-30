# Tóm tắt đêm 28→29/10/2026 (đọc cái này trước, chi tiết ở H-overnight-log.md)

Nhánh: `api-optimization` (đã push; **không** đụng `main`). Bộ test: **1490 qua** (chạy 1 phút 50 giây), ruff sạch.
Chi phí OpenRouter cả ngày (UTC, gồm cả phần bạn dùng ban ngày): **≈ $0.08** (trần $2–3).
Không cài thư viện mới vào dự án. Không sửa dự án thật — mọi thử nghiệm chạy trên **bản sao**
(`…\scratchpad\e2e_storage`). Kiểm tra chỉ-đọc: 2 dự án đã duyệt của bạn vẫn còn "đã duyệt".

## Việc dở đã xong (test 4 link + tập 9)
- Dựng hàng loạt 4 video (tập 1–5, 6, 7, 8) + thêm "tập 9" vào hàng giữa chừng → dựng đủ.
- Giả lập **sập app** giữa lúc dựng → mở lại hiện "Làm tiếp việc còn dở?" → dựng tiếp từ chỗ dừng.
- Ghép tập 1–8: chương "Tập 1–5 / 6 / 7 / 8" đúng; rồi **thêm tập 9 vào video ghép** → ghép
  không mã hoá lại, giữ chương cũ, 582 câu phụ đề khớp.
- ⚠ Link "tập 9" bạn gửi (modal_id 7699919754521283890) **không phải 《末日校车》** mà là quảng cáo
  game 剑网3 ("50级新世代！… 10月29日公测"). App tải đúng id đó — bạn gửi lại link tập 9 thật nhé.

## Tính năng mới (bấm thử theo thứ tự này)
1. **Bộ phim** (màn Bắt đầu → Tùy chọn → "Bộ phim" + ⚙): thuật ngữ, nhân vật/xưng hô, brief, cách
   đọc, kiểu chữ, cài đặt dựng mang sang tập sau; dịch tập N có 8 câu cuối tập N−1 làm ngữ cảnh.
2. **Soát lỗi tiếng Việt** tự động sau khi dịch (dấu câu, "..."→"…") + đánh dấu chữ Trung sót, lệch
   số, từ chưa dịch, thuật ngữ sai.
3. **Editor**: danh sách gọn (thấy ~1.7× số câu), dải tổng quan cả tập trên timeline, video dọc
   không phí chỗ, D = "Đã xem" + câu kế, Ctrl+↑/↓ trong ô chữ, chuột phải có menu, kéo mép câu
   tự hít vào playhead/câu bên cạnh (Alt = tự do), **vùng che của TikTok/Reels/Shorts** (nút điện
   thoại ở đầu khung video), xuất/nhập **CSV** để soát bằng Google Sheets.
4. **Thư viện**: lọc theo trạng thái (Chưa duyệt / Đã duyệt / Đã dựng / Cần dựng lại), xếp "Theo tập",
   phím tắt (↑↓ Enter Space Delete).
5. **Màn Dựng**: "Thẻ tập" (chữ "Tập 9" đầu video + "Còn tiếp · Tập 10" 4 giây cuối), "Giọng theo
   nhân vật (nam/nữ)" (mặc định TẮT — đang kiểm tra độ chính xác), âm lượng đều −14 LUFS giữa các
   tập, nền mờ thay viền đen khi dựng 9:16, "Gợi ý tiêu đề + hashtag (AI)" trong "Mô tả đăng bài".
6. **Cập nhật model tự động**: Cài đặt → AI và mô hình → "Cập nhật model mới". Danh sách model khuyên
   dùng nằm ở `catalog/models.json` — **chỉ có hiệu lực khi file này lên nhánh main** (bạn merge).
   Chỉ nhận model còn sống trên OpenRouter; model bạn tự chọn luôn được giữ. Cũng báo khi có bản
   app mới (phần tải bộ cài có chữ ký để sau).

## Lỗi đã sửa đêm qua
- Timing phụ đề: Whisper trả rỗng / dồn chữ khi có nhạc → hỏi lại, dùng giọng đã tách; câu "đoán"
  giờ được đặt vào đúng đoạn có giọng (tập 1–5: 8 → 2 câu đoán, không còn câu nằm giữa khoảng lặng).
- edge-tts ban đêm hay trả "no audio" (~40 %) → mỗi câu thử lại riêng, 6 câu song song (nhanh ~2×).
- **Mở một dự án đã duyệt làm mất trạng thái "đã duyệt"** (lỗi cũ: tự chọn "Hộp đặc" rồi lưu) → hết.
- Tiêu đề dài + màn hình phóng to (DPI) làm editor tràn khỏi cửa sổ → hết.
- Video 1924×1080 ra 1282×720 khiến ghép tập phải mã hoá lại → chuẩn hoá 1280×720.
- Chip trong thư viện bị bóp chữ ("Video ç") → hết.

## Nửa sau đêm (02:00–03:40)
- **Đoán giọng nam/nữ** sửa lại: ngưỡng riêng cho từng video + tách giọng khi video có chữ in sẵn.
  Tập 7: trước 2 nam/30 nữ/50 không rõ → sau 25/43/14 (khớp cách xưng hô trong lời thoại 20/22).
  Tuỳ chọn "Giọng theo nhân vật" vẫn **mặc định tắt** — bạn nghe thử rồi hãy bật.
- **Session 1 soát lỗi toàn bộ code đêm qua**: 7 lỗi thật đã sửa (quan trọng nhất: nhập CSV mà Excel
  đã biến ô thành "#NAME?" sẽ ghi đè phụ đề → giờ bị từ chối). Danh mục model: chặn model đắt
  (> $25/M token) và model không đọc được ảnh cho việc OCR.
- **Đóng app khi đang chờ mạng** (kiểm tra model, gợi ý tiêu đề) không còn treo/văng (< 0,6 s).
- **Màn hình phóng to 150 %** (cửa sổ của bạn): màn Dựng mất nút "Dựng video", thẻ âm thanh trống,
  editor không thấy timeline → đã sửa và **kiểm tra trên cửa sổ thật**.
- Màn Dựng: mục "Đăng tải & thương hiệu" gấp gọn được (vẫn báo vàng khi chưa ghi quyền dùng video),
  dòng tóm tắt cài đặt trên nút dựng, "Xem N câu" ở cảnh báo đọc nhanh → mở editor chỉ các câu đó.
- Xuất CSV: ô bắt đầu bằng "-", "=", "+" được bảo vệ để Excel/Sheets không biến thành công thức.
- Tài liệu `docs/architecture.md`, `docs/providers.md`, `CLAUDE.md` cập nhật theo code mới.

## Cần bạn quyết
- Merge `api-optimization` vào `main` (để danh mục model tự cập nhật chạy được).
- Gửi lại link tập 9 thật nếu muốn thử lại "thêm tập".
- "Giọng theo nhân vật": để mặc định tắt cho tới khi đo xong độ chính xác (đang làm).

---

# Tóm tắt đêm 29→30/10 (đêm 2)

Nhánh `api-optimization` = `main` (eb34a4b+). Bộ test: **1578 qua**, ruff sạch. Chi phí OpenRouter từ
sáng 29/10 tới sáng 30/10: **≈ $0.03** (trần $2–3). Thư viện mới cài vào app: `vieneu 3.8.3` (cài
`--no-deps`), `sea-g2p`, `soundfile`, `soxr`, `kaldi-native-fbank` — ghi trong log.
⚠ Khoảng 00:30–08:16 phiên điều phối bị ngắt (máy/ứng dụng khởi động lại) nên đêm qua dừng sớm;
sáng nay tôi đã nối lại, duyệt và merge nốt phần 2 session đã làm xong.

## Cần bạn làm khi dậy
1. **Duyệt 4 tập 1–5, 6, 7, 8 trong Thư viện** (đã có video dựng sẵn; hệ thống không cho tôi tự duyệt
   hộ — đúng). Tập 9 + video ghép 1–8, 1–9 đã ở Thư viện.
2. **Nên dựng lại tập 1–9 rồi ghép lại** (giọng đã có sẵn nên nhanh): bản dựng mới cắt khoảng lặng của
   giọng đọc → tập 1–5 từ 167 câu "quá dài" còn 2, nội dung không còn bị cắt (57 s → 5 s). Nghe so sánh
   trước/sau (20 câu đầu tập 9) — file trong thư mục tạm của session 1:
   `C:\Users\Admin\AppData\Local\Temp\claude\c--Work-AI-vizsup\93824f1b-178d-4cf8-ad43-854a972619b1\scratchpad\fit\listen\`
   (`ep9_first20_before_padded.mp4` / `ep9_first20_after_trimmed.mp4`). Giọng giờ vào đúng lúc phụ đề
   hiện (sớm hơn ~0,2 s so với bản cũ).
3. Thử **Cài đặt → "Giọng đọc trên máy (VieNeu)"** (đã cài sẵn) và **màn Dựng → danh sách giọng**.

## Đã làm
- **Dịch / lồng tiếng vừa khe:** nguyên nhân chính của câu "quá dài" là ~0,9 s im lặng edge-tts chèn
  quanh mỗi câu, không phải bản dịch. Đã cắt + tính lại độ dài theo đúng tốc độ bộ phim. Bản dịch cũng
  ít bị rút gọn oan hơn (32 → 11 câu), giữ đủ ý hơn.
- **Chi phí:** prompt đã xếp đúng để được tính rẻ phần lặp; mỗi tập dịch chỉ ~$0.0026 — không còn gì đáng
  tối ưu. Nhãn người nói bằng AI: đã thử, không đáng thêm (sai đúng chỗ cần nhất).
- **Short (màn dọc):**
  - Màn Dựng → **"Cài sẵn Short (9:16)"**: nền mờ hai bên, phụ đề tránh vùng nút/caption của
    TikTok/Reels/Shorts, **câu hook to ở dải trên**, **phụ đề to ở dải dưới**, "Xem full tập N" ở cuối.
    Cài đặt nhớ theo bộ phim.
  - Video gốc dọc: phụ đề tự tránh vùng che.
  - Thư viện → ⋯ → **"Cắt Short (30–60 giây)"** từ tập đã duyệt: gợi ý đoạn có hook (tự động hoặc AI),
    tạo dự án Short mới dùng lại giọng đã đọc, **cần bạn duyệt riêng**.
  - Ảnh thử (bản sao tập 9): `…\8f845e3a-…\scratchpad\ui\short_hook2.png`, `short_mid2.png`, `short_end2.png`.
- **VieNeu:** nút cài trong Cài đặt, báo khi tải model lần đầu, dùng được cho Bình luận.
- **"Cần dựng lại"** giờ so nội dung phụ đề đã dựng, không so giờ file.

## Gợi ý tiếp theo
- Phụ đề Short nhảy theo từng chữ (karaoke) — rất hợp TikTok.
- Câu hook nên là câu "nhử" khác câu thoại đầu (hiện có thể trùng); sửa được trong hộp thoại Cắt Short.
- Bản cài đặt (Inno Setup) có chữ ký + tự tải bản mới.
