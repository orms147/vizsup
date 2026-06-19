---
name: add-provider
description: Scaffold a new translation, TTS, or ASR provider for vizsup following the pluggable interface. Use when the user wants to add/integrate a new model or voice engine (e.g. "add Gemini translation", "add FPT TTS", "wire up SenseVoice ASR").
---

# Add a provider

Providers are pluggable and UI-selectable. Adding one = **one file + one registry line**. Never hardcode an engine in pipeline code.

## Steps

1. **Pick the stage** and read the interface in [backend/app/providers/base.py](../../../backend/app/providers/base.py):
   - `TranslationProvider.translate_texts(texts, *, source_lang, target_lang, context, glossary) -> list[str]` (text only — timecodes are stripped/re-attached by the pipeline; preserve order & count).
   - `TTSProvider.synthesize(text, out_path, *, voice, rate) -> Path`.
   - `ASRProvider.transcribe(media, *, language) -> list[Cue]`.

2. **Create the file** under the matching folder: `backend/app/providers/{translation,tts,asr}/<name>.py`.
   - Set class attrs: `name` (stable id), `display_name`, `cost_note`, `local`.
   - Implement `available()` — return True only if the key/dependency is present. Use `_installed("pkg")` and read keys from `app.config.settings`. **Import the SDK lazily inside the work method**, never at module top (keeps the registry importable without the SDK).
   - Add the new key (if any) to `app/config.py` `Settings` and to `.env.example`.

3. **Register it** in [backend/app/providers/registry.py](../../../backend/app/providers/registry.py) — import the class and add an instance to `TRANSLATORS` / `TTS_PROVIDERS` / `ASR_PROVIDERS`.

4. **Mirror existing style.** For OpenAI-compatible chat endpoints, subclass `OpenAICompatTranslator` (see `deepseek.py`, `qwen.py`, `glm.py`) — just set `base_url`, `model`, and an `api_key` property.

5. **Verify**: `python -c "from app.providers.registry import list_providers; print(list_providers())"` shows the new provider with correct `available`.

## Notes
- Verify model ids / pricing on the web — don't trust memory. Current ids: `deepseek-v4-flash`/`deepseek-v4-pro`, `claude-opus-4-8`/`claude-sonnet-4-6`, `glm-5.2`.
- TTS voices: expose them via `voices()` so the UI can list them.
- Update [docs/providers.md](../../../docs/providers.md) with the new row.
