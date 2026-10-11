# Bàn giao thiết kế v2 sang PySide6

Thiết kế nằm ở thư mục này. Không lấy v1 làm chuẩn cho khoảng cách, điều khiển hoặc cấu trúc màn v2. Backend, registry công cụ, cache TTS và cơ chế duyệt đang có vẫn là nguồn dữ liệu thật.

## Thành phần và tên thống nhất

| Thành phần | Qt phù hợp | Hành vi |
|---|---|---|
| Thanh quy trình | QFrame, QPushButton / QToolButton | Bắt đầu → Sửa phụ đề → Dựng video; hiện bước đang mở |
| Ô nguồn video | QLineEdit, QPushButton | Enter bắt đầu với mặc định; nhận kéo thả video |
| Dự án gần đây | QListView + delegate | Ảnh nhỏ, tên, trạng thái, lần mở; click tiếp tục |
| Tùy chọn video | QFrame thu gọn, QComboBox | Nhớ mở / đóng; OCR và tệp SRT hiện đúng điều kiện |
| Thẻ lỗi cookie | QFrame, QLabel, QPushButton | Ba bước; chọn tệp và thử lại ở cùng chỗ |
| Danh sách bước xử lý | QListWidget / QFrame | Bước hiện tại, câu tiến độ, thời gian, Hủy; log có thể mở |
| Khung video | VideoCanvas đang có | Giữ tỉ lệ, phụ đề đúng vị trí; kéo vị trí và độ rộng |
| Thanh chia video | QSplitter | Có chặn kích thước tối thiểu; lưu tỉ lệ chia |
| Danh sách câu | QListView + QAbstractListModel + delegate | 2–3 dòng mỗi câu; không dùng các cột trống rộng |
| Bộ lọc xem lại | QPushButton / QToolButton | Số câu chưa kiểm tra; lọc và F8 dùng cùng tập câu |
| Bảng câu đang chọn | QTabWidget / QStackedWidget | Câu đang chọn / Kiểu chữ / Ngữ cảnh |
| Ô sửa tiếng Việt | QPlainTextEdit | Dừng video khi bắt đầu sửa; Enter lưu và sang câu sau |
| Nhóm canh timing | QLineEdit / QTimeEdit, QToolButton | Đầu và cuối có ±50 / 200 ms, đặt = vị trí phát, khóa |
| Giọng của câu | QFrame thu gọn, QSlider, QCheckBox | Âm lượng, tắt tiếng, nghe giọng đã duyệt / có cache |
| Dòng thời gian | Timeline đang có | Sóng âm thật, block, playhead, kéo và thu phóng |
| Hộp duyệt | QDialog | Tóm tắt số câu, giọng, thời lượng; một lần xác nhận rõ ràng |
| Chế độ âm thanh | QPushButton checkable / QButtonGroup | 4 lựa chọn kèm mô tả ngắn; chỉ chọn một |
| Kết quả dựng | VideoPanel, QPushButton | Mở thư mục, xuất SRT / ASS, quay lại sửa |
| Cài đặt nhóm | QListWidget + QStackedWidget | Khóa API / Tải video / AI và mô hình / Đường dẫn |
| Kiểm tra kết nối | Worker đang có + trạng thái inline | Có Hủy; không chiếm thread của pipeline đang chạy |

## Quy tắc thao tác cần giữ

