# vizsup — hướng dẫn dùng (bàn giao 31/10/2026, cập nhật 01/11/2026)

Một trang cho chủ máy: cài một lần, rồi mỗi ngày chỉ cần **bấm duyệt**.
Chi tiết từng phần nằm ở các tài liệu được nhắc tên bên dưới.

---

## Mở app

Bấm đúp biểu tượng **vizsup** (tím) trên Desktop hoặc Start Menu. Không cần mở terminal. Cập nhật code xong muốn tạo lại `vizsup.exe` và lối tắt: `make app` (hoặc `.\build-app.ps1`).

Hướng dẫn đầy đủ có ảnh: https://claude.ai/artifact/AKru3GxbiE7r4kkH5dWH7a

## Luồng hằng ngày (sau khi cài xong)

```
Tập mới trên Douyin / Bilibili (hoặc bạn gửi link cho bot / dán vào app)
  → app tự tải, đọc phụ đề trên hình (không có thì AI nghe), dịch theo xưng hô của bộ phim
  → dừng ở bước DUYỆT  ── bot Telegram gửi các câu cần xem + nút [✅ Duyệt]
  → bạn bấm Duyệt (trên điện thoại hoặc Thư viện → «Duyệt nhanh»)
  → app tự dựng → tự cắt Short → tải lên YouTube / Facebook hẹn giờ, TikTok đăng đúng giờ
  → bot báo "⏰ đã tải lên — tự công khai lúc …" và link bài
```

- **Bước duyệt luôn là của bạn.** Chưa duyệt thì app không đọc lồng tiếng, không dựng, không
  đăng gì.
- Muốn sửa kỹ một tập: mở tập đó trong app, sửa, rồi duyệt như cũ.

## Mới (01/11/2026)

- **Mỗi lần dựng ra cả hai bản**: ngang (YouTube / Facebook) và dọc 9:16 1080×1920
  (TikTok / Reels / Shorts) — ô «Dựng thêm bản dọc 9:16» ở màn Dựng, bật sẵn; «Ghép video»
  cũng ra bản dọc. «Lưu video…» lưu cả hai, «Xem bản dọc» xem ngay trong app.
- **Mặc định theo lựa chọn của bạn**: kiểu chữ mẫu «Default», giọng Ngọc Huyền ×1.3, thanh
  mới **«Cao độ giọng»** (+2 gần giọng review phim), giữ âm gốc + lồng tiếng, 1080p «Nét»,
  watermark «Hải Thanh Review». Lần sau app nhớ giọng / tốc độ / cao độ / âm thanh bạn chọn.
- **Màn Bắt đầu gọn hơn**: chỉ còn Nguồn phụ đề, Bộ phim, Khoanh vùng chữ Trung; phần còn lại
  ở «Tùy chọn nâng cao».
- **Soát xưng hô cả video** (bật sẵn): AI đọc cả tập để biết ai nói với ai, rồi sửa chữ tự
  xưng sai cặp (nữ sinh nói với anh Cố «tôi» → «em»).
