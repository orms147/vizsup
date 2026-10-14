# F — What to build next, from viewer / creator / platform research (2026-10-14)

Three research passes (Haiku 5.5 subagents, web only) on: Vietnamese viewers, the people who
make translated Chinese videos and the tools they compare against, and the platform / legal
rules for translated, AI-voiced reposts. Key claims below were re-opened by hand where marked
✓. Confidence: **H**igh / **M**edium / **L**ow. Everything here is a proposal — nothing built yet.

## 1. What the research says

### Viewers (Vietnam)
- **No Vietnam-specific data** on dub vs subtitles vs *thuyết minh*, AI-voice acceptance or
  sound-off viewing was findable (H that it isn't public). Only qualitative signals: distributors
  say a subtitle-loyal audience remains while dubbed audiences grow (Việt Nam News, 2025-05, L);
  a 2017 VTV regional dub of a tense drama drew "phần lớn khán giả khó chịu" — dubbing *removed*
  the tension (VTC, 2017, M). → choose audio mode per video type; measure your own retention.
- **Readability standard** — Netflix Vietnamese Timed Text Style Guide (changelog 2025-12): **≤ 42
  characters/line, ≤ 2 lines, ≤ 17 characters/second** for adults (13 for children) (H). Phones
  are the worst screen for subtitles (UAB study 2021, M).
- **Viewers check translations against the audio** and call out mismatches publicly (Tuổi Trẻ
  2024, TikTok case, M) → accuracy is a feature.
- **Short drama is large in VN**: app charts led by PineDrama / DramaBox / NetShort; episodes
  1–3 min; SEA ~40 min/day of use (Sensor Tower via VietNamNet, 2026-09, M-H). Localisation is
  mostly **subtitles** of translated Chinese shows; no evidence of Vietnamese dubbing (M).
- **A/V sync** — ITU-R BT.1359: noticeable at audio +45 / −125 ms, acceptable to +90 / −185 ms (H);
  speech is the most sensitive content (arXiv 2212.01686, M). vizsup's parts are within 1 ms.

### Creators and competing tools
- The **#1 complaint** across HeyGen, ElevenLabs, CapCut, Rask (Trustpilot, Sep–Oct 2026) is
  **billing/credits** — surprise charges, credits drained mid-project (H). vizsup already shows
  cost after a run; nobody shows it *before*.
- Competitors ship **editable translation before render** (HeyGen Pro, Rask) — parity with
  vizsup's gate; **voice similarity / emotion controls** (ElevenLabs slider, YouTube Expressive
  Speech — not for Vietnamese), **speaker labels / one voice per speaker** (Rask), **keeping
  background music** (ElevenLabs) (M-H).
- Lip-sync draws the most complaints (Rask "total garbage") → not worth building (M).
- Hardsub removal has demand (open-source *video-subtitle-remover*, 13k★; STTN is the light model
  for 8 GB) (M).
- No independent data on Vietnamese creators' volume/earnings (only promotional blogs, L).

### Platform & legal rules
- **YouTube** ✓: reused content is allowed only "if viewers can tell that there's a meaningful
  difference" — e.g. "edited footage from other creators where you add a storyline and
  commentary"; "This policy applies even if you have permission from the original creator."
  "Repetitious" was renamed "inauthentic content" on 2025-07-15 (H).
- **Meta** (2025-07, 2026-03): reposting without meaningful enhancement → loss of monetisation
  and reduced distribution; stitching clips or adding a watermark doesn't count (H).
- **TikTok**: realistic AI-generated content must be labelled; Creator Rewards needs ≥ 1-min
  original videos; lightly repurposed / watermarked reposts are ineligible (M-H).
- **Douyin's terms** (2026-02) forbid editing/adapting its content and showing it outside Douyin
  without authorisation (H). **VN IP law** Art. 20: derivative works (incl. translation) need the
  author's permission; Art. 19: right to be named (H for the 2005 text).
- **VN AI Law 134/2025/QH15, Art. 11(4)** ✓, in force 2026-03-01: AI audio/video that "simulate or
  impersonate the appearance or voice of real persons" must be labelled; Decree 142/2026 Art. 18
  requires the label to be visible before/at first view (H for text, L for how it applies to a
  stock TTS voice — probably outside, a cloned voice is inside).
- **Enforcement climate (VN)** ✓: the Cinema Department (2026-10-09) says online films, "dù là phim
  ngắn hay phim ứng dụng công nghệ AI", fall under cinema law; takedowns in ~2 working hours.
  Báo Văn Hóa (same day) describes studios that translate, dub and re-split films across accounts
  and says fingerprinting still matches renamed/cropped/sped-up/split copies (M-H).

**Bottom line:** nothing licenses translating and reposting someone else's Douyin/Bilibili video.
What platforms reward — and what the law cares about — is *permission* plus *your own added
value* (commentary, storyline, credit) and *honest labelling*. The features below help with the
second and third; they cannot replace the first.

## 2. What vizsup already covers

Editable translation before any spend (the gate) · per-video context brief and glossary · AI
review flags · cloud OCR/ASR with cost tracking after the run · five audio modes incl. ducked
original (= *thuyết minh*) and "Giữ giọng gốc" (subtitles only) · 9:16 preset · soft-sub .mkv ·
long-video parts with consistent names · A/V sync < 1 ms.

