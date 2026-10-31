# Tạo và giữ kênh (YouTube · TikTok · Facebook) — lưu ý cho chủ kênh

Tra cứu ngày 2026-10-31 trên trang chính thức của từng nền tảng (link ở mỗi mục). Chính sách đổi
thường xuyên — mục nào quan trọng thì mở link kiểm tra lại trước khi dựa vào.
"(chưa xác minh)" = chỉ thấy ở nguồn phụ, không tìm được trang chính thức.

---

## 0. Tóm tắt 1 phút

1. **"Hội nhóm report hàng loạt" KHÔNG tự xóa được video đúng luật trên YouTube.** YouTube
   nói rõ: báo cáo không tự gỡ video; nếu đội kiểm duyệt không thấy vi phạm thì "báo cáo bao
   nhiêu cũng không thay đổi" ([2802027](https://support.google.com/youtube/answer/2802027)).
   TikTok cũng từng nói report hàng loạt không làm tăng khả năng bị gỡ (chưa xác minh trên trang
   hiện tại).
2. **Thứ thật sự làm chết kênh ở VN:**
   - **Gậy bản quyền** (thật hoặc giả — có nơi tống tiền bằng gậy, có cả "dịch vụ rip kênh").
     3 gậy trong 90 ngày = mất kênh.
   - **Bị hack** qua email "tài trợ" giả, phần mềm giả.
   - **Luật nội dung tái sử dụng / spam** cho kênh đăng lại video người khác.
3. **Loại nội dung của mình là rủi ro gốc:** dịch + lồng tiếng video của người khác.
   - Luật VN: bản dịch là tác phẩm phái sinh, cần phép của tác giả.
   - **Chưa nền tảng nào ghi rằng dịch hay lồng tiếng là "đủ biến đổi".** Facebook còn liệt kê
     "chèn phụ đề" là thay đổi giá trị thấp.
   - ⇒ **Việc bảo vệ kênh tốt nhất: xin phép tác giả gốc (阿熊动漫…), giữ bằng chứng.** Có phép
     thì gậy giả đánh được lại. Không có phép thì gậy của chính chủ là hợp lệ, không cãi được.
4. **Bảo mật tài khoản quan trọng ngang bản quyền:**
   - passkey / khóa bảo mật thay SMS;
   - không mở file "tài trợ";
   - tách trình duyệt riêng cho kênh.
5. **Kiếm tiền:** YouTube nói xin phép **không** gỡ được lỗi "nội dung tái sử dụng" khi xét bật
   kiếm tiền. Hãy coi kênh dịch là kênh xây khán giả trước; tiền là phần thưởng không chắc chắn.

---

## 1. Trước khi đăng video đầu tiên (làm 1 lần)

### 1.1 Tài khoản & bảo mật — *phần lớn kênh VN "chết" vì bị hack, không phải vì report*

- [ ] **Tài khoản Google riêng cho kênh.** Không dùng chung với tài khoản cá nhân, không đăng
      nhập lung tung.
