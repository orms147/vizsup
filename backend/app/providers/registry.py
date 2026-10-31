"""Provider registry — the single source of truth for what the UI offers.

``list_providers()`` returns metadata (incl. `available`) for every stage so the
frontend can render dropdowns and grey out engines whose key/dependency is missing.
"""
from __future__ import annotations

from app.providers.asr.audio_llm import AudioLLM
from app.providers.asr.cloud_stt import CloudSTT
from app.providers.asr.faster_whisper import FasterWhisperASR
from app.providers.asr.funasr_aligned import FunASRAligned
from app.providers.asr.whisperx_funasr import WhisperXFunASR
from app.providers.ocr.rapid import RapidOCRLocal
from app.providers.ocr.vision_llm import VisionLLMOCR
from app.providers.translation.claude import ClaudeOpus, ClaudeTranslator
from app.providers.translation.deepl import DeepLTranslator
from app.providers.translation.deepseek import DeepSeekFlash, DeepSeekPro
from app.providers.translation.gemini import GeminiTranslator
from app.providers.translation.glm import GLM
from app.providers.translation.openrouter import OpenRouter
from app.providers.translation.passthrough import PassthroughTranslator
from app.providers.translation.qwen import Qwen
from app.providers.tts.azure import AzureTTS
from app.providers.tts.edge import EdgeTTS
from app.providers.tts.fpt import FptTTS
from app.providers.tts.google import GoogleTTS
from app.providers.tts.vieneu import VieNeuTTS
from app.providers.publish.facebook import FacebookPublisher
from app.providers.publish.folder import FolderPublisher
from app.providers.publish.tiktok import TikTokPublisher
from app.providers.publish.youtube import YouTubePublisher

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
        Qwen(),
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
        VieNeuTTS(),
    )
}

PUBLISHERS = {
    p.name: p
    for p in (
        FolderPublisher(),      # always there: export a post package, post by hand
        YouTubePublisher(),
        FacebookPublisher(),
        TikTokPublisher(),
    )
}

VOICE_SEP = ":"          # "vieneu:Trúc Ly" — a voice that names its own provider


def split_voice(provider: str, voice: str | None) -> tuple[str, str | None]:
    """(provider, voice) for a voice from voice_choices(): "vieneu:Trúc Ly" → ("vieneu",
    "Trúc Ly"); anything else keeps ``provider``. Pure."""
    if voice and VOICE_SEP in voice:
        head, rest = voice.split(VOICE_SEP, 1)
        if head in TTS_PROVIDERS:
            return head, rest
    return provider, voice


def voice_choices(provider: str) -> list[str]:
    """The render page's voice list: ``provider``'s voices, then the voices of every
    installed local provider (``local_voices``, free) as "name:voice"."""
    try:
        out = list(get_tts(provider).voices() or [])
    except Exception:  # noqa: BLE001 - an unknown provider lists nothing of its own
        out = []
    return out + local_voice_choices(provider)


_KNOWN_NAMES = {"HoaiMy": "Hoài My", "NamMinh": "Nam Minh"}


def voice_label(v: str) -> str:
    """Display name: vi-VN-HoaiMyNeural → Hoài My, fr-FR-VivienneMultilingualNeural →
    Vivienne (đa ngôn ngữ), vieneu:Trúc Ly → Trúc Ly · trên máy; else unchanged. Pure."""
    head, _sep, rest = v.partition(VOICE_SEP)
    if rest and head in TTS_PROVIDERS:
        return f"{rest} · trên máy" if getattr(TTS_PROVIDERS[head], "local_voices", False)             else f"{rest} · {head}"
    if "MultilingualNeural" in v:
        return f"{v.split('-')[2].replace('MultilingualNeural', '')} (đa ngôn ngữ)"
    core = v.replace("vi-VN-", "").replace("Neural", "")
    return _KNOWN_NAMES.get(core, core or v)


def local_voice_choices(provider: str) -> list[str]:
    """The installed local providers' voices as "name:voice" (none for ``provider``)."""
    out: list[str] = []
    for name, p in TTS_PROVIDERS.items():
        if name != provider and getattr(p, "local_voices", False) and p.available():
            out += [f"{name}{VOICE_SEP}{v}" for v in p.voices()]
    return out

ASR_PROVIDERS = {
    p.name: p
    for p in (
        AudioLLM(),           # default — first available is what the GUI pre-selects
        CloudSTT(),
        FunASRAligned(),      # local engines: optional extras
        WhisperXFunASR(),
        FasterWhisperASR(),
    )
}


OCR_PROVIDERS = {
    p.name: p
    for p in (
        RapidOCRLocal(),      # default: local, uploads nothing
        VisionLLMOCR(),       # opt-in: uploads the subtitle band
    )
}


def list_providers() -> dict:
    return {
        "translation": [p.info() for p in TRANSLATORS.values()],
        "tts": [p.info() for p in TTS_PROVIDERS.values()],
        "asr": [p.info() for p in ASR_PROVIDERS.values()],
        "ocr": [p.info() for p in OCR_PROVIDERS.values()],
        "publish": [p.info() for p in PUBLISHERS.values()],
    }


def find_provider(name: str):
    """Look up a provider by name across all stages (for Settings test/fetch)."""
    for d in (TRANSLATORS, TTS_PROVIDERS, ASR_PROVIDERS, OCR_PROVIDERS):
        if name in d:
            return d[name]
    return None


def get_translator(name: str):
    if name not in TRANSLATORS:
        raise KeyError(f"Unknown translator '{name}'. Options: {sorted(TRANSLATORS)}")
    return TRANSLATORS[name]


def get_publisher(name: str):
    if name not in PUBLISHERS:
        raise KeyError(f"Unknown publisher '{name}'. Options: {sorted(PUBLISHERS)}")
    return PUBLISHERS[name]


def get_tts(name: str):
    if name not in TTS_PROVIDERS:
        raise KeyError(f"Unknown TTS provider '{name}'. Options: {sorted(TTS_PROVIDERS)}")
    return TTS_PROVIDERS[name]


def get_asr(name: str):
    if name not in ASR_PROVIDERS:
        raise KeyError(f"Unknown ASR provider '{name}'. Options: {sorted(ASR_PROVIDERS)}")
    return ASR_PROVIDERS[name]


def get_ocr(name: str):
    if name not in OCR_PROVIDERS:
        raise KeyError(f"Unknown OCR engine '{name}'. Options: {sorted(OCR_PROVIDERS)}")
    return OCR_PROVIDERS[name]
