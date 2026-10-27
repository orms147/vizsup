# G — Cost log (measured runs) → input for the cost-optimization pass

**Audience:** the session that will do the cost pass (the user asked for it to come *after* the
feature/UX fixes of 2026-10-26/27). Every number here was measured on a real run, not estimated.
Add a section per new measured run; keep the table shape so runs can be compared.

## Run 1 — 2026-10-26, job `703e11aadf05` (full end-to-end in the desktop app)

**Video:** Douyin tech review "能折！但代价呢？iPhone Duo首发体验" (何同学), 27:42, 1080p, one fast male
speaker (~28 chars/s in Vietnamese), burned-in Chinese subtitles, music intro 0:42–1:16.
**Options:** source "AI nghe âm thanh" (ASR `audio_llm`), draft + refine OpenRouter (role models),
AI soát on, Tự đọc ngữ cảnh on, Tự canh lời on, TTS edge (free), voice NamMinh.

### Spend to the edit gate (`usage.json`)

| model (OpenRouter) | role | calls | in tok | out tok | $ | share |
|---|---|---|---|---|---|---|
| `moonshotai/kimi-k3` | refine | 18 | 63,675 | 20,829 | 0.2481 | **≈86%** |
| `deepseek/deepseek-v4.1-flash` | draft | 16 | 27,024 | 15,457 | 0.0135 | 5% |
| `openai/whisper-large-v3` | word timing (7 × ≤4 min chunks) | 7 | — | — | ≈0.0125 † | 4% |
| `qwen/qwen3.8-flash` | context brief + AI review ($0.0058) | 7 | 13,208 | 3,168 | 0.0031 + review | 2% |
| `qwen/qwen3.8-omni-flash` | ASR transcript (7 × ≤4 min) | 8 | 12,717 | 6,146 | 0.0048 | 2% |
| **total** | | | | | **≈ $0.29** | |

† not recorded before `6d94d46`: cloud Whisper calls were missing from `usage.json`. OpenRouter lists
it at $0.0000075/s (≈$0.00045/min); a live 60 s call was billed exactly that, an 8 s call $0.0002
(minimum charge). Sources: https://openrouter.ai/api/v1/models,
https://openrouter.ai/openai/whisper-large-v3/providers (checked 2026-10-26).

**Time to the gate:** 287 s (ASR 99 s · context 19 s · translate + refine + AI review ≈ 170 s).
The estimate before Start said 8–19 min (fixed in `6d94d46`: now 4–8 min).

### Spend in the editor (human gate)

| action | model (role) | calls | $ |
|---|---|---|---|
| Rút gọn (1 line), Dịch lại (3 phương án) | `qwen/qwen3.8-flash` (shorten) | 8 | ≈0.003 |
| Rút gọn hàng loạt, pass 1 (111 lines → 86 proposals, 82 kept) + pass 2 (11 → 10) | same | ~4 | < 0.01 |

### After the gate

