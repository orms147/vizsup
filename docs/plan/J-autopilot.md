# J — Autopilot: dán link (hoặc tự theo dõi) → duyệt 1 bấm → tự dựng → tự đăng

Owner, 2026-10-31: "luồng mình chỉ cần làm là: up link → đăng video, không cần chỉnh nhiều; nếu làm
được luồng tự lấy video đăng định kỳ thì tốt". Decisions (AskUserQuestion, same day):
- **Duyệt nhanh 1 bấm** — the human gate STAYS (CLAUDE.md hard constraint): the app prepares
  everything; the owner sees only flagged lines and approves with one click. No auto-approval.
- **Đăng tự động: YouTube (long + Shorts), TikTok, Facebook (Page/Reels)** via official APIs; the
  owner links each account once (OAuth in the browser — Claude never enters passwords).
- **Theo dõi tập mới mỗi 6 giờ** (Douyin / Bilibili creator page, filtered by the series' 《…》).

Platform facts (checked 2026-10-31, re-check before relying on them):
- YouTube Data API: the videos.insert reference (updated 2026-10-15) now says uploads from
  unverified projects are NOT restricted to private; 100 uploads/day ("Video Uploads" bucket).
  The publisher still reads the status back and reports "private" if it ever happens. (Earlier
  third-party summaries repeated the 2020 private-lock rule — superseded.) Consent screen in
  Testing mode → refresh tokens expire in 7 days: press "Publish app" (J-publish-setup.md).
- TikTok Content Posting API: an unaudited client can post only to a **private account**
  (SELF_ONLY); public posting needs TikTok's audit.
- Facebook Page Reels (`/{page-id}/video_reels`, rupload): public by nature, 30 API posts / 24 h.
- Reposting other creators' videos is the risky part of any audit and of the platforms'
  reused-content rules (TikTok's guidelines: "Not acceptable: an app that copies arbitrary
  contents from other platforms to TikTok"); the rights note (`rights.json`) stays visible.
- A platform that is not connected still gets its post as a package in `storage/_posts`.

## Watch sources — re-verified 2026-10-31 (real pages, read-only)
- Douyin creator page 阿熊动漫: 30–36 posts read in 7–10 s without login. **《末日校车》09 (public,
  2026-10-25) is NOT on the creator page** — neither in its cards nor in the post-list answers —
  but it is in the series' 合集. Simulated "library stops at 8": the creator page would fetch
  nothing, the 合集 fetches 09. → new source kind: Douyin 合集 (`collection/<mix_id>` or any
  episode link; `douyin_capture --mix` asks the page's own `aweme/v1/web/mix/aweme`, 20 per
  answer by cursor; 7 episodes in 9–12 s, 《暴击契约》 28 episodes in 2 answers, a video in no
  合集 → a clear error). Theo dõi pre-fills the newest downloaded episode's link and
  «Kiểm tra ngay» swaps an episode link for its 合集 link.
- A video page sometimes never reports loadFinished (its stream keeps loading) and twice answered
  "This site can't be reached" while curl got 200: mix mode starts once the page's own detail
  answer is seen; a failed check is simply retried at the next one.
- Bilibili: a season list (panfood, 125 items) in 1.7 s; the space API answered 412 right after a
  burst of my API calls and was fine minutes later (2.5 s) → one automatic retry after 20 s.
  Titles like 【AI漫剧】《暖家奇遇第二部》 are whole seasons: a number inside 《…》 is not an
  episode; 《萧族降世》P1-4 is now a 1–4 compilation (skipped), not episode 1.

## Flow
```
watch (6 h) ─┐
paste link ──┴→ run to gate (series auto) → "Chờ duyệt" queue → 1-click approve
   → auto render (long; + Short if the series says so) → post queue (per platform slots)
   → publish at the slot (connected platforms) / export a post package (others) → done
```

## Pieces
| Piece | Module | Owner |
|---|---|---|
| Publisher interface + "Gói đăng" folder publisher | `providers/base.py` PublishProvider, `providers/publish/folder.py`, `registry.PUBLISHERS` | coordinator (done) |
| YouTube / Facebook / TikTok publishers + OAuth + Settings "Tài khoản đăng" + setup guide | `providers/publish/*.py`, `docs/plan/J-publish-setup.md` | agent P |
| Watcher (Douyin user page via the embedded browser, Bilibili space/season) + CLI `autopilot --once` | `pipeline/watch.py`, `douyin_capture.py` list mode | agent W |
| Autopilot state, queue, schedule; fast review; auto render; post worker | `pipeline/autopilot.py`, desktop | coordinator |

State: series profile `autopilot` block (watch sources, interval, platforms, slots, auto Short);
`storage/_autopilot/queue.json` (episode → stage, post jobs with platform/slot/result). Accounts:
`storage/_accounts/<platform>.json` (gitignored with storage/; never printed).
