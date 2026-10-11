# Kiểm tra bản thiết kế v2

Đã kiểm tra trong trình duyệt cục bộ ngày 02/10/2026. Đây là kiểm tra prototype, chưa phải kiểm tra bản port Qt.

- Cả bốn màn ở 1366×768 và 1920×1080: document không tràn kích thước, nút chính trong vùng nhìn thấy.
- Màn Dựng ở laptop: vùng cấu hình cao 485 px, nội dung 485 px; giọng, âm thanh và lựa chọn che chữ đều thấy được.
- Các trạng thái tiến độ nhận dạng / dịch và TTS / dựng: nút Hủy hiện trong vùng nhìn thấy ở cả hai kích thước.
- Nudge cuối câu +50 ms: `00:39.630 → 00:39.680`. Ctrl+Z trả về giờ trước khi chỉnh.
- Khóa timing: cả hai ô giờ và tám nút nudge bị vô hiệu hóa; bỏ khóa cho phép sửa tiếp.
- Enter sau khi sửa: chuyển từ câu 4 sang câu 5 và đưa focus vào chữ.
- F8 từ câu 5: đến câu 14 cần xem lại. “Đã xem” giảm số câu chờ kiểm tra `13 → 12` và chuyển tiếp.
- Xem thử kiểu: có khung hình video đang chọn, phụ đề theo kiểu và vị trí đang dùng.
- Video gốc thật phát và dừng được từ thanh điều khiển.
- Sau duyệt, sửa chữ rồi mở Dựng: trở về trạng thái cần duyệt phụ đề, không có nút chạy TTS trực tiếp.
- Không có lỗi JavaScript ở các màn / trạng thái được xem. Hai file JavaScript qua kiểm tra cú pháp Node.
- Font và SVG đóng gói cục bộ; không phụ thuộc CDN cho phần giao diện.

Ảnh trong `screenshots/` gồm bốn màn và các trạng thái trọng tâm ở hai kích thước. Các tình huống khác có trong menu Xem trạng thái; kích thước JPEG đã được kiểm tra bằng Pillow.