## 3. Proposed items (ranked)

| # | Item | Why (evidence) | Size |
|---|---|---|---|
| F-1 | ✅ **Source credit** — auto text from metadata (creator, platform, link, date) for the post description + an optional end card / first-line caption. *Done 2026-10-14: `pipeline/disclosure.py`; "Ghi nguồn trên video" burns "Nguồn: người đăng · Douyin" top-left for 5 s; "Mô tả đăng bài" on the render page* | Meta "without crediting them"; VN IP Art. 19 naming right; YouTube credit alone isn't enough but helps (H) | S |
| F-2 | ✅ **AI-voice disclosure** — optional burned label "Lồng tiếng AI" (corner, first seconds) + a description line; on by default when TTS is used. *Done: top-right, 5 s, off for "Giữ tiếng gốc"; corner notes sit ~9% down, below TikTok's top bar* | TikTok AIGC rule; VN AI Law Art. 11(4) / Decree 142 Art. 18 for imitated voices; cheap trust signal (H) | S |
| F-3 | ✅ **Rights note per project** — own / permission (attach proof) / licensed / unknown; a visible warning on "unknown", never a hard block. *Done: `rights.json` + `rights/` proof copy; pre-post checklist in the description dialog* | Douyin terms, VN IP Art. 20, YouTube "even with permission" (H) | S |
| F-4 | **Commentary track** — the user's own narration (typed → a *different* TTS voice, or recorded mic) at chosen points + intro/outro text; show the share of runtime that is original commentary | The one thing YouTube/Meta/TikTok all name as making reuse acceptable (H) | M |
| F-5 | ✅ **Cost & time estimate before Start** — from duration: ASR/OCR/translate/TTS $ and minutes, per engine choice. *Done: `app/estimate.py`, calibrated on two real runs, switches to the user's own history after 3 projects; expired cookies flagged* | #1 complaint about every competitor (H) | S |
| F-6 | ✅ **Reading-speed check** — flag > 17 cps, > 42 chars/line, > 2 lines (Netflix VI); auto line breaking; a stricter "mobile" profile. *Done: "Đọc không kịp" flags > 25 cps or > 2 wrapped lines (real vlogs run a median 22–24 cps, so 17 would flag most lines); the inspector shows cps against 17* | Netflix VI guide (H); phone screens (M). Today "Quá dài" only checks the *dub* slot | S |
| F-7 | **Audio mode by content type** — name the modes as viewers know them (*Phụ đề* / *Thuyết minh* / *Lồng tiếng*), suggest per video (singing → Phụ đề — already; drama → Thuyết minh; vlog/comedy → Lồng tiếng) | VN distributor quotes + 2017 dub backlash (L-M); no survey exists → keep user's choice | S |
| F-8 | **Publish checklist + log** — per platform: AI toggle, credit present, commentary share, no third-party watermark; record the posted URL; note claims/strikes | YouTube 3 strikes/90 days; Meta duplicate distribution (H) | S-M |
| F-9 | **Voices**: Southern + more Northern voices via the existing FPT.AI provider (lannhi, banmai, leminh…), then **one voice per speaker** (needs speaker labels per line) | 2017 regional backlash → A/B rather than guess (L); Rask speaker labels (M) | M-L |
| F-10 | **Dub onset drift flag** — show lines whose dub starts > 200 ms after the speech (fit_report already has `carry_s`) | BT.1359 / arXiv 2212.01686 (M) | S |
| F-11 | **Short-episode export** — 1–3-min clips with "Phần k/n" cards cut at voice gaps (reuses the split machinery) | VN short-drama format (M). Only for content you have rights to — split reposts are exactly what regulators describe | M |
| F-12 | **Keep background music** — make "Giữ nhạc nền" work out of the box (Demucs not installed here; or a cloud separation API) | ElevenLabs keeps it; dub-over-silence sounds cheap (M) | M |
| F-13 | **Hardsub inpainting (STTN)** as a third cover option next to box/blur — Chinese subtitle band only, never logos/watermarks | 13k★ OSS demand (M); 8 GB VRAM → STTN only | M-L |

**Do not build** (evidence or policy says no): watermark/logo removal or watermark-free download
(Douyin forbids it; platforms treat it as unoriginal); mirror/crop/speed tricks against duplicate
detection (fingerprinting catches them, and it is evasion); lip-sync (complaint-heavy); cloning a
real person's voice (CLAUDE.md out of scope; labelled-impersonation rules).

**Suggested order:** ~~F-5, F-6, F-1, F-2, F-3~~ (done 2026-10-14) → **F-4** (the biggest real
difference for monetisable posts) → F-7, F-10, F-8 → F-9, F-12 → F-11, F-13.

## 4. Open questions (not findable on the web)
Vietnamese viewers' dub/sub/thuyết minh preference by genre; acceptance of stock AI voices and
Bắc/Nam preference; whether DramaBox/PineDrama ship Vietnamese dubs. The cheapest way to answer
them is the user's own analytics: log audio mode + voice per published video (F-8) and compare
retention.
