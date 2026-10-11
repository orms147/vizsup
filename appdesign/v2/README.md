# vizsup — thiết kế v2

**Hướng thiết kế:** bàn dựng cá nhân, thao tác ngắn và video luôn trong tầm mắt. Màn Sửa phụ đề là trung tâm; các tùy chọn ít dùng chỉ mở khi cần.

## Mở và dùng thử

Mở `index.html` để vào bàn sửa phụ đề. Bốn màn chính là các file mới, bản v1 ở thư mục cha được giữ nguyên:

- `v2 — Bắt đầu.html`
- `v2 — Sửa phụ đề.html`
- `v2 — Dựng video.html`
- `v2 — Cài đặt.html`
- `v2 — Bộ thành phần.html`: màu sắc, chữ, nút và trạng thái dùng chung.

Để phát **video gốc thật**, mở máy chủ xem trước từ thư mục này:

```powershell
node preview-server.cjs
```

Sau đó vào `http://127.0.0.1:4317`. Máy chủ chỉ phục vụ các tài nguyên thiết kế và một đường dẫn video gốc đã chỉ định; không phục vụ cấu hình, cookie, khóa API hoặc các file khác trong repo.

Mở HTML trực tiếp vẫn có ảnh, phụ đề, sóng âm và các tương tác thiết kế. Video phát nếu project nguồn còn ở đường dẫn cũ.

## Những thay đổi đáng dùng nhất

1. **Dán link là bắt đầu.** Chọn công cụ và ngữ cảnh không chen vào tác vụ chính. Ghi chú cho riêng video cách một cú bấm. Nút Bắt đầu giữ ở đầu vùng cuộn khi mở tùy chọn.
2. **Xem và sửa cùng một chỗ.** Video dùng toàn bộ chiều cao bên trái; danh sách và ô sửa nằm sát nhau. Chọn câu sẽ nhảy video tới câu đó. Bắt đầu nhập chữ hoặc giờ sẽ dừng video để tránh mất tiêu điểm.
3. **Canh timing trong một nhịp.** Hai ô đầu / cuối có nút ±50, ±200 ms; `[` và `]` lấy vị trí phát. Kéo thân block để dịch cả câu, kéo cạnh để đổi đầu / cuối. Khóa câu ngăn chỉnh timing ngoài ý muốn.
4. **Duyệt theo vấn đề.** `F8` đi đến câu cần xem lại kế tiếp, `Shift+F8` quay lại. “Đã xem” xác nhận câu vừa kiểm tra rồi chuyển tiếp. Danh sách có lọc riêng và số câu đang chờ kiểm tra.
5. **Sửa chữ liên tục.** Enter mở ô sửa; Enter trong ô sửa lưu và mở câu kế tiếp; Shift+Enter xuống dòng. S, M, Space, `[` và `]` không chiếm phím khi đang nhập chữ.
6. **Tùy chỉnh ít gây phân tâm.** Kiểu chữ và ngữ cảnh dùng tab trong cùng bảng. Giọng từng câu thu gọn; Rút gọn nằm ngay cạnh tên câu. Chọn “Cân bằng” để đổi sang “Ưu tiên video” khi cần quan sát kỹ.
7. **Lỗi có đường ra.** Lỗi cookie có hướng dẫn ba bước và chọn tệp ngay tại thẻ lỗi. Lỗi mạng thử lại từ bước đã xong. Thiếu khóa có đường vào đúng phần Cài đặt.
8. **Kết quả có hành động tiếp.** Mở thư mục, xuất .srt / .ass hoặc quay lại sửa. Phụ đề đổi sau khi duyệt sẽ khóa dựng cho đến lần duyệt tiếp theo.

## Bố cục

**Cân bằng — khuyên dùng:** video 38,5% chiều rộng, vùng câu và bảng chỉnh ở bên phải; timeline phía dưới hai vùng này. Clip dọc không bị kéo méo. Chạy được ở 1366×768; các cột mở rộng và chữ / khoảng cách tăng ở 1920×1080. Có thể kéo thanh chia cột.

