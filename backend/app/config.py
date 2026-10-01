"""Central settings, loaded from environment / .env.

Only providers whose key is set are reported as `available()` by the registry,
so the UI can show exactly the engines the user can actually run.

The .env file and the default storage dir are anchored to the REPO ROOT, not the
current working directory: a CWD-relative ".env" silently loses every key when
the app/CLI is launched from backend/ instead of the root (this bit — the job
storage on this machine had already split between <root>/storage and
backend/storage). Env vars still override everything.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]   # backend/app/config.py → repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ROOT / ".env"),
                                      env_file_encoding="utf-8", extra="ignore")

    # translation provider keys
    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    zhipu_api_key: str = ""
    zhipu_model: str = ""  # override the GLM model id if Zhipu rolls it (default glm-5.2)
    gemini_api_key: str = ""
    dashscope_api_key: str = ""
    deepl_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = ""  # e.g. "deepseek/deepseek-chat-v3"; pick via Settings → Fetch models
    # Time-boxed model (e.g. a free "stealth" model) used INSTEAD of openrouter_model
    # until it expires; from that date on the app falls back on its own. The date is
    # the first day it is NOT used — same meaning as OpenRouter's `expiration_date`.
    openrouter_promo_model: str = ""
    openrouter_promo_expires: str = ""  # YYYY-MM-DD; blank = no expiry

    # tts provider keys (edge-tts needs none)
    fpt_api_key: str = ""
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    google_application_credentials: str = ""

    # cloud speech-to-text (opt-in: these providers UPLOAD the audio). Base URL and key
    # default to OpenRouter, so the one OpenRouter key already covers it; point them at
    # Groq / OpenAI / SiliconFlow … to use a vendor directly.
    stt_api_base_url: str = ""   # blank → https://openrouter.ai/api/v1
    stt_api_key: str = ""        # blank → OPENROUTER_API_KEY when the base is OpenRouter
    stt_api_model: str = ""      # /audio/transcriptions model (cloud_stt); blank → default
    audio_llm_model: str = ""    # chat model that listens to audio (audio_llm); blank → default

    # download
    ytdlp_cookies_file: str = ""
    ytdlp_cookies_from_browser: str = ""
    bbdown_path: str = ""  # BBDown.exe — optional Bilibili fallback when yt-dlp fails

    # paths / defaults
    ffmpeg_path: str = ""  # explicit ffmpeg(.exe) or its bin dir; empty → resolve from PATH
    vizsup_storage_dir: Path = _ROOT / "storage"
    vizsup_default_translator: str = "deepseek"
    vizsup_default_tts: str = "edge"
    vizsup_default_asr: str = "faster_whisper"
    whisper_model: str = "large-v3"  # faster-whisper model size; set WHISPER_MODEL=tiny to downsize


settings = Settings()
