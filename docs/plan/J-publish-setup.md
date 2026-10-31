# J — Nối tài khoản để vizsup tự đăng video (YouTube · Facebook · TikTok) + điều khiển qua Telegram

Hướng dẫn cho chủ máy, làm **một lần**. vizsup đăng qua **API chính thức** của từng nền tảng,
bằng **ứng dụng do chính bạn tạo** trên trang dành cho nhà phát triển của họ. vizsup không bao
giờ hỏi mật khẩu: bạn đăng nhập trong trình duyệt, nền tảng trao cho vizsup một "mã truy cập".

- **Mã ứng dụng** (Client ID / App ID / Client key + secret): dán vào **Cài đặt → Đăng video**,
  lưu trong `.env` ở thư mục gốc (không đưa lên git).
- **Mã truy cập tài khoản**: lưu trong `storage/_accounts/<nền tảng>.json` trên máy này (không
  đưa lên git, không in ra log). **Ngắt kết nối** trong Cài đặt sẽ xóa tệp đó.
- Nền tảng chưa nối vẫn dùng được "Gói đăng (thư mục)": vizsup xuất sẵn video + tiêu đề + mô tả
  để bạn tự đăng tay.

Kiểm tra lần cuối: **31/10/2026**. Các trang của Google/Meta/TikTok đổi thường xuyên — tên nút
có thể khác đôi chút; nếu khác nhiều, xem các liên kết chính thức ở cuối mỗi phần.

## Tóm tắt — được gì khi CHƯA xin kiểm duyệt (audit)

| Nền tảng | Bạn tạo | Khi chưa kiểm duyệt | Công khai ngay? |
|---|---|---|---|
| YouTube | Dự án Google Cloud + OAuth client loại *Desktop app* | ~100 video/ngày; chế độ *Testing* chỉ giữ kết nối **7 ngày** → bấm *Publish app* | Có (theo tài liệu hiện tại — xem 1.7) |
| Facebook (Trang) | App Meta, 3 quyền về Trang | App ở *Development*: bài đăng có thể chỉ người có vai trò trong app thấy → chuyển *Live* | Có, khi app ở Live |
| TikTok | App TikTok for Developers (Login Kit + Content Posting API) | Chỉ đăng **"Chỉ mình tôi"** lên tài khoản **riêng tư**; tài khoản công khai → vizsup gửi **bản nháp vào hộp thư TikTok** | Không — bạn tự bấm đăng trong app TikTok |

> **Nói thẳng về video đăng lại (reup):** cả ba nền tảng đều có luật về nội dung không nguyên
> gốc. YouTube: "reused content" khiến kênh bị từ chối/mất kiếm tiền (YPP). Facebook: "nội dung
> không nguyên bản" bị giảm phân phối, mất kiếm tiền. TikTok ghi rõ trong hướng dẫn cho nhà phát
> triển: *"Not acceptable: An app that copies arbitrary contents from other platforms to TikTok"*.
> Một ứng dụng chuyên **đăng lại video của người khác** rất dễ **trượt kiểm duyệt API** (YouTube,
> TikTok) và bị nền tảng hạn chế. Việc dịch + lồng tiếng giúp video "biến đổi" hơn nhưng không bảo
> đảm gì. vizsup luôn hiện ghi chú bản quyền (`rights.json`) cạnh mỗi bài — hãy ưu tiên video bạn
> có quyền đăng (được tác giả cho phép, nguồn cho phép dùng lại).

---

## 1. YouTube (video dài + Shorts)

### 1.1 Tạo dự án Google Cloud
1. Mở <https://console.cloud.google.com/> và đăng nhập **đúng tài khoản Google sở hữu kênh**.
2. Ô chọn dự án (trên cùng) → **Dự án mới / New project** → tên `vizsup` → **Tạo**.

### 1.2 Bật YouTube Data API v3
**APIs & Services → Library** → tìm **YouTube Data API v3** → **Enable**.

### 1.3 Màn hình đồng ý OAuth (Google Auth Platform)
1. **APIs & Services → OAuth consent screen** (giao diện mới tên **Google Auth Platform**) →
   **Get started**.