- **Đăng bài**: YouTube tự gắn nhãn «Tạo bằng AI»; tiêu đề tự động
  «Tập N | Chuyến Xe Tận Thế | Hải Thanh Review», mô tả có hashtag + «Bộ phim:» + ghi nguồn.
  Video đã xuất + mô tả soạn sẵn: `C:\Users\Admin\Videos\Chuyến Xe Tận Thế\`.
- **Thư viện** ghi «Chuyến Xe Tận Thế · Tập 9» thay tiêu đề tiếng Trung dài.

## Cài một lần

| # | Việc | Ở đâu | Ghi chú |
|---|---|---|---|
| 1 | Đọc **lưu ý giữ kênh**, nhắn xin phép tác giả gốc | [plan/J-channel-safety.md](plan/J-channel-safety.md) | Có mẫu tin nhắn tiếng Trung |
| 2 | Nối **bot Telegram** | Cài đặt → Đăng video → Điều khiển qua Telegram | [J-publish-setup §6](plan/J-publish-setup.md) |
| 3 | Nối **YouTube** (rồi Facebook, TikTok nếu dùng) | Cài đặt → Đăng video | [J-publish-setup §1–3](plan/J-publish-setup.md) — nhớ bấm "Publish app" ở Google |
| 4 | Bật **Theo dõi** tập mới cho bộ phim | Bộ phim 《…》 → tab **Theo dõi** → dán link 1 tập → **Kiểm tra ngay** | App tự tìm 合集; nên chọn mỗi 6 giờ |
| 5 | Bật **Tự động đăng** | Thư viện → **Tự động đăng** | Chọn nền tảng × video dài / Short, giờ đăng, 1 bài/ngày, tên phim tiếng Việt. Mấy tập đầu tick **Đăng thử** (YouTube riêng tư, TikTok chỉ mình tôi, Facebook xuất gói) — chạy ổn thì bỏ tick |
| 6 | **Chạy nền** khi app tắt | Bộ phim → Theo dõi → **Bật chạy nền** (và nút "Chạy nền mỗi 15 phút" ở thẻ Telegram) | Tác vụ Windows: kiểm tra tập mới, trả lời bot, dựng tập đã duyệt, đăng bài tới giờ |

- **Bộ phim mới:**
  - Tập đầu được dựng theo cài đặt lần dựng gần nhất của bạn.
  - Xưng hô và tên nhân vật tự học từ các tập bạn đã duyệt.
  - Xem hoặc sửa trong Bộ phim → **Nhân vật**; dòng bạn sửa luôn được giữ.

## Bot Telegram làm được gì

- **Gửi link** → app chạy tới bước duyệt rồi gửi phụ đề cần xem.
- **[✅ Duyệt]** → dựng và lên lịch đăng.
  - Nút tự từ chối nếu phụ đề đã đổi sau khi bot gửi.
- **`/trangthai`** → tập đang chờ duyệt, lịch đăng, bài đã đăng.
- Bot chỉ trả lời đúng tài khoản Telegram đã ghép.

## Khi có chuyện

| Thấy gì | Làm gì |
|---|---|
| Bot im lặng | App đang tắt và chưa bật chạy nền → mở app, hoặc bật chạy nền (bước 6) |
| "❌ chưa đăng được …" | App tự thử lại sau 1 giờ (3 lần). Lỗi đăng nhập → Cài đặt → Đăng video → nối lại |
| Video YouTube bị để "Riêng tư" | Mở Studio xem lý do (bản quyền / dự án API chưa kiểm duyệt) — xem J-publish-setup §1.7 |
| Studio báo khiếu nại bản quyền trước giờ công khai | Đổi video đó về "Riêng tư" trong Studio; làm theo J-channel-safety §3 |
| Theo dõi báo lỗi Douyin / Bilibili | Bộ phim → Theo dõi → **Kiểm tra ngay** để xem lỗi; lỗi tạm thời được thử lại ở lần kiểm tra sau |
| Muốn tắt hết tự động | Thư viện → Tự động đăng → tắt; Bộ phim → Theo dõi → Tắt chạy nền |

## Chi phí

- Mỗi tập ~2–4 phút tốn khoảng **$0,002–0,004** tiền AI (đọc chữ trên hình là miễn phí; phần
  dịch đi qua máy chủ rẻ nhất).
- Lồng tiếng, dựng, cắt Short, đăng: **miễn phí**.
- Xem số thật: mỗi dự án có `usage.json`; nhật ký chi phí ở [plan/G-cost-log.md](plan/G-cost-log.md).

## Sao lưu

Ba thứ đáng giữ:
- `storage/_series/` — bộ phim: nhân vật, thuật ngữ, cài đặt;
- `storage/_autopilot/` — kế hoạch và lịch đăng;
- `.env` — các khóa API.

`storage/_accounts/` là chìa khóa đăng bài của các kênh: **không chia sẻ, không đưa lên
mạng**. Mất máy thì gỡ quyền ở trang tài khoản của từng nền tảng rồi nối lại.