TTS edge-tts: free (717 lines) — but **unreliable in Oct 2026**: during this run Microsoft's
endpoint answered ~50–75% of requests with "no audio" (rany2/edge-tts #473,
travisvn/openai-edge-tts #61). Before `2e3b405` that silently dropped 417/717 lines; now the app
retries with a shared backoff and stops with "đã tạo X/Y câu" after a streak. A 717-line dub
took several clicks of "Dựng lại". Render: local (NVENC when available) — no API spend.

### Final render (2026-10-27 08:39 → 08:56, 17 min, 1080p, NVENC)

| | 1.0× preview (no warning) | final: 1.3× + bulk shorten at 1.3× |
|---|---|---|
| lines sped up / flagged | 643 / 603 | 437 / 61 |
| late lines (> 0.2 s) | 522 | 61 |
| speech trimmed | 297 s | 36 s |
| max carry | 4.2 s | 2.8 s |

Root cause of the 1.0× result: the fit estimate assumed 4.8 words/s; edge-tts NamMinh speaks
2.8 (`cb9ad02` → 3.0 + a speed suggestion on the render page). Output: `output.mp4`
1920×1080, 4.8 Mb/s, 27:50 (incl. a 9 s commentary pause); exports .srt / .mkv (soft ASS) checked.

## Where the money goes → candidates for the cost pass

1. **Refine model ≈ 86% of the run.** kimi-k3 refine on 756 lines = $0.25. Options to measure:
   a cheaper refine role model (benchmark table in `E-roadmap.md`), refine only lines the
   draft flags (uncertain / long / term hits) instead of all, or draft-only for "nháp nhanh".
2. **Quality data for that decision:** of 61 AI-review notes, ~22 were wrong because the
   reviewer judged half-sentences (fixed in `6d94d46` — needs a re-measure); of 111 lines the
   bulk shortener touched, 4 lost meaning and 8 broke "các bạn" → keep the human dialog.
3. **TTS reliability has a price question:** a paid fallback voice (FPT / Azure / Google are
   wired but have no keys here) used only for lines edge-tts refuses would make renders
   finish in one click — measure its per-line cost before proposing it.
4. Whisper + omni ASR together ≈ $0.017 / 28 min — not worth optimizing.
5. Cost per video minute (this run): ≈ $0.0105 / min to the gate.

## Cost pass — 2026-10-27: cheaper refiners, model packages (→ default ≈ $0.0016 / min)

User: "kimi đắt quá — luna 6 hoặc deepseek 4.1 flash 1017 cũng ổn". Measured on short material
first, as asked: the **first 120 lines (0:00–4:24)** of Run 1's job `703e11aadf05`.

**Method.** Every setup ran through the app's real `translate_cues` (draft → refine with the
brief, word budgets, pre-gate auto-shorten by the `shorten` role) with the role models set the
way Settings sets them (`MODEL_<ROLE>`); one DeepSeek draft was reused for every refiner through
the real checkpoint, so refiners saw the same input. Baseline **K** = the shipped `vi.mt.srt`
(DeepSeek draft + Kimi K3 refine + Kimi auto-shorten) — not re-bought. Cost = OpenRouter
`usage.cost` per response. **Blind judging:** systems shuffled behind letters on every line,
20 consecutive lines per request, each translation scored 1-10 for accuracy (vs the Chinese),
naturalness (spoken VI, "mình/các bạn"), length fit (syllables vs the slot budget = slot × 1.3 ×
3.0 syl/s). Three judges, none a candidate: `qwen/qwen3.7-plus`, `z-ai/glm-5.3`,
`google/gemini-3.8-flash`. Script: `backend/scripts/cost_bench.py` (run / judge / report).

| system | draft → refine (shorten) | judge qwen | judge glm | judge gemini | acc | nat | len | **mean** | lines over budget | $ / 120 lines | s / 120 lines | $ / video-min † |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **K** shipped | ds-v4.1-flash → **kimi-k3** | 8.51 | 8.80 | 8.66 | 9.15 | 8.84 | 7.98 | 8.66 | 88 | ≈ 0.040 ‡ | — | 0.0091 |
| F draft only | ds-v4.1-flash (ds) | 8.54 | 8.79 | 8.61 | **9.20** | 8.85 | 7.90 | 8.65 | 90 | 0.0022 | 21 | 0.0005 |
| **L** | ds-v4.1-flash → **gpt-6-luna** | 8.47 | **9.06** | **9.04** | 9.06 | **9.04** | **8.47** | **8.86** | **59** | 0.0033 | 29 | 0.0007 |
| **D** | ds-v4.1-flash → **ds-v4.1-flash-1017** | 8.53 | 9.05 | 8.95 | **9.20** | 8.96 | 8.36 | 8.84 | 76 | 0.0041 | 25 | 0.0009 |
| LO draft only | gpt-6-luna (luna) | **8.56** | 8.94 | 8.90 | 9.11 | 9.02 | 8.27 | 8.80 | 69 | **0.0019** | **15** | **0.0004** |
| LL | gpt-6-luna → gpt-6-luna | 8.50 | 8.97 | 9.01 | 9.03 | 9.02 | 8.43 | 8.83 | 68 | 0.0026 | 20 | 0.0006 |

