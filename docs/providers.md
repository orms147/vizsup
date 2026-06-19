# Provider matrix

All providers are pluggable and UI-selectable. The registry reports `available`
based on whether the key/dependency is present. Verify model ids & pricing on the
web before relying on them — don't trust memory.

## Translation (CN→VI)

| id | engine | key (env) | local | notes |
|---|---|---|---|---|
| `passthrough` | none (keeps CN) | — | ✓ | default; offline smoke-test only |
| `deepseek` | DeepSeek-V4 Flash | `DEEPSEEK_API_KEY` | | `deepseek-v4-flash` ~$0.14/$0.28; best $/quality draft |
| `deepseek-pro` | DeepSeek-V4 Pro | `DEEPSEEK_API_KEY` | | `deepseek-v4-pro` ~$0.435/$0.87; hard lines |
| `claude` | Claude Sonnet 4.6 | `ANTHROPIC_API_KEY` | | `claude-sonnet-4-6`; best VI register → refine pass |
| `claude-opus` | Claude Opus 4.8 | `ANTHROPIC_API_KEY` | | `claude-opus-4-8`; premium |
| `glm` | GLM-5.2 (Zhipu) | `ZHIPU_API_KEY` | | latest GLM, 744B MoE, MIT weights, 1M ctx; verify API id |
| `gemini` | Gemini 2.5 Flash | `GEMINI_API_KEY` | | cheap, good VI fluency |
| `qwen` | Qwen3 (DashScope) | `DASHSCOPE_API_KEY` | | Chinese-native, cheap |
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

| id | engine | local | notes |
|---|---|---|---|
| `whisperx_funasr` | WhisperX timing + FunASR Paraformer | ✓ | **recommended**; best CN accuracy + word timestamps; GPU (stub, P2) |
| `faster_whisper` | faster-whisper large-v3-turbo | ✓ | simpler, lower CN accuracy (stub, P2) |

> ASR timing ≠ accuracy: WhisperX for word timestamps, a CN-native model (FunASR
> Paraformer) for the transcript. Don't use Whisper alone for Mandarin (CER 3–5× worse).

## Adding a provider
See the `/add-provider` skill: one file in `providers/{translation,tts,asr}/` + one line in `registry.py`.
