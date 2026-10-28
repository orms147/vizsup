# Homophone (pinyin) anchoring in `align._match_pairs` — evaluated 2026-10-29, NOT shipped

Question: Whisper mishears sung / chanted hanzi as homophones (王于兴师 → 五月七日), so ~11 lines of the
sung 《诗经》 ad stay "Timing đoán" after the island placement. Would tone-less pinyin matching
(pypinyin 0.55.0, pure Python, no dependencies) anchor them?

Method: replayed `align_text_to_words` on the stored `asr_words.json` + VAD of the two real jobs
(3fa4326731ee: 11 guessed of 43; 817323b24757: 2 guessed of 325), with `_match_pairs` run on syllable
lists instead of hanzi. Copies only; nothing installed into the venv (pypinyin went to a scratch dir).

| variant | job 3fa4 guessed | job 8173 guessed | damage |
|---|---|---|---|
| exact hanzi (today) | 11 | 2 | — |
| pinyin, all sentences | 11 → 4 | 2 → 3 | 4 previously-anchored lines shifted > 1 s (修我甲兵: voice coverage 1.00 → 0.00; 叮: 1.00 → 0.14); 3 new anchors on silence / music hallucination (cov 0.00, 0.13, 0.37) |
| fuzzy (zh/z, ch/c, sh/s, ng/n, l/n) | same as above | same | same, fuzzier |
| pinyin only for sentences with NO exact anchor, only on VAD voice (≥ 50 %), only in the order the exact anchors fix | 11 → 7 | 2 → 2 | 岂曰无衣 anchored 3.8 s for 4 chars and the lines after it squeezed to 0.2–0.45 s |
| + pace guard (0.10–0.60 s/char per sentence) | 11 → 9 | 2 → 2 | 2 lines anchored (百川东赴海 refined 136.98–143.04 → 138.44–142.42; 岂曰无衣 → 215.58–216.64, plausible), but the two sibling lines it left behind both got 216.64–216.94 (stacked, 0.3 s each) instead of their old place in the real voice island 196.35–199.29 |

Why it fails: the song repeats the same two couplets (岂曰无衣 / 与子同袍 / 王于兴师 …) and Whisper loops
"我可以去寻找你吗" / "嗯 嗯 …" over the music; homophone matching happily pairs a sung line with a
DIFFERENT repeat or with a hallucination, and one wrong anchor drags its neighbours (the DP is
monotonic, so the leftovers are squeezed). With exact hanzi the same effect is rare because 2+ identical
hanzi in a row are hard to hallucinate.

Verdict: net ≈ +1 good refinement, +1 plausible anchor, −2 degraded lines on the one job it helps, no
gain on the other → **not shipped, pypinyin not added.** The island placement (feat/timing-guess)
already keeps every guessed line on real voice. Re-open only with a way to veto a repeat/loop (e.g.
require the matched heard run to be the ONLY candidate within ±15 s, or an audio-level check).
