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

    # Optional capabilities (overridden by providers that support them).
    def list_models(self) -> list[str]:
        """Return selectable model ids for this provider (empty if N/A)."""
        return []

    def test_connection(self) -> tuple[bool, str]:
        """Verify credentials/connectivity. Returns (ok, message)."""
        return False, "Test kết nối chưa hỗ trợ cho provider này."


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

    def refine_texts(
        self,
        cn_texts: list[str],
        vi_texts: list[str],
        *,
        context: dict | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        """Second-pass polish: improve a Vietnamese draft against its Chinese source.

        The two-pass design drafts with a CN-native model then refines with a
        frontier model. A refiner must see BOTH the source and the draft — a plain
        ``translate_texts`` call would re-run CN→VI on Vietnamese input. Providers
        with a chat endpoint override this to run a real editor pass; the default is
        a no-op (returns the draft unchanged) so a non-chat engine can't corrupt it.
        Must return one line per input, same order and count as ``vi_texts``.
        """
        return list(vi_texts)

    def proofread_texts(
        self,
        texts: list[str],
        *,
        context: dict | None = None,
    ) -> list[str]:
        """Opt-in OCR cleanup: fix character-recognition errors in already-Chinese
        lines WITHOUT translating (text only — no frames leave the machine). Default
        is a no-op; chat providers override. One line per input, same order/count."""
        return list(texts)


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
