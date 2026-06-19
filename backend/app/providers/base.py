"""Provider interfaces. Three pluggable stages: translation, TTS, ASR.

Design rules:
- Providers do their SDK import lazily (inside the work method), so importing
  the registry never fails just because an optional SDK isn't installed.
- ``available()`` reflects whether the provider can actually run right now
  (key present and/or dependency importable). The UI lists everything but only
  enables what's available.
- Translation providers operate on *text only* — the pipeline strips and
  re-attaches timecodes, so models never see timing data.
"""
from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from pathlib import Path


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


class Provider(ABC):
    name: str = ""           # stable id used in config/UI
    display_name: str = ""   # human label for the UI
    cost_note: str = ""      # short cost/quality hint for the UI
    local: bool = False      # runs offline / on this machine

    def available(self) -> bool:  # noqa: D401 - simple predicate
        return True

    def info(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "cost_note": self.cost_note,
            "local": self.local,
            "available": self.available(),
        }


class TranslationProvider(Provider):
    """Translate a list of strings, preserving order and count (1:1 with cues)."""

    @abstractmethod
    def translate_texts(
        self,
        texts: list[str],
        *,
        source_lang: str = "zh",
        target_lang: str = "vi",
        context: dict | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        ...


class TTSProvider(Provider):
    """Synthesize one line of speech to ``out_path``. Return the written path."""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        voice: str | None = None,
        rate: str | None = None,
    ) -> Path:
        ...

    def voices(self) -> list[str]:
        return []


class ASRProvider(Provider):
    """Transcribe audio/video to timed cues (Chinese, with timestamps)."""

    @abstractmethod
    def transcribe(self, media: Path, *, language: str = "zh"):  # -> list[Cue]
        ...
