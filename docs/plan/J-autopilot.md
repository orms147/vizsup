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

## Flow
```
watch (6 h) ─┐
paste link ──┤
Telegram link┴→ run to gate (series auto) → "Chờ duyệt" queue → 1-click approve (app / bot ✅)
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
| Telegram remote ("Điều khiển qua Telegram"): link in, «Duyệt nhanh» + [✅ Duyệt] out (revision-guarded), progress / plan / post reports, `/trangthai`; app open = `desktop/remote_bridge.py`, app closed = `autopilot --once` (headless render via `pipeline/render.py`), task every 15 min once paired | `remote/telegram.py`, `pipeline/remote.py`, `pipeline/quickreview.py`, J-publish-setup.md §6 | agent T |

State: series profile `autopilot` block (watch sources, interval, platforms, slots, auto Short);
`storage/_autopilot/queue.json` (episode → stage, post jobs with platform/slot/result). Accounts:
`storage/_accounts/<platform>.json` (gitignored with storage/; never printed).

Telegram (2026-10-31, J-publish-setup.md §6): the bot is the owner's own (@BotFather token,
paired by a one-time code; every other chat ignored). The gate stays the owner's click — the
✅ button carries the job id + `approval.revision()[:8]`; a changed revision, an approved
project, a running job or the open editor's unsaved edits refuse it. App closed: the scheduled
task answers, renders an approved episode headless and plans its posts; one process reads the
bot at a time (`storage/_remote/poll.json`).