- Click câu: chọn, nhảy video tới đầu câu, đưa câu vào giữa vùng nhìn thấy; giữ vùng chọn nếu Shift-click.
- Khi video phát: chọn cue hiện hành, tự cuộn nếu “Theo video” bật. Trong vùng chồng lấn ưu tiên cue người dùng đang chọn; không giật lựa chọn sang câu khác khi họ đang sửa.
- Focus vào chữ hoặc ô timing: dừng video. Kết thúc chỉnh sửa không tự phát lại.
- Enter ở danh sách: vào sửa, chọn toàn bộ nội dung. Enter khi sửa: commit, sang câu tiếp theo và đặt focus vào ô chữ. Shift+Enter xuống dòng.
- Phím chữ và Space giữ nghĩa nhập liệu khi focus trong ô nhập. Tab ở danh sách chuyển câu; Tab ở các điều khiển khác đi theo thứ tự focus chuẩn.
- Sau nudge / đặt đầu-cuối: giữ cue được chọn, cập nhật dòng, block, cảnh báo và thời lượng. Không đổi timing khi đã khóa. Thời gian phải trong video và đầu phải trước cuối.
- Tách: vị trí phát phải trong cue. Gộp: các cue liền nhau. Mọi sửa chữ, timing, tách / gộp, xóa, âm lượng, khóa và kiểu chữ cần đi qua undo stack đang có.
- Kiểu chữ dùng tọa độ và cỡ chữ theo video gốc, không dùng pixel của khung xem. Font 42 px của source rộng 720 px sẽ được scale theo khung xem. Preview chuẩn khi port lấy từ cơ chế render ASS thật.
- Style scope toàn bộ và các câu đang chọn dùng hai cấp style đang có; đổi style của một câu không làm các câu khác đổi theo.
- “Đã xem”: đánh dấu đã kiểm tra rồi tới cue còn chờ. Các cảnh báo timing vẫn hiển thị dù đã xem; sửa cue sẽ làm trạng thái kiểm tra của nó hết hiệu lực.
- Khi bộ lọc không còn câu: hiện “Bạn đã xem hết các câu cần chú ý” và nút Xem tất cả. Không mất video hoặc các chỉnh sửa.
- Autosave có ba trạng thái: Đang lưu / Đã lưu / Chưa lưu được. Lỗi ghi file phải có thử lại, không báo Đã lưu khi ghi thất bại. Prototype chỉ minh họa hai trạng thái đầu.
- “Duyệt và dựng” mở tóm tắt một lần. Duyệt xong tới phần chọn âm thanh; nút Dựng video mới chạy TTS / dựng. So sánh revision đã duyệt với revision hiện tại ở backend, kể cả khi đổi màn.
- Nghe tiếng gốc luôn dùng được. Nghe giọng AI trước duyệt không tạo TTS; chỉ nghe cache đã được duyệt nếu có. Không dùng số credits hay giá ước đoán khi registry không cung cấp.
- Hủy giữ các bước đã xong. Mở lại bỏ qua bước xong theo trạng thái backend. Tránh chạy nhận dạng / dịch lại chỉ vì quay lại màn Bắt đầu.
- Cài đặt có Lưu / Hủy riêng. “Đã lưu” của editor không có nghĩa các thay đổi trong Cài đặt đã lưu.

## Màu sắc và khoảng cách

Các token ở đầu `design.css` là nguồn chuẩn. Toàn bộ UI là màu phẳng, dựng được bằng QSS và widget hiện có.

| Token | Giá trị |
|---|---|
| Nền ứng dụng | `#101116` |
| Bảng | `#17181f` |
| Vùng nổi | `#1e1f28` |
| Ô nhập | `#111218` |
| Viền | `#2d2e3a` |
| Chữ chính | `#eeeef5` |
| Chữ phụ | `#b4b4c4` |
| Chữ chú thích | `#9495aa` |
| Thao tác chính | `#9868f4` |
| Chữ tím | `#bb99ff` |
| Đã xong | `#85d3ac` |
| Cần xem lại | `#e9bd78` |
| Lỗi | `#ed939c` |

- Nhịp khoảng cách: 4, 8, 12, 16, 24 px. Giữa bảng: 8–14 px. Lề cửa sổ: 18–24 px.
- Bán kính: nút / ô nhập 7 px, bảng 10 px, hộp thoại 13 px.
- Nút chính thường 36 px; nút bắt đầu 49 px; điều khiển nhỏ 29 px; nudge 25–31 px.
- Chữ UI Be Vietnam Pro 12–14 px; tiêu đề màn 18–24 px; thời gian JetBrains Mono 10–12 px; Trung Microsoft YaHei / Noto Sans SC.
- Nút Duyệt, Bắt đầu, Dựng video và Lưu cài đặt nằm ngoài vùng cuộn của nội dung hoặc trong phần nguồn sticky. Kiểm tra lại ở 1366×768 và mức DPI thực tế của Windows.
- Không đưa thanh tiêu đề Windows giả, menu Xem trạng thái, footer “Bản thiết kế”, fixture tiến độ hay toast mô phỏng vào app thật.

## Kiểm tra khi port

Tập trung kiểm tra hành vi có thể làm mất dữ liệu hoặc tiêu tiền: revision duyệt, autosave, undo, timing giới hạn / khóa, cancel / resume và không TTS trước duyệt. Với UI, kiểm tra focus sau Enter / F8, không chiếm phím khi nhập, active cue tự cuộn, và primary action không bị cắt ở laptop. Dữ liệu 20 / 300 cue có sẵn trong trình xem thiết kế để thử kích thước.

Tên mô hình, công cụ khả dụng, ghi chú chi phí và lỗi kết nối phải đọc từ registry / cấu hình thật. Ngày khuyến mãi 30/09/2026 trong màn thiết kế chỉ minh họa tình huống đã hết hạn.
