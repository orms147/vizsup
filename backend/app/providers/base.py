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
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


_SPEAKABLE = re.compile(r"[0-9A-Za-zÀ-ɏḀ-ỿ]")


def has_speakable(text: str) -> bool:
    """Anything a Vietnamese voice can pronounce (Latin letters incl. diacritics, digits).
    A TTS "no audio" answer for such a line is the service throttling, not the text."""
    return bool(_SPEAKABLE.search(text or ""))


class Provider(ABC):
    name: str = ""           # stable id used in config/UI
    display_name: str = ""   # human label for the UI
    cost_note: str = ""      # short cost/quality hint for the UI
    local: bool = False      # runs offline / on this machine
    unavailable_note: str = ""  # why available() is False (UI suffix; "" → "cần API key")

    def available(self) -> bool:  # noqa: D401 - simple predicate
        return True

    def info(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "cost_note": self.cost_note,
            "local": self.local,
            "available": self.available(),
            "unavailable_note": self.unavailable_note,
        }

    def unload(self) -> None:
        """Release any cached heavy model (VRAM). No-op default; local model
        providers override so sequential stages fit on the 8 GB card."""

    # Optional capabilities (overridden by providers that support them).
    def list_models(self) -> list[str]:
        """Return selectable model ids for this provider (empty if N/A)."""
        return []

    def test_connection(self) -> tuple[bool, str]:
        """Verify credentials/connectivity. Returns (ok, message)."""
        return False, "Test kết nối chưa hỗ trợ cho provider này."


class TranslationProvider(Provider):
    """Translate a list of strings, preserving order and count (1:1 with cues).

    Capability flags are DECLARED, not discovered: the base refine/proofread
    methods are safe no-ops, so callers can't tell support from silence — the
    flags let the UI grey out engines that would quietly do nothing (a no-op
    refine pass used to log "✓ Đã rà soát N dòng" without touching a line).
    Subclasses that override refine_texts/proofread_texts must set these True
    (locked by tests/test_capabilities.py).
    """

    can_refine: bool = False     # overrides refine_texts with a real editor pass
    can_proofread: bool = False  # overrides proofread_texts with a real OCR fix pass
    parallel_batches: int = 1    # how many batches may run concurrently (API-9);
    #                              1 = strictly sequential (default, safe for any engine)

    def info(self) -> dict:
        return {**super().info(), "can_refine": self.can_refine,
                "can_proofread": self.can_proofread}

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

    supports_batch: bool = False   # implements synthesize_many (concurrent, one loop)

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

    def synthesize_many(
        self,
        items: list[tuple[str, str, Path]],   # (key, text, out_path)
        *,
        voice: str | None = None,
        rate: str | None = None,
        concurrency: int = 3,
        retry_delays: tuple[float, ...] = (1.0, 4.0),
        on_done: Callable[[str, Exception | None], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> list[tuple[str, Exception | None]]:
        """Synthesize many segments concurrently on ONE event loop (bounded by
        ``concurrency``), reusing a single connection pool. ``on_done(key, exc)`` is
        invoked on the loop thread as each item finishes — ``exc is None`` on success,
        a provider error on a real failure, or InterruptedError when ``cancel()``
        stopped the item before any network work. Returns ``[(key, exc), …]``. Only
        providers with ``supports_batch = True`` override this."""
        raise NotImplementedError

    def voices(self) -> list[str]:
        return []


class ASRProvider(Provider):
    """Transcribe audio/video to timed cues (Chinese, with timestamps)."""

    @abstractmethod
    def transcribe(self, media: Path, *, language: str = "zh", log=None):  # -> list[Cue]
        """``log``: optional progress callback (one human-readable line per call).
        Cloud engines report per-chunk progress — a 5-minute video otherwise sits
        silent for over a minute and looks frozen."""
        ...


class OCRProvider(Provider):
    """Read the burned-in subtitle off cropped video frames (BGR numpy arrays)."""

    batch_frames: int = 50       # frames per read call; the OCR stage checkpoints between calls
    crop_to_band: bool = False   # wants only the detected text band (hardsub_region.json)

    def cache_tag(self) -> str:
        """Part of the OCR checkpoint key: reads from another engine/model are not reused."""
        return self.name

    @abstractmethod
    def reader(self, *, log=None, cancel=None):
        """Context manager for one OCR run, yielding ``read(images) -> lines per image``
        where lines are ``[(text, confidence 0..1), …]`` (``[]`` = no text). Holds the
        per-run state (engines, worker threads, HTTP client) for the whole video."""
        ...


# --- publishing (autopilot, 2026-10-31) ------------------------------------------------------

@dataclass
class PostMeta:
    """What a platform needs for one post. ``kind``: "long" (the episode / a compilation) or
    "short" (9:16). ``publish_at``: UTC epoch seconds for a scheduled post (None = now)."""
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    kind: str = "long"
    publish_at: float | None = None
    thumbnail: Path | None = None
    playlist: str = ""              # series name — a playlist / collection where supported


@dataclass
class PostResult:
    ok: bool
    status: str = ""                # "published" | "scheduled" | "private" (unaudited app) | "draft" | "failed"
    url: str = ""
    remote_id: str = ""
    message: str = ""               # Vietnamese, for the UI / log


class PublishProvider(Provider):
    """Posts a rendered video on one platform for the owner (official APIs only, the owner
    connects their own account once). Credentials live under ``storage/_accounts/`` — never
    in the repo, never printed. ``kinds``: which of "long" / "short" this platform takes."""

    kinds: tuple[str, ...] = ("long", "short")

    def connected(self) -> bool:
        """An account is linked (credentials present and not revoked as far as we know)."""
        return False

    def account_label(self) -> str:
        """Who/what is linked, e.g. the channel or Page name ("" when not connected)."""
        return ""

    @abstractmethod
    def publish(self, video: Path, meta: PostMeta, *,
                progress: Callable[[float], None] | None = None) -> PostResult:
        ...
