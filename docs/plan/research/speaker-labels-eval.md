# Can the audio LLM label each line's speaker? — study 2026-10-30, nothing shipped

Question (night-2 T2): can `providers/asr/audio_llm.py` (Qwen-Omni, `qwen/qwen3.8-omni-flash`) say
who speaks each already-timed line (M / F / system voice, plus a stable speaker id) cheaply enough
to give the translator the right xưng hô (e.g. ep 9 line 4 "你倒是会挑" is a girl teasing anh Cố)?

Method: `AudioLLM.ask()` with the clip's audio (mono 16 kHz mp3, whole episode ≤ 240 s) and the
numbered cn.srt lines with their time ranges; reply `n| G ID`. Copies of ep 7 (88 lines, 135 s) and
ep 9 (66 lines, 117 s). No hand-labelled ground truth exists, so the reference is the local pitch
tagger (`voices.py`, per-video split) — itself an estimate — plus reading the dialogue.

| | ep 9 | ep 7 |
|---|---|---|
| cost (one call) | $0.0006 (with ids) / $0.0011 (gender only) | $0.0018 |
| replies parsed | 66/66 | 88/88 |
| agreement with pitch, all pitch-tagged lines | 51/54 (94 %) | 15/16 once system-voice lines are set aside |
| agreement, confident pitch subset (≥ 20 voiced frames, > 25 Hz from the split) | 32/33 | 15/16 |
| labels | M 32 · F 34 | M 44 · F 36 · **S 8** |

What the numbers say:
- Gender per line is as good as pitch on long lines, for ~$0.001 per episode (≈ $0.01 for 30 min).
- Its real extra: it recognises the **system voice** ("Ting! Hạ zombie", "Điểm sinh tồn +1") as `S`.
  Pitch calls those lines female (331–375 Hz). 8 lines in ep 7; they could get a neutral / separate dub voice.
- Where it matters most it does not help: the short reply right after a turn change. Ep 9 line 4
  (the girl's "你倒是会挑", 10 voiced frames) is labelled **M A** by the model and `m` by pitch — both
  wrong, so the translator would still get "Cô…" vs "Anh…" no better than today. Lines 7, 9, 55 of
  ep 9 are the other disagreements (short, near the pitch split); I cannot say which side is right.
- Speaker ids over-segment (ep 7: 12 ids for 4 speakers; ep 9: A 31 lines, 7 other ids) — usable as
  "same voice as line n" hints at best.

Verdict: not shipped. It would add an upload-audio call per episode for a label that is right where
the pitch tagger is right and wrong where it is wrong; the one clear win (system voice → `S`) is a
separate small feature. If xưng hô errors on short replies remain the problem, the cheaper lever is
the translator itself: give it the neighbouring lines' speaker turn (it already has the series sheet
and the previous episode's last lines) rather than another model's guess.
Reproduce: `scratchpad/fit/t2_run.py <ep> source.wav ids` (needs OPENROUTER_API_KEY).
