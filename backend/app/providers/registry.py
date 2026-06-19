"""Provider registry — the single source of truth for what the UI offers.

``list_providers()`` returns metadata (incl. `available`) for every stage so the
frontend can render dropdowns and grey out engines whose key/dependency is missing.
"""
from __future__ import annotations

from app.providers.asr.faster_whisper import FasterWhisperASR
from app.providers.asr.whisperx_funasr import WhisperXFunASR
from app.providers.translation.claude import ClaudeOpus, ClaudeTranslator
from app.providers.translation.deepl import DeepLTranslator
from app.providers.translation.deepseek import DeepSeekFlash, DeepSeekPro
from app.providers.translation.gemini import GeminiTranslator
from app.providers.translation.glm import GLM
from app.providers.translation.openrouter import OpenRouter
from app.providers.translation.passthrough import PassthroughTranslator
from app.providers.translation.qwen import Qwen3
from app.providers.tts.azure import AzureTTS
from app.providers.tts.edge import EdgeTTS
from app.providers.tts.fpt import FptTTS
from app.providers.tts.google import GoogleTTS

TRANSLATORS = {
    p.name: p
    for p in (
        PassthroughTranslator(),
        OpenRouter(),
        DeepSeekFlash(),
        DeepSeekPro(),
        ClaudeTranslator(),
        ClaudeOpus(),
        GLM(),
        GeminiTranslator(),
        Qwen3(),
        DeepLTranslator(),
    )
}

TTS_PROVIDERS = {
    p.name: p
    for p in (
        EdgeTTS(),
        FptTTS(),
        AzureTTS(),
        GoogleTTS(),
    )
}

ASR_PROVIDERS = {
    p.name: p
    for p in (
        WhisperXFunASR(),
        FasterWhisperASR(),
    )
}


def list_providers() -> dict:
    return {
        "translation": [p.info() for p in TRANSLATORS.values()],
        "tts": [p.info() for p in TTS_PROVIDERS.values()],
        "asr": [p.info() for p in ASR_PROVIDERS.values()],
    }


def find_provider(name: str):
    """Look up a provider by name across all stages (for Settings test/fetch)."""
    for d in (TRANSLATORS, TTS_PROVIDERS, ASR_PROVIDERS):
        if name in d:
            return d[name]
    return None


def get_translator(name: str):
    if name not in TRANSLATORS:
        raise KeyError(f"Unknown translator '{name}'. Options: {sorted(TRANSLATORS)}")
    return TRANSLATORS[name]


def get_tts(name: str):
    if name not in TTS_PROVIDERS:
        raise KeyError(f"Unknown TTS provider '{name}'. Options: {sorted(TTS_PROVIDERS)}")
    return TTS_PROVIDERS[name]


def get_asr(name: str):
    if name not in ASR_PROVIDERS:
        raise KeyError(f"Unknown ASR provider '{name}'. Options: {sorted(ASR_PROVIDERS)}")
    return ASR_PROVIDERS[name]
