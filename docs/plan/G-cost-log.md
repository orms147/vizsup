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

TTS edge-tts: free (717 lines). Render: local (NVENC when available) — no API spend.

## Where the money goes → candidates for the cost pass

1. **Refine model ≈ 86% of the run.** kimi-k3 refine on 756 lines = $0.25. Options to measure:
   a cheaper refine role model (benchmark table in `E-roadmap.md`), refine only lines the
   draft flags (uncertain / long / term hits) instead of all, or draft-only for "nháp nhanh".
2. **Quality data for that decision:** of 61 AI-review notes, ~22 were wrong because the
   reviewer judged half-sentences (fixed in `6d94d46` — needs a re-measure); of 111 lines the
   bulk shortener touched, 4 lost meaning and 8 broke "các bạn" → keep the human dialog.
3. Whisper + omni ASR together ≈ $0.017 / 28 min — not worth optimizing.
4. Cost per video minute (this run): ≈ $0.0105 / min to the gate.
