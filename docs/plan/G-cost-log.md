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