2. *App name* `vizsup`, *User support email* = email của bạn → *Audience*: **External** →
   *Contact information* = email của bạn → đồng ý điều khoản → **Create**.
3. **Data access → Add or remove scopes** → thêm hai quyền:
   - `https://www.googleapis.com/auth/youtube.upload` (tải video lên)
   - `https://www.googleapis.com/auth/youtube` (đọc tên kênh, tạo danh sách phát)
   → **Update → Save**.
4. **Audience → Test users → Add users** → thêm **email Google của bạn**.

> ⚠️ **7 ngày:** Google ghi rõ: dự án *External* đang ở trạng thái **Testing** nhận refresh token
> **hết hạn sau 7 ngày** — tức là tuần nào cũng phải bấm *Kết nối lại*. Để khỏi phải vậy:
> **Audience → Publish app → Confirm** (trạng thái *In production*). Bạn **không cần** gửi xác minh
> cho việc dùng riêng: khi kết nối, Google hiện *"Google hasn't verified this app"* → bấm
> **Advanced → Go to vizsup (unsafe)** (đây là app của chính bạn). App chưa xác minh dùng quyền
> nhạy cảm bị giới hạn 100 người dùng — thừa cho một người.

### 1.4 Tạo OAuth client loại Desktop
**Clients (hoặc Credentials) → Create client → Application type: Desktop app** → tên
`vizsup desktop` → **Create** → chép **Client ID** và **Client secret**.
(Loại *Desktop app* không cần khai báo URL chuyển hướng: vizsup tự mở cổng
`http://127.0.0.1:<cổng ngẫu nhiên>/` trên máy để nhận kết quả đăng nhập.)

### 1.5 Nối trong vizsup
**Cài đặt → Đăng video → YouTube** → dán *Client ID*, *Client secret* → **Kết nối** → trình duyệt
mở trang Google → chọn tài khoản (kênh thương hiệu: chọn đúng kênh) → **Cho phép** → trang
"Đã nhận đăng nhập" → quay lại vizsup: **Đã nối: <tên kênh>**. Có 5 phút để làm; bấm **Hủy** nếu
muốn dừng.

### 1.6 Giới hạn cần biết
- **Hạn mức API:** `videos.insert` = 100 lần/ngày (nhóm hạn mức riêng *Video Uploads*); các lệnh
  khác (danh sách phát, ảnh bìa ~50 đơn vị, đọc trạng thái) dùng hạn mức mặc định 10.000 đơn
  vị/ngày — dư cho vài chục video. Hạn mức đặt lại lúc 0 giờ giờ Thái Bình Dương (~14–15 giờ
  chiều giờ Việt Nam).
- **Shorts:** video **dọc hoặc vuông, tối đa 3 phút** được YouTube tự xếp vào Shorts; vizsup
  thêm `#Shorts` vào mô tả.
- **Hẹn giờ:** vizsup tải lên ở chế độ riêng tư + `publishAt`; YouTube tự công khai đúng giờ.
- **Ảnh bìa riêng:** kênh phải **xác minh số điện thoại** (<https://www.youtube.com/verify>);
  chưa xác minh thì vizsup vẫn đăng, chỉ báo "chưa đặt được ảnh bìa".
- **Danh sách phát theo Bộ phim:** vizsup tìm danh sách phát trùng tên bộ phim, chưa có thì tạo
  (công khai) và thêm từng tập vào.

### 1.7 Video bị để "Riêng tư" & xin kiểm duyệt API (audit)
- Từ 28/07/2020 Google khóa ở chế độ riêng tư mọi video tải lên từ dự án **chưa qua kiểm duyệt
  API**. **Tài liệu `videos.insert` hiện tại (cập nhật 15/10/2026) lại ghi:** *"Videos uploaded
  from unverified API projects are not restricted to private viewing mode"* — tức là không còn
  khóa. vizsup vẫn **kiểm tra sau mỗi lần đăng**: nếu video bị để Riêng tư dù đã yêu cầu công khai
  / hẹn giờ, kết quả hiện **"riêng tư"** kèm lời nhắc mở **YouTube Studio → Nội dung** để đặt
  Công khai.
- Nếu lần nào cũng bị vậy, hoặc cần nhiều hơn 100 video/ngày: gửi **YouTube API Services — Audit
  and Quota Extension Form**: <https://support.google.com/youtube/contact/yt_api_form> (giải thích:
  [Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits)). Cần mô tả
  app, cách dùng API, video quay màn hình luồng đăng nhập/đăng video, trang chính sách quyền
  riêng tư. Google kiểm tra việc tuân thủ *YouTube API Services Terms & Developer Policies* —
  app đăng lại video của người khác dễ bị từ chối.

