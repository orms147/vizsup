# Provider matrix

All providers are pluggable and UI-selectable. The registry reports `available`
based on whether the key/dependency is present. Verify model ids & pricing on the
web before relying on them — don't trust memory.

## Translation (CN→VI)

| id | engine | key (env) | local | notes |
|---|---|---|---|---|
| `passthrough` | none (keeps CN) | — | ✓ | default; offline smoke-test only |
| `openrouter` | **OpenRouter** (any model) | `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` (+ optional `OPENROUTER_PROMO_MODEL` / `OPENROUTER_PROMO_EXPIRES`) | | one key → 460+ models. Pick the model in **Settings → Tải danh sách model** (live `/models` fetch, public). **Test kết nối** verifies the key. Reasoning is switched off per request — see below. |
| `deepseek` | DeepSeek-V4 Flash | `DEEPSEEK_API_KEY` | | `deepseek-v4-flash` ~$0.14/$0.28; best $/quality draft |
| `deepseek-pro` | DeepSeek-V4 Pro | `DEEPSEEK_API_KEY` | | `deepseek-v4-pro` ~$0.435/$0.87; hard lines |
| `claude` | Claude Sonnet 4.6 | `ANTHROPIC_API_KEY` | | `claude-sonnet-4-6`; best VI register → refine pass |
| `claude-opus` | Claude Opus 4.8 | `ANTHROPIC_API_KEY` | | `claude-opus-4-8`; premium |
| `glm` | GLM-5.2 (Zhipu) | `ZHIPU_API_KEY` | | latest GLM, 744B MoE, MIT weights, 1M ctx; verify API id |
| `gemini` | Gemini 2.5 Flash | `GEMINI_API_KEY` | | cheap, good VI fluency |
| `qwen` | Qwen-Plus (DashScope) | `DASHSCOPE_API_KEY` | | `qwen-plus` rolling alias (→ current Qwen-Plus snapshot); Chinese-native, cheap |
| `deepl` | DeepL | `DEEPL_API_KEY` | | gist baseline; VI support limited |

> **Two-pass recommendation**: draft with a CN-native model (`deepseek` / `glm` / `qwen`)
> for source comprehension, refine with `claude` / `gemini` for Vietnamese register.
> Legacy ids `deepseek-chat`/`deepseek-reasoner` deprecate **2026-07-24** — use v4.

## TTS (Vietnamese)

| id | engine | key (env) | local | notes |
|---|---|---|---|---|
| `edge` | edge-tts vi-VN | — | | **default**, free; HoaiMy (F) / NamMinh (M); occasional 403 |
| `fpt` | FPT.AI | `FPT_API_KEY` | | native VI voices (stub, P5) |
| `azure` | Azure Neural vi-VN | `AZURE_SPEECH_KEY` | | reliable + SSML rate (stub, P5) |
| `google` | Google Cloud TTS | `GOOGLE_APPLICATION_CREDENTIALS` | | neural vi-VN (stub, P5) |

> Voice cloning (ElevenLabs / viXTTS / F5-TTS) is **out of scope for v1** — stock voice is enough.

## ASR (Chinese, with timestamps)

| id | engine | local | install | notes |
|---|---|---|---|---|
| `faster_whisper` | faster-whisper (large-v3) | ✓ | `.[asr]` | optional offline engine (was the default until 2026-10-01) — CPU/GPU, segment timestamps; CER 0.185 on a real Douyin vlog vs 0.128 for `audio_llm`. Models are ~0.5–3 GB. |
| `whisperx_funasr` | FunASR Paraformer (CN-native) | ✓ | `.[asr-accurate]` | **accuracy option** — CER ~1.7%, CPU-viable; needs `funasr` + torch. WhisperX word-alignment is a future refinement. **Implemented.** |
| `audio_llm` | audio LLM via chat (`input_audio`) + word timing | ✗ **uploads audio** | base deps | **default.** Text from a Chinese-native omni model (default `qwen/qwen3.8-omni-flash`), split into subtitle lines and aligned onto a Whisper **word** track (`align.align_text_to_words`): cloud `STT_TIMING_MODEL` (default `openai/whisper-large-v3`), or local faster-whisper when installed. Cloud vs local timing on the real vlog: cue start median 0.26 s vs 0.20 s. 240 s chunks cut at silences. |
| `cloud_stt` | `/audio/transcriptions` (Whisper-family API) | ✗ **uploads audio** | base deps | Text + segment timestamps in one call (default `openai/whisper-large-v3`). Models that reject `verbose_json` (gpt-4o-transcribe) are re-asked for `json` and get local word timing like `audio_llm`. 360 s chunks. |

**Cloud ASR endpoint.** Both cloud providers default to OpenRouter and its key
(`STT_API_BASE_URL` / `STT_API_KEY` blank). Set them to use a vendor directly (Groq,
OpenAI, SiliconFlow …); the OpenRouter key is never sent to another host. Models:
`STT_API_MODEL`, `AUDIO_LLM_MODEL`.