- [ ] **Bảo mật đăng nhập:** bật xác minh 2 bước bằng **passkey hoặc khóa bảo mật**. Không dùng
      SMS — SMS bị vượt qua được khi trộm cookie. YouTube: "Passkeys provide the strongest
      protection against phishing"
      ([9701986](https://support.google.com/youtube/answer/9701986)).
      - Kênh lớn: bật **Advanced Protection**
        ([link](https://landing.google.com/advancedprotection/)).
      - In **mã dự phòng**, cất giấy.
- [ ] **Chiêu hack phổ biến nhất:** email mời tài trợ / hợp tác, kèm file `.rar`, `.exe`, `.scr`,
      "bản dùng thử phần mềm", link tải game.
      - Mở là mã độc **trộm cookie đăng nhập**, vượt qua cả 2FA bằng SMS. Google đã ghi nhận đúng
        chiêu này nhắm vào YouTuber
        ([TAG 2021](https://blog.google/threat-analysis-group/phishing-campaign-targets-youtube-creators-cookie-theft-malware/)).
      - **Không bao giờ chạy file từ người lạ.**
- [ ] **Tách nơi làm kênh:** dùng **một hồ sơ Chrome riêng** (hoặc một user Windows riêng) chỉ
      để làm kênh, không cài tiện ích lạ trong hồ sơ đó.
- [ ] **Không đưa mật khẩu cho ai.** Cần người giúp thì thêm họ qua **quyền kênh**
      (Người quản lý / Biên tập viên) —
      [9481328](https://support.google.com/youtube/answer/9481328). Người quản lý không xóa
      được kênh.
- [ ] **Facebook:**
      - bật 2FA cho **mọi** quản trị viên Trang; ít người có toàn quyền thôi;
      - thông báo kiểu "Trang của bạn sẽ bị vô hiệu hóa / vi phạm bản quyền, bấm vào đây kháng
        nghị" qua Messenger hay email là **lừa đảo phổ biến ở VN**
        ([lsvn](https://lsvn.vn/canh-giac-voi-thu-doan-gia-mao-doi-ngu-an-ninh-cua-meta-de-lua-dao-chu-tai-khoan-facebook-a150428.html));
      - chỉ tin thông báo nằm trong **Hộp thư hỗ trợ** của chính app Facebook.
- [ ] **Riêng với vizsup:**
      - `storage/_accounts/` chứa chìa khóa đăng bài của kênh (YouTube có quyền quản lý kênh,
        Facebook, TikTok, bot Telegram).
      - Không chia sẻ thư mục `storage`, không đồng bộ nó lên Drive công khai.
      - Nghi máy bị nhiễm thì vào [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
        gỡ quyền app, rồi nối lại.
- [ ] **Bị hack thì làm gì:** khôi phục tài khoản Google trước, rồi hoàn tác thay đổi trên kênh.
      YouTube hỗ trợ các vụ trong 9 tháng gần nhất
      ([76187](https://support.google.com/youtube/answer/76187)).

### 1.2 Giấy tờ & pháp lý VN

- [ ] **Nghị định 147/2024** (hiệu lực 25/12/2024): tài khoản mạng xã hội phải xác thực bằng số
      điện thoại VN (hoặc số định danh cá nhân) mới được đăng, bình luận, livestream
      ([luatvietnam](https://luatvietnam.vn/legal-updates/social-network-user-accounts-to-be-verified-with-subscriber-phone-numbers-892-100003-article.html)).
      Làm theo lời nhắc của từng nền tảng, dùng **số chính chủ**.
- [ ] **YouTube:** xác minh số điện thoại cho kênh để mở video **dài hơn 15 phút** (cần cho video
      "Ghép tập") và ảnh thu nhỏ tự chọn.
- [ ] **Thuế khi có thu nhập:**
      - Từ 1/1/2026 bỏ thuế khoán. Doanh thu ≤ 1 tỷ/năm không phải nộp VAT và TNCN nhưng vẫn
        phải kê khai
        ([lsvn 09/2026](https://lsvn.vn/nhung-thay-doi-quan-trong-ve-thue-ho-ca-nhan-kinh-doanh-can-biet-a178824.html)).
      - Tiền YouTube / TikTok không bị khấu trừ tại VN ⇒ tự kê khai (nguồn phụ, chưa xác minh).
      - **Thuế Mỹ trên YouTube:** nộp W-8BEN trong AdSense.
        - VN–Mỹ chưa có hiệp định thuế có hiệu lực ⇒ bị giữ **30% phần doanh thu từ người xem
          ở Mỹ**.
        - Không nộp thông tin thuế thì bị giữ **24% toàn bộ** doanh thu
          ([10391362](https://support.google.com/youtube/answer/10391362)).
- [ ] **MCN / "network":** đọc kỹ hợp đồng trước khi ký. Báo chí VN mô tả có MCN dùng công cụ
      bản quyền để "cưỡng đoạt có hệ thống"
      ([VnExpress 05/2026](https://vnexpress.net/lo-hong-danh-gay-ban-quyen-tren-nen-tang-online-5075114.html)).

### 1.3 Xin phép tác giả gốc — việc bảo vệ kênh mạnh nhất

Vì sao cần:
- Luật SHTT VN: làm bản dịch là tạo tác phẩm phái sinh, cần phép (và trả thù lao nếu có) của chủ
  sở hữu quyền tác giả ([gvlawyers](https://gvlawyers.com.vn/tac-pham-dich)).
- Có phép thì:
  - **YouTube:** tranh chấp Content ID bằng lý do "có đủ quyền", và gửi **thông báo phản đối**
    khi bị gậy giả ([2797454](https://support.google.com/youtube/answer/2797454)).
  - **Facebook:** trường hợp được chấp nhận theo Meta (07/2025) là "có phép + có cải tiến đáng
    kể như thuyết minh"
    ([blog Meta](https://creators.facebook.com/blog/combating-unoriginal-content)).
- Không có phép mà chính tác giả báo bản quyền thì gậy đó hợp lệ. Gửi phản đối sai sự thật là
  khai gian có tuyên thệ — **đừng làm**.

Cách làm: nhắn riêng cho tác giả trên Douyin / Bilibili. Mẫu tin nhắn (sửa tên phim / nền tảng):

> 您好！我是越南的观众，非常喜欢您的作品《末日校车》。想申请把这部作品翻译成越南语（越南语字幕 + 配音），
> 发布在 YouTube、TikTok 和 Facebook 上。每个视频都会注明原作者和原视频链接，并保留您的水印。
> 请问您是否同意授权？如果有任何条件（例如只限某些平台、收益分成），我都愿意遵守。
> 如果同意，麻烦回复一句"同意授权翻译并发布在 YouTube、TikTok、Facebook"，方便我在平台上证明授权。谢谢！

Sau đó:
- Chụp màn hình cuộc trò chuyện (thấy rõ tên tài khoản, ngày), lưu cả bản gốc lẫn bản dịch.
- Ghi vào **Ghi chú quyền** của dự án trong vizsup (`rights.json`). Lúc đăng, app nhắc khi
  quyền còn "chưa rõ".
- Tác giả từ chối hoặc không trả lời: biết là mình đang chịu rủi ro. Nên chọn bộ khác, hoặc đăng
  ít và chấp nhận có thể mất video.
- Hiện không thấy Douyin / Bilibili có chương trình cấp phép chính thức cho việc đăng lại ra
  nước ngoài (chưa xác minh là không có).

### 1.4 Thiết lập kênh

- [ ] **Mô tả kênh nói thật:** "Bản dịch & lồng tiếng tiếng Việt của các phim hoạt hình Trung
      Quốc. Phim gốc: 《…》 của <tác giả> (link). Đã được tác giả cho phép." (bỏ câu cuối nếu
      chưa có phép).
- [ ] **Mỗi video ghi nguồn + link gốc.** Dialog đăng bài của vizsup đã gợi ý dòng "Nguồn".
      Lưu ý: "ghi nguồn" **không** phải lý do hợp lệ khi tranh chấp bản quyền — chỉ là phép lịch
      sự.
- [ ] **Đối tượng: "Không dành cho trẻ em".** Hoạt hình không tự động là nội dung cho trẻ em
      ([9528076](https://support.google.com/youtube/answer/9528076)).
      - vizsup đặt sẵn `selfDeclaredMadeForKids = false` cho YouTube — đúng với 《末日校车》.
      - Phim nào thật sự dành cho trẻ nhỏ thì phải đổi (đánh dấu sai có thể bị phạt).
      - **Không dùng tiêu đề / thẻ / ảnh nhắm trẻ em cho phim có bạo lực, xác sống**: YouTube gỡ
        "hoạt hình gia đình nhắm trẻ nhỏ nhưng có bạo lực, chết chóc"
        ([2801999](https://support.google.com/youtube/answer/2801999)).
- [ ] **Nhãn AI:**
      - YouTube chỉ bắt khai nội dung AI **trông như thật** (người thật, cảnh thật). Hoạt hình
        lồng giọng AI có sẵn thì theo cách đọc chính sách là **không bắt buộc** (suy luận —
        chính sách không nêu đích danh trường hợp này)
        ([14328491](https://support.google.com/youtube/answer/14328491)).
      - TikTok bắt gắn nhãn nội dung AI "trông như thật"
        ([TikTok](https://www.tiktok.com/community-guidelines/en/integrity-authenticity)).
      - Nên bật ô **"Nhãn “Lồng tiếng AI” (5 giây đầu)"** ở trang Dựng: minh bạch, không tốn gì.

---

## 2. Khi đăng — nhịp và cách làm để không dính "spam / tái sử dụng"

Chính sách spam của YouTube ([2801973](https://support.google.com/youtube/answer/2801973))
hiện nêu đích danh 3 điều:
- **Đăng lại nội dung lấy từ nơi khác "mà không thêm gì của mình"** — ví dụ kênh đăng lại nguyên
  clip phổ biến, không biên tập biến đổi.
- **"Dùng công cụ tự động hoặc AI để sản xuất hàng loạt nội dung giống nhau, thay đổi rất ít."**
- **Lật ngược hình (mirror) để né phát hiện.** vizsup **không bao giờ** làm việc này; đừng làm
  bằng tay.

Luật YPP ([1311392](https://support.google.com/youtube/answer/1311392)):
- **Được phép:** "Một cảnh phim mà bạn đã **viết lại lời thoại và thay giọng đọc**"; "một bộ
  phim theo cùng nhóm nhân vật qua các tập, mỗi tập có cốt truyện riêng".
- **Không được:** tải về và đăng lại không biến đổi đáng kể; nội dung nhiều kênh khác đã đăng;
  "quảng bá nội dung của người khác (**kể cả khi có phép**)".

⇒ Cách làm hợp lý với vizsup:

- [ ] **Bản dịch phải là "viết lại lời thoại" thật:** câu tiếng Việt tự nhiên, xưng hô nhất
      quán, không dịch word-by-word.
      - App giờ tự học xưng hô từ tập đã duyệt để cả bộ thống nhất (`pipeline/cast.py`).
      - Bước duyệt của bạn chính là phần "con người biên tập".
- [ ] **Thêm phần của riêng mình:**
      - lời bình / tóm tắt tập trước bằng tab **"Bình luận"** trong màn sửa (giọng khác hoặc giọng
        bạn thu);
      - thẻ "Tập N", màn "Còn tiếp";
      - ảnh thu nhỏ và tiêu đề tự làm.
- [ ] **Nhịp đăng:** kênh mới nên 1 video/ngày/nền tảng (+ 1 Short), đều đặn, giờ cố định. Đặt
      "số bài mỗi ngày" = 1 trong kế hoạch Tự động của bộ phim
      (mặc định đã là 1).
      - Một bộ có cốt truyện riêng từng tập là ví dụ **được phép**.
      - Cái bị cấm là sản xuất hàng loạt thay đổi rất ít, không phải số lượng.
- [ ] **Không đăng cùng một tập lên nhiều kênh của mình**, không làm nhiều kênh "vệ tinh" chung
      nội dung.
- [ ] **Không mua view / sub / like, không vào nhóm "sub chéo"** — vi phạm chính sách tương tác
      giả, có thể bị gỡ video hoặc xóa kênh.
- [ ] **Đăng thử ở chế độ riêng tư / hẹn giờ trước:** YouTube Studio chạy "Kiểm tra" bản quyền
      lúc tải lên. Thấy khiếu nại bản quyền thì đừng công khai video đó. Bài vizsup hẹn giờ
      được tải lên ở chế độ riêng tư, tới giờ mới công khai — vẫn kịp xem Studio báo gì.

**TikTok:**
- Nội dung "không nguyên bản / tái sử dụng mà không thêm gì mới" **không được lên For You**
  ([TikTok 09/2026](https://www.tiktok.com/community-guidelines/en/integrity-authenticity)).
- Creator Rewards đòi nội dung gốc (nguồn phụ). ⇒ Với kênh dịch, TikTok là chỗ **kéo người xem**
  về YouTube / Facebook, đừng kỳ vọng tiền.

**Facebook:**
- Meta (03/2026) coi "thêm viền, đổi tốc độ, **chèn phụ đề**" là thay đổi giá trị thấp. Nội dung
  không nguyên bản bị giảm phân phối
  ([blog Meta 03/2026](https://creators.facebook.com/blog/supporting-original-content-on-facebook)).
- Thuyết minh + có phép là trường hợp Meta 2025 chấp nhận.
- Theo dõi **Trạng thái tài khoản / Chất lượng Trang** trong Bảng điều khiển chuyên nghiệp;
  quyết định về tính nguyên gốc kháng nghị được.

---

## 3. Khi bị "đánh gậy" / báo cáo — làm gì

### 3.1 YouTube — hiểu luật chơi trước

**Vi phạm Nguyên tắc cộng đồng** ([2802032](https://support.google.com/youtube/answer/2802032)):
- **Lần đầu thường chỉ là cảnh báo.** Làm khóa đào tạo chính sách thì cảnh báo hết hạn sau
  90 ngày.
- Gậy 1: khóa đăng 1 tuần. Gậy 2 trong 90 ngày: khóa 2 tuần. **3 gậy trong 90 ngày: có thể bị
  xóa kênh.**
- Vi phạm nặng (spam, khiêu dâm…) có thể bị xóa ngay.
- **Kháng nghị** cảnh báo / gậy trong 6 tháng, mỗi gậy 1 lần, kháng nghị thất bại không bị phạt
  thêm ([185111](https://support.google.com/youtube/answer/185111)).

**Gậy bản quyền** ([2814000](https://support.google.com/youtube/answer/2814000)):
- Hết hạn sau 90 ngày nếu làm xong **Trường học bản quyền**. 3 gậy = xóa kênh, **và các kênh liên
  kết có thể bị xóa theo**.
- Một số khiếu nại cho **7 ngày tự xóa video để khỏi bị gậy**: nếu không có phép, xóa ngay là lựa
  chọn khôn ngoan. Ngoài trường hợp đó, xóa video **không** gỡ được gậy.

**Content ID claim ≠ gậy** ([2797454](https://support.google.com/youtube/answer/2797454)):
- Claim thường chỉ chuyển tiền quảng cáo hoặc chặn ở vài nước. Nhưng **tranh chấp claim không có
  căn cứ** có thể làm bên kia chuyển thành yêu cầu gỡ = **gậy**.
- Chỉ tranh chấp khi có phép, hoặc claim nhầm (ví dụ ai đó claim phần lồng tiếng / nhạc nền của
  chính mình).

**Kênh bị xóa** ([2802168](https://support.google.com/youtube/answer/2802168)):
- Kháng nghị trong 1 năm, tối đa 2 lần.
- **Không được lập kênh mới** khi bị xóa (luật "lách"). Lập kênh mới sẽ bị xóa tiếp, nên phải
  phòng từ đầu.

### 3.2 Gậy giả / tống tiền

Ở VN có các vụ claim để đòi tiền: một vụ đòi $50 Bitcoin, kiểu "ăn được thì ăn, không ăn được thì
nhả" ([Tuổi Trẻ](https://cuoituan.tuoitre.vn/gay-ban-quyen-trao-tay-ke-nhan-vo-1620091.htm)).
Theo bài báo: **hơn 60% gậy bị phản đối đã được gỡ, nhưng chưa tới 1% người bị gậy dám gửi
phản đối.**

1. **Không trả tiền.** Không nhắn qua kênh bên claim đưa ra.
2. Kiểm tra: bên claim có phải tác giả gốc / đơn vị được tác giả ủy quyền không?
3. **Có phép của tác giả** → gửi **thông báo phản đối** trong YouTube Studio
   ([2807684](https://support.google.com/youtube/answer/2807684)):
   - Phải ghi họ tên thật, địa chỉ, số điện thoại. **YouTube buộc phải gửi các thông tin này cho
     bên claim.**
   - Có thể nhờ luật sư / người đại diện gửi thay.
   - Bên claim có 10 ngày làm việc (giờ Mỹ) để chứng minh đã kiện. Không kiện thì video được khôi
     phục.
4. Bên claim lạm dụng lặp lại → gửi lý do "yêu cầu gỡ là lạm dụng / sai" kèm bằng chứng.
   - YouTube nói mỗi năm họ xóa hàng chục nghìn tài khoản lạm dụng công cụ bản quyền
     ([15575746](https://support.google.com/youtube/answer/15575746)).
   - Họ cũng nói YouTube **không phân xử ai là chủ** — giấy phép của bạn là thứ quyết định.
5. **Lưu hồ sơ mọi thứ:**
   - ảnh chụp giấy phép;
   - thư mục dự án vizsup (`cn.srt`, `vi.srt`, thời điểm duyệt `approved.json`, tệp xuất) — bằng
     chứng bạn tự dịch, tự lồng tiếng;
   - email của YouTube.
6. **Đừng lách:** không đăng lại video đã bị gỡ, không lật hình, không đổi tốc độ để né Content ID
   — đó là vi phạm spam riêng.

### 3.3 Report hàng loạt (Nguyên tắc cộng đồng)

- **YouTube:** video bị report được người thật xem lại. Không vi phạm thì giữ nguyên, report nhiều
  cũng vậy.
  - Cái bạn kiểm soát được: không để video có điểm yếu thật — tiêu đề giật tít sai sự thật, ảnh
    thu nhỏ gây hiểu lầm, nội dung bạo lực ghi "cho trẻ em", spam thẻ.
  - Bị gỡ oan → kháng nghị (miễn phí, không bị phạt thêm).
- **TikTok:** kiểm duyệt máy trước rồi người. Gậy hết hạn sau 90 ngày
  ([TikTok 2023](https://newsroom.tiktok.com/supporting-creators-with-an-updated-account-enforcement-system?lang=en)).
  Kháng nghị và theo dõi trong **Trung tâm an toàn** của app. Lưu ý: kháng nghị bản quyền trên
  TikTok sẽ **gửi cả thông tin liên hệ của bạn** cho bên báo cáo
  ([TikTok](https://www.tiktok.com/legal/page/global/copyright-policy/en)).
- **Facebook:** 1 gậy = cảnh báo; 2–6 gậy hạn chế tính năng; từ gậy 7 khóa đăng 1 → 3 → 7 →
  30 ngày ([Meta](https://transparency.meta.com/enforcement/taking-action/restricting-accounts/)).
  - Trang có thể bị loại khỏi đề xuất và mất kiếm tiền.
  - Xem tại **Trạng thái tài khoản** / **Chất lượng Trang**.
  - Phản đối bản quyền trên Meta cũng chuyển thông tin liên hệ của bạn cho bên báo cáo.

---

## 4. Duy trì kênh — việc hằng tuần (5 phút)

- [ ] YouTube Studio → **Bản quyền** & **Trạng thái kênh**: có claim / cảnh báo mới không?
- [ ] Facebook → **Chất lượng Trang**; TikTok → **Trạng thái tài khoản**.
- [ ] Kiểm tra email **chỉ trong Studio / Hộp thư hỗ trợ**, không tin email lạ.
- [ ] Bot Telegram của vizsup báo kết quả từng bài đăng. Bài nào "không đăng được" hoặc bị để
      "riêng tư" thì mở Studio xem lý do.
- [ ] Mỗi 3 tháng: rà quyền truy cập kênh (ai đang có quyền), gỡ app lạ ở
      [myaccount.google.com/permissions](https://myaccount.google.com/permissions), đổi mật khẩu
      nếu nghi ngờ.

---

## 5. Ghi chú cho người phát triển

- YouTube upload: `selfDeclaredMadeForKids = false` (cứng); `status.containsSyntheticMedia` để
  trống. Hoạt hình + giọng AI có sẵn không "trông như thật" ⇒ không cần khai.
  - Thêm tùy chọn theo bộ phim nếu sau này làm nội dung cho trẻ em, hoặc lồng tiếng người thật.
- Scope YouTube gồm `youtube` (để làm playlist) ⇒ `storage/_accounts/youtube.json` đủ quyền quản
  lý kênh. Không bao giờ in ra, không commit (`storage/` đã gitignore).
- Nhịp "kênh mới" chưa có bộ hãm riêng; nhịp do kế hoạch Tự động của bộ phim quyết định.