Nguồn: [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert) ·
[videos resource](https://developers.google.com/youtube/v3/docs/videos) ·
[resumable upload](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol) ·
[OAuth cho ứng dụng desktop](https://developers.google.com/identity/protocols/oauth2/native-app) ·
[refresh token 7 ngày](https://developers.google.com/identity/protocols/oauth2) ·
[Shorts 3 phút](https://support.google.com/youtube/answer/15424877) ·
[lịch sử thay đổi API](https://developers.google.com/youtube/v3/revision_history)

---

## 2. Facebook — Trang (video dài + Reels)

Cần: một **Trang Facebook** mà tài khoản của bạn có quyền **tạo nội dung** (quản trị viên / toàn
quyền).

### 2.1 Tạo app Meta
1. <https://developers.facebook.com/> → **My Apps → Create app**.
2. Tên `vizsup`, email liên hệ → **Use case: "Manage everything on your Page"** (Quản lý mọi thứ
   trên Trang). Không thấy mục này thì chọn **Other → Business**. Không cần chọn Business
   portfolio.
3. Trong use case đó → **Customize / Permissions** → bật (thêm) ba quyền:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`. Mức **Standard access** là
   đủ vì chỉ bạn — người có vai trò Admin trong app — dùng nó. (`publish_video` không còn nằm
   trong danh sách quyền cho video Trang.)
4. **App settings → Basic**: chép **App ID** và **App secret** (bấm *Show*). Điền **Privacy
   policy URL** (bắt buộc để chuyển Live — một trang bất kỳ của bạn mô tả công cụ cá nhân này, ví
   dụ Google Sites / GitHub Gist), chọn *Category*, lưu.
5. **Facebook Login → Settings → Valid OAuth Redirect URIs**: thêm
   `https://www.facebook.com/connect/login_success.html` (nếu Facebook báo lỗi URL chuyển hướng
   khi kết nối).

### 2.2 Development hay Live?
Khi app ở **Development**, theo báo cáo của nhiều công cụ đăng bài (chưa thấy Meta ghi trong tài
liệu hiện tại), **bài đăng qua API chỉ người có vai trò trong app nhìn thấy**. Sau khi điền đủ
mục 2.1.4: bật công tắc **App Mode → Live** ở đầu trang app. Ở Live, bạn (Admin) vẫn dùng được
các quyền Standard access mà **không cần App Review**. App Review / Advanced access / xác minh
doanh nghiệp chỉ cần khi người **khác** (không có vai trò trong app) dùng app của bạn.

### 2.3 Nối trong vizsup
Tài liệu của Meta không có cách nhận kết quả đăng nhập qua `http://localhost` cho app desktop
(theo báo cáo của nhiều người, localhost chỉ chạy khi app còn ở Development), nên vizsup dùng
cách Meta hướng dẫn cho desktop — **dán địa chỉ**:
1. **Cài đặt → Đăng video → Facebook** → dán *App ID*, *App secret* → **Kết nối**.
2. Trình duyệt mở hộp đăng nhập Facebook → chọn **các Trang** cho phép → **Tiếp tục**.
3. Facebook chuyển tới trang trắng chữ **"Success"**. **Chép toàn bộ địa chỉ** trên thanh địa chỉ
   (dạng `https://www.facebook.com/connect/login_success.html#access_token=…`) → dán vào ô vizsup
   vừa hỏi → **OK**.
4. vizsup đổi sang mã dài hạn, lấy danh sách Trang → chọn Trang → **Đã nối: <tên Trang>**.

**Cách dự phòng** (nếu bước 3 báo lỗi URL chuyển hướng): mở **Graph API Explorer**
<https://developers.facebook.com/tools/explorer/> → chọn app `vizsup` → *User or Page*: **Get User
Access Token** → tích 3 quyền ở trên → **Generate Access Token** → chép mã → dán vào vizsup ở bước 3.

vizsup chỉ lưu **mã của Trang** (không hết hạn khi lấy từ mã dài hạn). Mã mất hiệu lực nếu bạn
đổi mật khẩu Facebook, gỡ app, hoặc mất vai trò trên Trang → vizsup báo **"Kết nối lại"**.
Gỡ hẳn quyền: Facebook → Cài đặt & quyền riêng tư → Cài đặt → **Tiện ích tích hợp doanh nghiệp**
→ gỡ `vizsup`.

### 2.4 Giới hạn
- **Reels** (kind "short"): 9:16, tối thiểu 540×960, 24–60 khung hình/giây, **3–90 giây**; tối đa
  **30 Reels qua API trong 24 giờ** cho mỗi Trang (vizsup tự đếm và dừng trước). Hẹn giờ: hơn 10
  phút tới 29 ngày. vizsup đánh dấu *is_ai_generated* (giọng lồng tiếng là AI).
- **Video dài**: tải lên từng phần; hẹn giờ từ 10 phút tới 6 tháng.

Nguồn: [Reels publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing) ·
[Page /videos](https://developers.facebook.com/docs/graph-api/reference/page/videos/) ·
[đăng nhập thủ công (desktop)](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow) ·
[mã dài hạn & mã Trang](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived)

---

## 3. TikTok

### 3.1 Tạo app
1. <https://developers.tiktok.com/> → đăng nhập → **Manage apps → Connect an app**.
2. Điền tên, icon, mô tả, *Category*; **Terms of Service URL** và **Privacy Policy URL** (bắt
   buộc — trang của bạn); *Platforms*: **Desktop**.
3. **Add products**: **Login Kit** và **Content Posting API** (trong cấu hình Content Posting API
   bật **Direct Post**). Scopes: `user.info.basic`, `video.publish`, `video.upload`.
4. **Login Kit → Desktop → Redirect URI**: `http://127.0.0.1:*/callback/` (đúng từng ký tự; `*` =
   cổng bất kỳ — vizsup mở cổng ngẫu nhiên).
5. Chép **Client key** và **Client secret**.

### 3.2 Sandbox (không cần TikTok duyệt app)
App mới chưa được TikTok duyệt thì chưa đăng nhập được với tài khoản thường. Cách nhanh:
**gạt công tắc cạnh tên app sang Sandbox → Create Sandbox** → thêm sản phẩm như 3.1.3–3.1.4 →
**Sandbox settings → Target users → Add account** (đăng nhập tài khoản TikTok của bạn; tối đa 10
tài khoản; có thể mất tới 1 giờ mới hiện). Dùng *Client key / secret* hiển thị trong sandbox.
Sandbox **không** cho đăng video công khai qua Content Posting API. (Bản nháp vào hộp thư có chạy
trong sandbox hay không: tài liệu không nói — cần thử.)

Muốn dùng bản chính thức: **Submit for review** (TikTok xem trang web, chính sách, video demo) —
rồi mới tới bước kiểm duyệt Content Posting API ở 3.4.

### 3.3 Nối trong vizsup
**Cài đặt → Đăng video → TikTok** → dán *Client key*, *Client secret* → **Kết nối** → trình duyệt
mở trang TikTok → **Cho phép** → **Đã nối: <tên hiển thị>**. TikTok giữ kết nối 365 ngày (vizsup
tự làm mới mã 24 giờ).

### 3.4 Khi app CHƯA qua kiểm duyệt (audit) — đây là trường hợp của bạn
TikTok ghi trong *Content Sharing Guidelines*:
- tối đa **5 người** đăng qua app trong 24 giờ;
- tài khoản đăng **phải đang ở chế độ riêng tư** lúc đăng;
- bài đăng chỉ ở chế độ **"Chỉ mình tôi" (SELF_ONLY)**.

vizsup xử lý như sau:
- **Tài khoản đang riêng tư** → đăng thẳng ở "Chỉ mình tôi" → kết quả **"riêng tư"**. Muốn công
  khai: đổi tài khoản sang công khai, rồi trong app TikTok đổi quyền xem từng video thành
  **Mọi người**.
- **Tài khoản công khai** → TikTok từ chối đăng thẳng → vizsup **gửi video vào hộp thư TikTok**
  (bản nháp, quyền `video.upload`) → mở app TikTok, bấm thông báo, chỉnh và **đăng tay**. Kết quả
  hiện **"bản nháp"**. Tối đa 5 bản nháp chờ trong 24 giờ.
- TikTok **không hẹn giờ** được qua API: hàng đăng của vizsup gọi đúng giờ.
- Giới hạn ~15 bài/ngày/tài khoản qua Direct Post (kể cả khi đã kiểm duyệt).

Xin kiểm duyệt: trong trang app → Content Posting API → **Apply for audit**. TikTok yêu cầu app
phục vụ người sáng tạo **đăng nội dung gốc**, có giao diện hiện tên tài khoản, để người dùng **tự
chọn quyền xem (không đặt sẵn)**, hiện câu đồng ý *Music Usage Confirmation*… và **không chấp nhận
app sao chép nội dung từ nền tảng khác sang TikTok**, cũng không chấp nhận app chỉ dùng nội bộ.
Thực tế: một công cụ cá nhân đăng lại video Douyin/Bilibili **gần như chắc chắn trượt** — dùng
cách "bản nháp vào hộp thư" ở trên là đường đi ổn định.

Nguồn: [Login Kit desktop](https://developers.tiktok.com/doc/login-kit-desktop/) ·
[quản lý token](https://developers.tiktok.com/doc/oauth-user-access-token-management) ·
[Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) ·
[tải tệp theo phần](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) ·
[Upload (hộp thư)](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video) ·
[Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) ·
[Sandbox](https://developers.tiktok.com/doc/add-a-sandbox)

---

## 4. Khi có lỗi

| vizsup báo | Làm gì |
|---|---|
| "Kết nối lại" / "đã hết hạn hoặc bị gỡ" | Cài đặt → Đăng video → **Kết nối lại** (YouTube Testing: bấm *Publish app*, xem 1.3) |
| "Hết hạn mức API YouTube" | Đợi tới ~14–15 giờ chiều hôm sau; hàng đăng sẽ thử lại |
| "riêng tư" (YouTube) | Mở YouTube Studio đặt Công khai; nếu lặp lại, xin audit (1.7) |
| "App Meta thiếu quyền" | Kiểm tra 3 quyền ở 2.1.3 và bạn có quyền tạo nội dung trên Trang, rồi Kết nối lại |
| "30 Reels qua API trong 24 giờ" | Đợi; hoặc đăng dạng video thường |
| "bản nháp" (TikTok) | Mở app TikTok → hộp thư → hoàn tất đăng |
| "Mất mạng…" | vizsup chờ có mạng rồi thử lại (video tải dở được tải tiếp) |

## 5. Ghi chú cho người phát triển
`backend/app/providers/publish/{youtube,facebook,tiktok}.py` (mỗi tệp có docstring ghi các sự
thật đã kiểm và liên kết), phần chung ở `_common.py` (tệp tài khoản, PKCE, cổng loopback, client
httpx thay được bằng `httpx.MockTransport` trong test). Thuộc tính thêm trên mỗi publisher:
`auth_kind` ("loopback" / "paste"), `can_schedule` (TikTok = False), `account_hint()`,
`connect()` / `fetch_pages()`+`choose_page()` / `disconnect()`. `publish()` trả
`PostResult(ok=False, status="failed")` khi nền tảng từ chối; lỗi mạng sau khi đã thử lại thì
**ném ra** để hàng đăng chờ mạng (`app.net.run_resilient`).

---

## 6. Điều khiển qua Telegram (gửi link · duyệt · xem lịch đăng từ điện thoại)

Một **bot Telegram của riêng bạn** để không phải ngồi máy: gửi link → máy làm tới bước duyệt →
bot gửi các câu cần xem kèm nút **✅ Duyệt** / **✏️ Để sửa trên máy** → bấm Duyệt → máy tự dựng,
cắt Short, lên lịch và đăng (theo "Tự động đăng" của bộ phim) → bot báo từng bước.

### 6.1 Tạo bot (1 lần, ~2 phút)
1. Trong Telegram tìm **@BotFather** (có dấu tích xanh) → gửi `/newbot`.
2. Đặt **tên** (vd. *vizsup của Bách*) rồi **username** kết thúc bằng `bot` (vd. `vizsup_bach_bot`).
3. BotFather gửi **token** dạng `123456789:AA…` — chép cả dòng. Ai có token là điều khiển được
   bot: đừng gửi cho ai. Lộ thì gửi `/revoke` cho BotFather để lấy token mới.

vizsup không tạo tài khoản, không đăng nhập Telegram giúp bạn — chỉ dùng token bạn dán.

### 6.2 Ghép bot với vizsup
1. **Cài đặt → Đăng video → Điều khiển qua Telegram**: dán token → **Kết nối** (vizsup hỏi
   Telegram để kiểm tra token).
2. Thẻ hiện **mã 6 số** (dùng 1 lần, 30 phút). Gửi `/start <mã>` cho bot — hoặc bấm **Mở bot
   trong Telegram** (link `t.me/<bot>?start=<mã>`, chỉ cần bấm Bắt đầu).
3. Thẻ đổi sang **Đã ghép với <tên bạn>**, bot chào và gửi hướng dẫn. Từ đó bot **chỉ trả lời
   đoạn chat này**: tin từ người khác (kể cả trong nhóm) bị bỏ qua, không trả lời gì. Nhập sai mã
   5 lần → mã hủy, bấm **Lấy mã ghép mới**.
4. **Báo cho bạn:** chọn loại tin muốn nhận — *Tập chờ duyệt*, *Đã lên lịch đăng*, *Đã đăng*,
   *Lỗi*. Trả lời cho link bạn gửi (đã nhận, đã tải, đang dịch, lỗi) luôn được gửi.
5. **Gỡ kết nối** xóa token + đoạn chat khỏi máy; bot không trả lời nữa.

### 6.3 Bot làm được gì
| Bạn gửi | vizsup làm |
|---|---|
| Link Douyin / Bilibili, hoặc dán nguyên đoạn chia sẻ "长按复制… https://v.douyin.com/… 打开抖音" | Chạy tới bước duyệt như link dán trong app (bộ phim tự nhận từ 《…》), báo: đã nhận → đã tải → đang dịch → **chờ duyệt** (hoặc lỗi + lý do). Link đã có trên máy: gửi lại tin duyệt / báo đã làm |
| (tập tới bước duyệt — cả tập do "Theo dõi tập mới" tìm được) | Tin **Chờ duyệt**: tiêu đề, số câu, tối đa 40 câu bị đánh dấu (lúc, tiếng Trung, tiếng Việt, vì sao), "Sau khi duyệt: …" như hộp *Duyệt nhanh*; nút ✅ Duyệt / ✏️ Để sửa trên máy |
| ✅ Duyệt | Duyệt **đúng bản phụ đề trong tin đó**, rồi Tự động đăng: dựng → "Đã lên lịch đăng: YouTube lúc 19:00 …" → "✅ YouTube: đã đăng <link>" / "❌ … lỗi" |
| ✏️ Để sửa trên máy | Không duyệt; tập chờ bạn mở Thư viện sửa |
| `/trangthai` (`/status`) | Tập chờ duyệt, hàng dựng, bài sắp đăng trong 24 giờ, 5 bài vừa đăng (có link), bài lỗi |
| `/help` | Hướng dẫn |

**Duyệt vẫn là việc của bạn.** Bot không bao giờ tự duyệt. Nút ✅ mang mã dự án + mã phiên bản
phụ đề lúc gửi tin; bấm ✅ bị **từ chối (kèm lý do)** khi: phụ đề đã đổi sau khi gửi tin (bot gửi
bản mới để bạn đọc lại), tập đã duyệt rồi, tập đang xử lý / đang dựng, hoặc tập đang mở trên máy
và bản đang sửa dở khác bản trong tin (app lưu bản đang sửa trước rồi mới so).

### 6.4 Khi app đóng
- **App mở** (khuyên dùng — để ở khay hệ thống): app đọc tin nhắn liên tục, trả lời trong vài
  giây, dựng trong app như thường.
- **App đóng**: tác vụ nền Windows (`vizsup autopilot --once`, xem Bộ phim → Theo dõi → *Bật chạy
  nền*) đọc tin mỗi lần chạy: link → tải + dịch → gửi tin duyệt; ✅ Duyệt → **duyệt rồi dựng ngay
  trong tác vụ nền** (không cần mở app) theo cài đặt dựng của bộ phim, cắt Short (chỉ tự duyệt
  Short khi mọi câu của nó là câu đã duyệt, như trong app), lên lịch và đăng đúng giờ. Bộ phim
  chưa bật Tự động đăng / chưa từng dựng tập nào: bot báo "đã duyệt — mở app để dựng".
- Sau khi ghép bot, bấm **Chạy nền mỗi 15 phút** (thẻ Telegram) hoặc *Đặt lại lịch* ở Bộ phim →
  Theo dõi: tác vụ chạy mỗi 15 phút để trả lời kịp; tập mới của từng bộ phim vẫn chỉ kiểm tra
  theo lịch riêng (mỗi 6 giờ …). Lịch chỉ tạo/đổi khi **bạn bấm**. Chưa đặt lại thì tác vụ vẫn
  chạy theo giờ cũ — Telegram giữ tin chờ tối đa **24 giờ**, quá thì tin bị mất.
- Chỉ một nơi đọc tin của bot một lúc: app mở thì tác vụ nền chỉ gửi báo, không đọc.
- Máy phải bật và bạn đã đăng nhập Windows (tác vụ nền chạy khi bạn đăng nhập).

### 6.5 Riêng tư
- Token, mã đoạn chat và vị trí đọc tin (offset) chỉ nằm trong `storage/_accounts/telegram.json`
  trên máy này (thư mục `storage/` không lên git); Cài đặt chỉ hiện 4 ký tự cuối. Log không bao
  giờ có token.
- vizsup chỉ gọi `https://api.telegram.org` (long polling — không webhook, máy sau router vẫn chạy).
- Nội dung đi qua máy chủ Telegram: tiêu đề tập, các câu phụ đề bị đánh dấu, link bài đã đăng.
  Không gửi video, không gửi khóa API. Chat với bot **không** mã hóa đầu-cuối (Telegram không có
  Secret Chat cho bot).

### 6.6 Ghi chú cho người phát triển
`backend/app/remote/telegram.py` (Bot API qua httpx: getMe, getUpdates, sendMessage,
answerCallbackQuery, editMessageReplyMarkup; ghép mã; tệp tài khoản; `TelegramChannel.transport`
thay bằng `httpx.MockTransport` trong test), `backend/app/remote/__init__.py` (giao diện kênh:
thêm Zalo/Google Sheet = một module cùng các hàm), `backend/app/pipeline/remote.py` (ý nghĩa tin
nhắn, chốt duyệt theo revision, khóa "một nơi đọc" `storage/_remote/poll.json`, trạng thái tin
duyệt đã gửi `storage/_remote/state.json`, `CliSession` cho tác vụ nền), `desktop/remote_bridge.py`
(app mở: poll trên luồng nền, xử lý trên luồng GUI, gửi qua `remote.Outbox`). Dựng không cần app:
`backend/app/pipeline/render.py` (thân dựng chung với `desktop/worker.py`, tự kiểm tra duyệt).
Sự thật đã kiểm (core.telegram.org/bots/api, 31/10/2026): tin ≤ 4096 ký tự, `callback_data`
1–64 byte, `answerCallbackQuery` ≤ 200 ký tự, update giữ ≤ 24 giờ, đang có webhook thì
getUpdates không chạy (vizsup gọi `deleteWebhook` khi Kết nối), hai getUpdates cùng lúc → 409.
