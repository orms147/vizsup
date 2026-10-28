# Tóm tắt đêm 28→29/10/2026 (đọc cái này trước, chi tiết ở H-overnight-log.md)

Nhánh: `api-optimization` (đã push; **không** đụng `main`). Bộ test: 1400 qua, ruff sạch.
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

## Cần bạn quyết
- Merge `api-optimization` vào `main` (để danh mục model tự cập nhật chạy được).
- Gửi lại link tập 9 thật nếu muốn thử lại "thêm tập".
- "Giọng theo nhân vật": để mặc định tắt cho tới khi đo xong độ chính xác (đang làm).