**Ưu tiên video:** thu gọn thanh ứng dụng và timeline, tăng chiều cao video, giữ bảng timing bên cạnh và nút Duyệt trong tầm nhìn. Đây là bố cục tập trung trong bản thiết kế; khi port Qt có thể dùng cửa sổ toàn màn hình để giấu thanh tiêu đề hệ điều hành.

## Dữ liệu và phạm vi bản thiết kế

- 40 câu tiếng Việt, tiếng Trung, timing và ngữ cảnh từ project có sẵn `storage/2dc712c162d7`.
- Khung hình nguồn thật: 720×1566, dài 328 giây. Giữ đúng tỉ lệ này thay vì ép thành 9:16.
- Sóng âm lấy RMS từ `source.wav`, không phải sóng trang trí ngẫu nhiên.
- Timing quá ngắn và chồng lấn được suy ra từ thời gian thật. “Quá dài” trong prototype là ước lượng theo số ký tự / giây, **chưa phải kết quả đo TTS**.
- Nhãn OCR và thiếu thuật ngữ là fixture mô phỏng để xem UX, không phải kết luận QA mới về video. “Đã xem” ghi nhận đã kiểm tra, không sửa dữ liệu timing hay chứng nhận câu đúng.
- Trạng thái 300 câu dùng nội dung của clip thật, lặp lại trên một timeline mô phỏng để kiểm tra danh sách dài. Khung 16:9 kiểm tra bố cục xem; source vẫn là clip dọc gốc.
- Dự án gần đây, tiến độ, kiểm tra khóa, rút gọn AI và kết quả dựng là tình huống thiết kế. **Không gọi AI, không tạo giọng, không dựng video và không ghi cấu hình backend.** Trường khóa chứa chuỗi mẫu, không đọc `.env`.
- Chỉnh phụ đề, nudge, tách / gộp, hoàn tác / làm lại, lọc, tìm / thay, chọn nhiều, khóa, kéo timeline, kéo hộp phụ đề, kiểu chữ, phím tắt và xuất phụ đề chạy cục bộ trong prototype.
- Bản sửa được ghi vào `localStorage` riêng của thiết kế; mở file không có `?state=` sẽ khôi phục bản sửa. `?state=default` mở fixture sạch để so sánh.

## Xem các trạng thái

Nút **Xem trạng thái** ở thanh dưới thuộc công cụ xem thiết kế, không đưa nó vào ứng dụng Qt. Nó mở các tình huống rỗng, đang chạy, lỗi, thành công, tìm kiếm, chọn nhiều, 20 / 300 câu, kiểu chữ và ngữ cảnh. Cũng có thể thêm `?state=` vào mỗi file, ví dụ:

| Màn | Trạng thái |
|---|---|
| Bắt đầu | `idle`, `first`, `options`, `download`, `recognition`, `context`, `translation`, `cookie`, `network`, `cancelled`, `done` |
| Sửa phụ đề | `default`, `video`, `landscape`, `review`, `search`, `multi`, `style`, `brief`, `20`, `300`, `empty`, `loading`, `error` |
| Dựng video | `locked`, `ready`, `tts`, `encoding`, `cancelled`, `error`, `success` |
| Cài đặt | `keys`, `first`, `testing`, `key-error`, `key-success`, `download`, `ai`, `paths` |

Ảnh các màn chính và các trạng thái trọng tâm ở hai kích thước nằm trong `screenshots/`. `qt-handoff.md` mô tả thành phần, trạng thái và quy tắc tiêu điểm để port.

## Tài nguyên

Font Be Vietnam Pro và JetBrains Mono được đóng gói cùng giấy phép OFL. Chữ Trung dùng Microsoft YaHei hoặc Noto Sans SC trên hệ thống. Biểu tượng Lucide được lưu dưới dạng SVG, cùng giấy phép ISC. Các ảnh lấy từ video mà người dùng đã có trong repo, chỉ phục vụ thiết kế cá nhân này.
