"""Central settings, loaded from environment / .env.

Only providers whose key is set are reported as `available()` by the registry,
so the UI can show exactly the engines the user can actually run.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # translation provider keys
    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    zhipu_api_key: str = ""
    gemini_api_key: str = ""
    dashscope_api_key: str = ""
    deepl_api_key: str = ""

    # tts provider keys (edge-tts needs none)
    fpt_api_key: str = ""
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    google_application_credentials: str = ""
    elevenlabs_api_key: str = ""

    # download
    ytdlp_cookies_file: str = ""
    ytdlp_cookies_from_browser: str = ""

    # paths / defaults
    vizsup_storage_dir: Path = Path("./storage")
    vizsup_default_translator: str = "deepseek"
    vizsup_default_tts: str = "edge"
    vizsup_default_asr: str = "whisperx_funasr"


settings = Settings()
