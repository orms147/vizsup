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

### OpenRouter roles and model packages

Every OpenRouter call names a role; its model = the user's `MODEL_<ROLE>` → the package
(`MODEL_PRESET`, Settings → AI và mô hình → "Gói mô hình", `app/providers/presets.py`, plus the
[model catalog](#model-catalog)'s changes to it) → the role default (`app/providers/roles.py`,
`default_for()`: the catalog's, else the table). Any OpenRouter id can be typed per role. Measured
2026-10-27 (docs/plan/G-cost-log.md: app's own two-pass translate on 120 real lines, 3 blind
judges); prices from https://openrouter.ai/api/v1/models on 2026-10-27, $/M tokens in/out.

| role | default (= "Cân bằng") | Tiết kiệm | Chất lượng cao | Kimi K3 (như trước) |
|---|---|---|---|---|
| draft | `deepseek/deepseek-v4.1-flash` (0.14/0.42) | `openai/gpt-6-luna` (0.10/0.50) | default | default |
| refine | `openai/gpt-6-luna` | luna | `deepseek/deepseek-v4.1-flash-1017` (0.30/1.20) | `moonshotai/kimi-k3` (0.20/20.00) |
| shorten (auto-shorten, Rút gọn, Dịch lại) | `openai/gpt-6-luna` | luna | ds-1017 | kimi-k3 |
| brief (1 call / video) | `deepseek/deepseek-v4.1-flash-1017` | ds-v4.1-flash | kimi-k3 | kimi-k3 |
| proofread (OCR) · terms | `deepseek/deepseek-v4.1-flash` | default | default | default |
| qa (AI soát) | `qwen/qwen3.8-flash` (0.15/0.47) | default | default | default |
| vision · ocr (opt-in, not packaged) | `qwen/qwen3.7-flash` | — | — | — |
| **$ / video-minute to the gate** (cloud ASR + translate + AI soát) | **0.0016** + 0.0007/video | 0.0014 + 0.0005 | 0.0018 + 0.013 | 0.0099 + 0.013 |
| blind-judge mean (refine setup) | 8.86 | 8.83 (8.80 draft-only) | 8.84 (best accuracy 9.20) | 8.66 |

## TTS (Vietnamese)

| id | engine | key (env) | local | notes |
|---|---|---|---|---|
| `edge` | edge-tts vi-VN | — | | **default**, free; HoaiMy (F) / NamMinh (M); needs edge-tts ≥ 7.3.1 (403 otherwise); also used for **per-line voice** (`voices.py` tags a line `f`/`m` from its pitch; `tts.synthesize(voice_by_gender=True)` picks the provider's female/male voice via `speakers.voice_for`; one `synthesize_many` batch per voice) |
| `fpt` | FPT.AI | `FPT_API_KEY` | | native VI voices (stub, P5) |
| `azure` | Azure Neural vi-VN | `AZURE_SPEECH_KEY` | | reliable + SSML rate (stub, P5) |
| `google` | Google Cloud TTS | `GOOGLE_APPLICATION_CREDENTIALS` | | neural vi-VN (stub, P5) |

The text a voice receives is `speak.spoken()` ([speak.py](../backend/app/pipeline/speak.py)), not the
subtitle: numbers, %, times, dates, currency, units and common abbreviations are read the
Vietnamese way, `...` becomes a pause, emoji are dropped, and the user's dictionary
(`<storage>/pronounce.json` < the series' < `<job>/pronounce.json`) overrides. The spoken text is
what the TTS cache key hashes (`tts._seg_hash`), so a dictionary edit re-voices only the lines it
changes. The same applies to the commentary track.

> Voice cloning (ElevenLabs / viXTTS / F5-TTS) is **out of scope for v1** — stock voice is enough.

## ASR (Chinese, with timestamps)

| id | engine | local | install | notes |
|---|---|---|---|---|
| `faster_whisper` | faster-whisper (large-v3) | ✓ | `.[asr]` | optional offline engine (was the default until 2026-10-01) — CPU/GPU, segment timestamps; CER 0.185 on a real Douyin vlog vs 0.128 for `audio_llm`. Models are ~0.5–3 GB. |
| `whisperx_funasr` | FunASR Paraformer (CN-native) | ✓ | `.[asr-accurate]` | **accuracy option** — CER ~1.7%, CPU-viable; needs `funasr` + torch. WhisperX word-alignment is a future refinement. **Implemented.** |
| `audio_llm` | audio LLM via chat (`input_audio`) + word timing | ✗ **uploads audio** | base deps | **default.** Text from a Chinese-native omni model (default `qwen/qwen3.8-omni-flash`), split into subtitle lines and aligned onto a Whisper **word** track (`align.align_text_to_words`): cloud `STT_TIMING_MODEL` (default `openai/whisper-large-v3`), or local faster-whisper when installed. Cloud vs local timing on the real vlog: cue start median 0.26 s vs 0.20 s. 240 s chunks cut at silences. |
| `cloud_stt` | `/audio/transcriptions` (Whisper-family API) | ✗ **uploads audio** | base deps | Text + segment timestamps in one call (default `openai/whisper-large-v3`). Models that reject `verbose_json` (gpt-4o-transcribe) are re-asked for `json` and get local word timing like `audio_llm`. 360 s chunks. |

**Timing.** When a GPU and Demucs are available the word track is measured on the separated voice
(`separate.speech_stem` → `vocals.wav`), not on the mix; the words are saved in `asr_words.json`
so "Đo lại thời gian" re-times without another upload. Lines Whisper could not anchor are placed
on voice islands — see [architecture.md](architecture.md#guessed-timing-voice-islands).

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

## OCR (burned-in Chinese subtitles)

| Engine | Where | Notes |
|---|---|---|
| `rapidocr` (default) | local GPU via DirectML (Windows), else CPU | free, uploads nothing; GPU ~0.1–0.2 s per frame (137 s video: 25 s vs 68 s on CPU, 97/97 cues identical), CPU ~0.25–0.5 s; misses stylised captions |
| `vision_llm` | OpenRouter, role `ocr` (default `qwen/qwen3.7-flash`) | **uploads the subtitle band** of every frame; opt-in in the GUI ("Cách đọc chữ") / `--ocr-engine vision_llm` |

Benchmark 2026-10-14, 441 frames (2 fps) of two real Douyin vlogs with burned-in captions, every
disagreement checked by eye: RapidOCR ~31 frames wrong (missed 心脏喷血, 就是这个巧克力外皮…,
junk characters); `qwen/qwen3.7-flash` 4 (2 frames of song credits, 鍵 instead of 键 twice) for
$0.0022; `qwen/qwen3.8-flash` 4 (credits, one 吃 short) for $0.0096; `qwen/qwen3.7-plus` 0 on the
frames it returned, $0.022; `z-ai/glm-5.3-flash` 0 on one video (many dropped requests). Prices
from the OpenRouter models API that day; images bill as prompt tokens. Real run of the stage on a
137 s video: 83 cues, $0.002, 22 s with 16 requests in flight (≈ 30 min / $0.1 for 3 hours).
Requests are ~50 KB tiles (numbered strips, cropped to the detected text band): bigger uploads to
OpenRouter were dropped by this network often that day.

### Free / local speed-ups — research + measurements 2026-10-16
Two web passes (Chinese repos & posts: videocr-PaddleOCR, VideOCR, RapidVideOCR + VideoSubFinder,
video-subtitle-extractor, Umi-OCR, PaddleOCR-json; RapidOCR/ORT/OpenVINO docs), then every idea
measured here on the same 441 real frames (2 Douyin vlogs, 2 fps, bottom-third strips) against
the current output. CPU: AMD Ryzen 9 7945HX; GPU: RTX 4070 Laptop.

| Idea (source) | Speed | Output vs current | Verdict |
|---|---|---|---|
| **DirectML** (`onnxruntime-directml` 1.24.4 instead of `onnxruntime-gpu`, `EngineConfig.onnxruntime.use_dml`) | 432→166 / 173→129 ms per frame; det 420→80 ms | **identical, 441/441 frames** | lossless; needs the package swap (the three ORT packages share one module) |
| Change-gated OCR on a binarised band (videocr-PaddleOCR, VideOCR) | saves 3–4 % of calls at a safe threshold | stricter thresholds lose real lines | useless on vlogs (moving bright backgrounds); fine for static backgrounds |
| Recognition-only on the subtitle row found once (RapidOCR `use_det=False`) | 11 ms per frame (~45×) | vlog 1: 34/34 lines, 92 % chars, some first chars lost; vlog 2: **no fixed row** (stylised captions move) → nothing | only with a per-frame fallback to full det |
| Smaller detection scale + 24 px padding + `unclip_ratio` 2.0 / `box_thresh` 0.3 (RapidOCR/PaddleOCR docs) | ~3× | adds junk from background patterns ("制 哈喽闺蜜们 福"), still drops end characters ("…换上" → "…换") | rejected |
| OpenVINO (RapidOCR `engine_type: openvino`) | PP-OCRv6 paper: ~3 % for v6 small; officially Intel-only | — | skipped (AMD CPU here) |
| VideoSubFinder → RapidOCR (RapidVideOCR) | VSF step ~10–30 min per 2 h film (third-party) | not measured | GPL-2.0 exe, CPU-only, last update 2023 |
| Chinese cloud free tiers (Baidu 1,000/mo, Tencent 1,000/mo, Alibaba 200/mo) | — | — | need Chinese real-name verification; paid rates exceed OpenRouter |
| WeChat OCR wrappers | — | — | no licence, uses WeChat's binary → avoid |

Bottom line: no free local trick gets near the API's accuracy on stylised captions; the one
lossless free speed-up is DirectML (≈ 1 h of OCR per 3-hour video instead of 1.5–4 h) — adopted
2026-10-16 (`onnxruntime-directml` replaces `onnxruntime-gpu`; Silero VAD output unchanged).

**Follow-up, same day, on the GPU build.** Ground truth: a synthetic "film" — 40 real Chinese lines
burned white-with-outline in a fixed bottom row over real moving footage (148 s).
- *Hybrid row reader* (recognition-only on the subtitle row as a change detector, full OCR on
  every change and every 8th frame, back to full OCR when lines leave the row): film 43.9 → 32.4 s
  (−26 %), 34/40 lines vs 35/40 for full OCR; vlogs −9/−10 % with 81/97 and 31/34 cues identical.
  On DirectML a rec-only call still costs ~30 ms and rec readings flicker as the background
  changes, so most frames still trigger a full read. **Not shipped.**
- *1 frame/s instead of 2*: film 45 → 23 s, 36/40 lines (2 fps: 35/40), start error median 0.28 s,
  max 0.8 s; vlog with short captions 97 → 82 cues. **Shipped as the opt-in "OCR nhanh: 1
  khung/giây"** (`ocr_fast`, CLI `--ocr-fps 1`) for films / series.

## Model catalog

`catalog/models.json` on the `main` branch of the vizsup repository lets the owner change role
defaults and package models without a new installer ([catalog.py](../backend/app/catalog.py)).
Every install fetches it once a day (8 s after start, `catalog.due()`; Settings → AI và mô hình →
"Kiểm tra ngay" forces it) with a plain GET — no key, nothing about the user is sent — checks it,
and caches it in `<storage>/_cache/model_catalog.json`. `VIZSUP_NO_UPDATE_CHECK=1` turns the
daily check off (the test suite sets it).

```json
{"schema": 1, "updated": "…", "app": {"latest": "0.1.0", "url": "https://github.com/orms147/vizsup/releases"},
 "role_defaults": {"draft": "<openrouter id>", …},
 "presets": {"economy": {"overrides": {"refine": "<id>"}, "label": "…", "hint": "…",
                         "usd_per_min": 0.0014, "usd_per_video": 0.0005}}}
```

Resolution: `MODEL_<ROLE>` (the user's own pick, always wins) → package (catalog patch over
`presets.PRESETS`, `presets.effective()`) → role default (catalog over `roles.ROLES`). A catalog
is used **only if all of this holds**, else the previous cache or the built-in tables stay
(`refresh()` never raises, `current()` re-validates the cache on read):

- `schema` is 1; roles are keys of `roles.ROLES` (package overrides: `presets.TEXT_ROLES`);
  packages are keys of `presets.PRESETS`; every model id looks like `vendor/name`;
- `usd_per_min` / `usd_per_video` are numbers in [0, 1); `label` / `hint` ≤ 400 characters and
  free of `<` (they are shown in rich-text labels);
- `app.latest` is a version number and `app.url` is a path under
  `https://github.com/orms147/vizsup/` without `..` (Settings and a notification say "Có vizsup X
  mới"; the user installs it themselves);
- against OpenRouter's public `/api/v1/models`: every id exists, input price ≤ **$5/M** and
  output ≤ **$25/M** tokens, and the `vision` / `ocr` roles need a model with image input.

Re-measure (`backend/scripts/cost_bench.py`) before changing a model; the file's own `note` says
so. Tests never reach the network (`refresh(get=…)` takes a fake getter).

## Connectivity (Settings dialog)
Providers may implement `test_connection() -> (ok, msg)` and `list_models() -> [ids]`.
Implemented for the OpenAI-compatible base (OpenRouter / DeepSeek / Qwen) and for
Claude + Gemini. The Settings dialog runs these on a daemon thread (no UI freeze, and a hanging
request never delays closing the app — `desktop/ui_thread.py`):
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

**Host routing (2026-10-31).** One model id is sold by many hosts at very different prices —
`deepseek/deepseek-v4.1-flash` by ~40, 0.013–0.6 $/M in and 0.14–2.4 out, half of them 4-bit.
Every chat request therefore also sends `provider: {sort: "price", quantizations: [int8, fp8,
fp16, bf16, fp32, unknown]}`: the cheapest host that is not 4/6-bit ("unknown" = the vendor's own
host, e.g. OpenAI, Alibaba, DeepSeek). Measured on a real episode: DeepSeek draft + brief ≈ ⅓ of
what OpenRouter's default balancing paid (it chose mxfp4 / CoreWeave hosts), same blind-judge
scores, a few seconds slower (docs/plan/G-cost-log.md). `sort: "price"` alone ranks by the
*input* price and picked a 4-bit host with an expensive output price — hence the filter. When no
host matches (404 "No endpoints found for the request with quantization…") the call is re-sent
without the filter and the model remembered for the process. `OPENROUTER_ROUTING=auto` (Settings →
AI và mô hình → "Máy chủ chạy model") restores OpenRouter's own choice. The host of each call is
counted in `usage.json` (`models.<id>.hosts`). Whisper word timing is billed the same on every
host ($0.0000075/s), so the `/audio/transcriptions` call is left alone.

**Promo model.** `OPENROUTER_PROMO_MODEL` is used *instead of* `OPENROUTER_MODEL` until
`OPENROUTER_PROMO_EXPIRES` (YYYY-MM-DD — the first day it is NOT used, same meaning as
OpenRouter's `expiration_date` in `/models`). From that date the app falls back on its own; it also
falls back, for the rest of the process, if the promo model answers "not found / no endpoints"
early. A malformed date disables the promo rather than running it indefinitely.

