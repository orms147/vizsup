# Provider matrix

All providers are pluggable and UI-selectable. The registry reports `available`
based on whether the key/dependency is present. Verify model ids & pricing on the
web before relying on them — don't trust memory.

## Translation (CN→VI)

| id | engine | key (env) | local | notes |
|---|---|---|---|---|
| `passthrough` | none (keeps CN) | — | ✓ | default; offline smoke-test only |
| `openrouter` | **OpenRouter** (any model) | `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` | | one key → 340+ models. Pick the model in **Settings → Tải danh sách model** (live `/models` fetch, public). **Test kết nối** verifies the key. |
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

| id | engine | local | install | notes |
|---|---|---|---|---|
| `faster_whisper` | faster-whisper (large-v3) | ✓ | `.[asr]` | **default** — 1 pip package, CPU/GPU, segment timestamps; CER ~5% on Mandarin (edit gate catches errors). **Implemented + verified.** |
| `whisperx_funasr` | FunASR Paraformer (CN-native) | ✓ | `.[asr-accurate]` | **accuracy option** — CER ~1.7%, CPU-viable; needs `funasr` + torch. WhisperX word-alignment is a future refinement. **Implemented.** |

> ASR timing ≠ accuracy (CLAUDE.md): the ideal is WhisperX word timestamps + a
> CN-native transcript. v1 ships faster-whisper as the default for install
> reliability on Windows, with FunASR Paraformer as the one-click accuracy upgrade
> in the dropdown. Model weights download on first run (~75MB tiny … ~1.5GB large-v3).
> Set `WHISPER_MODEL=tiny|base|small|medium|large-v3` in `.env` to trade speed/accuracy.

## Connectivity (Settings dialog)
Providers may implement `test_connection() -> (ok, msg)` and `list_models() -> [ids]`.
Implemented for the OpenAI-compatible base (OpenRouter / DeepSeek / Qwen) and for
Claude + Gemini. The Settings dialog runs these on a background thread (no UI freeze):
**Test kết nối** per provider, and **Tải danh sách model** for OpenRouter.

## Adding a provider
See the `/add-provider` skill: one file in `providers/{translation,tts,asr}/` + one line in `registry.py`.
Optionally implement `list_models()` / `test_connection()` for the Settings dialog.