† translate stage only (draft + refine + auto-shorten), 27.3 lines/min as in Run 1. To the edit
gate add cloud ASR ≈ $0.00062/min and AI review (qwen3.8-flash) ≈ $0.00021/min.
‡ Run 1's Kimi refine + auto-shorten + brief: $0.248 / 756 lines (+ draft $0.0135 / 756); not
re-bought. At today's list price (20 $/M out) the same tokens would cost ~1.7× that.
acc / nat / len = mean of the three judges. Latency: 3 batches of ≤50 lines, 4-wide, the shared
draft (15.3 s) included for F / L / D; Kimi's was not re-measured (Run 1: draft + refine + AI
review of 756 lines ≈ 170 s).

**Judge agreement.** glm-5.3 vs gemini-3.8-flash agree (system ranking Spearman 0.94; per-line
× system score Pearson 0.54; length scores system-level 0.99 and both match the objective
over-budget count). qwen3.7-plus disagrees with both (Spearman −0.60 / −0.71) and its LENGTH
scores run backwards against the objective count (−0.96) — it ranked all six within 0.09, so
it cannot separate them. A re-run of gemini on the first 20 lines correlated 0.89 with itself.
Every setup scored within 0.25 of the others — on this material **Kimi K3 is not better**: it
ties the draft alone (8.66 vs 8.65) and keeps its long lines (88 over budget vs 59 for luna).

**Spot checks (read by hand, 15 lines).**
- #2 折叠屏一直是一个有点拧巴的产品形态 — K "hơi tréo ngoe" ✓, L "khá oái oăm" ✓, F/D "hơi gượng gạo" ok, LO "hơi lưng chừng" (wrong sense), LL "khó chiều" meh.
- #8–9 而且为了能折叠，它的性能、/ 续航和发热都会有所妥协 — L moves "hiệu năng" into #9 ("Để gập được, / máy cũng phải hy sinh hiệu năng, pin và khả năng tản nhiệt") — natural, but content crosses a cue boundary (judges docked #8); K keeps the split; draft/F/D "độ nóng" for 发热 is clumsy.
- #18 今天就让我们一起来看一看 — K "Hôm nay **chúng ta** cùng nhau xem thử nhé" breaks the brief's "mình/các bạn" (two judges flagged it); L/LL "Hôm nay mình cùng xem thử nhé" ✓; LO "mình cùng các bạn tìm hiểu nhé" ✓.
- #27 展开放上去的话，会荡下来 (unfolded on MagSafe it swings down) — K/D/F "trượt xuống" (slides), LO "rủ xuống" (droops) is the closest.
- #54 苹果的想法是用这种不对称的设计 — L "Apple dùng thiết kế này" drops 不对称 / 想法 (glm acc 5); D/LO/LL "Apple muốn dùng thiết kế bất đối xứng này" ✓.
- #55 时刻提醒你这块屏幕是不完整的 — D "để luôn nhắc màn hình này chưa trọn vẹn" lost "các bạn rằng" (ungrammatical); others fine.
- #60 折痕控制是真不错 — LL "Nếp gấp trên Duo mới khui hộp rất khó thấy" changes the claim; L/LO "được kiểm soát rất tốt" ✓.
- #6–7 但大带来的却经常是更宽的 / 黑边… — LO "… rộng hơn **nhưng** giao diện bị phóng to" wrong connector.
- #84–85 这个折叠屏版的 / Flappy Bird (0.46 s slot) — K "làm ra trò Flappy Bird / bản máy gập" fits; F/D put 7 syllables in the 0.46 s slot.
- #89 让它把手机们折叠三千次 — L adds "ba chiếc" (inferred from #91 三台手机; plausible, but added).
- #100 猜测是 Duo — LL "Bọn mình đoán là Duo】【。" — stray Chinese brackets (all judges ≤3). **Fixed**: `translate.clean_vi` now maps/drops Chinese punctuation in a translated line (lines that still hold hanzi are left for review).
- #105 当你把它折起来时 — K/D/F "khi bạn gập nó lại" (brief says "các bạn"); LL "khi các bạn gập máy lại" ✓.
- #117 媒体机 — L "máy review" (how VN tech channels say it), K/F "máy dành cho truyền thông" literal.
- #114 在折叠这件事情上做到了挺优秀的水平 — L "Apple đã làm khá xuất sắc ở khoản này" ✓, F "ở khoản gập lại này họ đã làm được ở mức khá xuất sắc" long.
- #3 哪块屏幕的使用体验都不够好 — K "chưa đủ ngon", L "dùng màn nào cũng chưa thật sự đã" (natural, shorter), D = draft.