**Benchmark, 2026-10-01** (all via OpenRouter except local; no Gemini — cost). Three clips:
a real 137 s Douyin vlog (fast speech, music; reference = its OCR'd hardsubs, cleaned
mechanically — residual OCR noise is shared by every engine, so the *ranking* holds), the
same with pink noise, and 53 s of synthetic accents (Northeast, Shaanxi, Taiwan, fast male,
slang). CER lower is better; Δstart = |cue start − reference|, median / p95.

| engine | vlog CER | noisy CER | accents CER | vlog Δstart | stability |
|---|---|---|---|---|---|
| **audio_llm · qwen/qwen3.8-omni-flash** (default) | **0.128** | **0.122** | **0.016** | 0.20 s / 1.8 s | 4/4 runs consistent |
| audio_llm · xiaomi/mimo-v2.6-pro | loop ✗ | 0.108 | 0.016 | — | fails some runs |
| audio_llm · xiaomi/mimo-v2.6-flash | 0.118 / loop ✗ | 0.124 | 0.016 | 0.23 s / 2.5 s | fails some runs |
| audio_llm · xiaomi/mimo-v2.5 | Korean / empty / loop ✗ | | | | unusable |
| cloud_stt · openai/gpt-4o-mini-transcribe | 0.148–0.229 | 0.161 | 0.053 | 0.20 s / 1.8 s | varies run to run |
| cloud_stt · openai/whisper-large-v3 | 0.181 | 0.185 | 0.080 | 0.50 s / 3.0 s | — |
| local faster-whisper small | 0.185 | 0.198 | 0.074 | **0.14 s / 0.8 s** | — |

Takeaways: Chinese omni models hear Mandarin ~30 % better than the Whisper family on a real
vlog and ~5× better on accents; Qwen-Omni is the only one that was reliable on every run.
The Whisper API adds nothing over local faster-whisper. Failure modes seen and now guarded
in `_cloud.py`: a reply in another language (rejected), a repetition loop (rejected), an
empty reply over audible speech (that chunk falls back to Whisper's text), and Whisper's
30 s pseudo-segments / unpunctuated text (re-timed from the word track). Reproduce with
`backend/scripts/asr_timing_eval.py` (`audio_llm:<model>` / `cloud_stt:<model>`).

> ASR timing ≠ accuracy (CLAUDE.md): a CN-native transcript + Whisper word timestamps.
> Since 2026-10-01 both halves run in the cloud by default (`audio_llm`): nothing to
> install, ~0 local CPU/GPU, and it beat local Whisper on real Douyin audio. The local
> engines stay available as optional extras for offline use: `.[asr]` (faster-whisper;
> `WHISPER_MODEL=tiny|base|small|medium|large-v3`, weights 75 MB … 2.9 GB on first run)
> and `.[asr-accurate]` (FunASR Paraformer).

## Connectivity (Settings dialog)
Providers may implement `test_connection() -> (ok, msg)` and `list_models() -> [ids]`.
Implemented for the OpenAI-compatible base (OpenRouter / DeepSeek / Qwen) and for
Claude + Gemini. The Settings dialog runs these on a background thread (no UI freeze):
**Test kết nối** per provider, and **Tải danh sách model** for OpenRouter.

## Adding a provider
See the `/add-provider` skill: one file in `providers/{translation,tts,asr}/` + one line in `registry.py`.
Optionally implement `list_models()` / `test_connection()` for the Settings dialog.

## OpenRouter: reasoning models and time-boxed promo models

**Reasoning is switched off on every OpenRouter request** (`reasoning: {effort: "none"}`).
Many current models reason by default (deepseek-v4.1-flash does), and on OpenRouter those
hidden tokens count against `max_tokens` — while `_completion_cap` budgets ~60 tokens per line
of *visible* text. Measured 2026-10-01, 50-line batch, cap 3000: deepseek-v4.1-flash with no
param was cut off at line 34; with `effort: "none"` it used 968 tokens and ran 2× faster.
Endpoints that refuse (`400 Reasoning is mandatory`, e.g. `stealth/space-bunny-alpha`) are
retried once with `effort: "minimal"` plus 2048 tokens of headroom, and remembered. Non-reasoning
models ignore the param. The shaping lives in `OpenRouter._chat()`; the DeepSeek/Qwen direct
providers do not send it (it is an OpenRouter extension).

**Promo model.** `OPENROUTER_PROMO_MODEL` is used *instead of* `OPENROUTER_MODEL` until
`OPENROUTER_PROMO_EXPIRES` (YYYY-MM-DD — the first day it is NOT used, same meaning as
OpenRouter's `expiration_date` in `/models`). From that date the app falls back on its own; it also
falls back, for the rest of the process, if the promo model answers "not found / no endpoints"
early. A malformed date disables the promo rather than running it indefinitely.