Pattern: the luna refiner rewrites almost every line (112/120) into shorter, more spoken Vietnamese
and occasionally condenses a detail away; deepseek-1017 edits conservatively (67/120) and keeps
the most meaning; Kimi kept the draft's literal phrasing and length.

**Brief role** (one call per video, same request as Run 1, 756 lines sampled): Kimi K3's brief
(Run 1) is the richest — term table (折痕 = nếp gấp, 铰链 = bản lề, 悬停 = giữ góc mở) + ASR-error
notes. deepseek-v4.1-flash-1017 ($0.0007, 3 s) and deepseek-v4.1-flash ($0.0005, 6 s) both get
genre, "mình / các bạn", tone, names and the ASR slips ("Air 20 Pro" = A20 Pro); gpt-6-luna
($0.0008) is the thinnest (no term notes). Kimi's brief ≈ $0.01–0.015.

**Prices** ($/M tokens in/out, https://openrouter.ai/api/v1/models, fetched 2026-10-27):
`openai/gpt-6-luna` 0.10/0.50 · `deepseek/deepseek-v4.1-flash` 0.14/0.42 ·
`deepseek/deepseek-v4.1-flash-1017` 0.30/1.20 · `moonshotai/kimi-k3` 0.20/**20.00** ·
`qwen/qwen3.8-flash` 0.15/0.47 · judges `qwen/qwen3.7-plus` 0.32/1.28, `z-ai/glm-5.3` 0.077/1.40,
`google/gemini-3.8-flash` 0.825/4.125. deepseek-1017 was billed ~40% under these list prices (likely prompt
caching); luna was billed exactly at list.

**Spend of this pass:** $0.1051 (6 translate setups $0.0111 · 3 judges + a 20-line probe $0.0919
· 3 briefs $0.0020).

### Shipped: model packages (Settings → AI và mô hình → "Gói mô hình")

`app/providers/presets.py`; a role resolves `MODEL_<ROLE>` → package (`MODEL_PRESET`) → role
default; editing one role shows "Tùy chỉnh"; choosing a package clears hand-picked text-role
models. $/min = to the edit gate (cloud ASR + translate + AI review), talking vlog.

| package | draft | refine | shorten | brief | $/video-min | + per video | 30-min video |
|---|---|---|---|---|---|---|---|
| Tiết kiệm | gpt-6-luna | gpt-6-luna | gpt-6-luna | ds-v4.1-flash | 0.0014 | 0.0005 | ≈ $0.04 |
| **Cân bằng (default)** | ds-v4.1-flash | **gpt-6-luna** | gpt-6-luna | ds-v4.1-flash-1017 | **0.0016** | 0.0007 | **≈ $0.05** |
| Chất lượng cao | ds-v4.1-flash | ds-v4.1-flash-1017 | ds-1017 | kimi-k3 | 0.0018 | 0.013 | ≈ $0.07 |
| Kimi K3 (như trước) | ds-v4.1-flash | kimi-k3 | kimi-k3 | kimi-k3 | 0.0099 | 0.013 | ≈ $0.31 |

Proofread / terms stay `deepseek/deepseek-v4.1-flash`, QA `qwen/qwen3.8-flash` (another
vendor) in every package; vision / OCR are opt-in and not packaged. Run 1 under the new
default would have cost ≈ $0.045 instead of $0.29 (−85%). `estimate.py` now prices each pass by
the role's model (`LINE_COST` / `BRIEF_COST`, measured; an unmeasured model gets a wide range and
a note), and past projects paid with other priced models no longer calibrate the cost.

**Not done / next:** re-check on a second genre (a song / an OCR'd vlog) before trusting the
luna refiner everywhere; the auto-shorten after a luna refine touched 6 lines vs 16 after
deepseek-1017 — measure late-dub lines on a full render with the new default.
