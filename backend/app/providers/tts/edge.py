"""edge-tts — free Microsoft neural Vietnamese voices (vi-VN). The v1 default.

Voices: vi-VN-HoaiMyNeural (female), vi-VN-NamMinhNeural (male).
Note: occasionally returns HTTP 403; retry/fallback if it does.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.providers.base import TTSProvider, _installed

DEFAULT_VOICE = "vi-VN-HoaiMyNeural"
VOICES = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]


def _no_audio(exc: Exception) -> bool:
    # NoAudioReceived = the voice can't speak this text (deterministic) — matched by
    # class name to keep the retry decision free of an edge_tts import at the top.
    return type(exc).__name__ == "NoAudioReceived"


def _guard_no_running_loop(where: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        raise RuntimeError(f"EdgeTTS.{where}() called inside a running event loop; use the async path.")


class EdgeTTS(TTSProvider):
    name = "edge"
    display_name = "edge-tts (vi-VN)"
    cost_note = "free · HoaiMy / NamMinh · no key"
    local = False  # uses Microsoft's online endpoint, but free + no key
    supports_batch = True   # synthesize_many below: one loop, shared pool, ≤3-wide (API-10)

    def available(self) -> bool:
        return _installed("edge_tts")

    def voices(self) -> list[str]:
        return VOICES

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        import edge_tts

        out_path = Path(out_path)
        voice = voice or DEFAULT_VOICE
        rate = rate or "+0%"

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
            await communicate.save(str(out_path))

        _guard_no_running_loop("synthesize")
        asyncio.run(_run())
        return out_path

    def synthesize_many(self, items, *, voice=None, rate=None, concurrency=3,
                        retry_delays=(1.0, 4.0), on_done=None, cancel=None):
        """One asyncio.run for the whole job: a single aiohttp connection pool +
        Semaphore(concurrency) instead of a fresh WSS/TLS handshake per cue. The
        per-item retry ladder mirrors the pipeline's (transient 403 → retry;
        NoAudioReceived → record immediately). cancel() before any network work
        records InterruptedError; after the first fatal (retries-exhausted, not
        NoAudioReceived) we stop admitting new items so a dead endpoint fails fast."""
        import asyncio as _asyncio

        import aiohttp
        import edge_tts

        voice = voice or DEFAULT_VOICE
        rate = rate or "+0%"
        _guard_no_running_loop("synthesize_many")
        results: list[tuple[str, Exception | None]] = []

        async def _run() -> None:
            connector = aiohttp.TCPConnector(limit=concurrency)
            try:
                sem = _asyncio.Semaphore(concurrency)
                stop = _asyncio.Event()   # set after the first fatal → stop admitting

                async def _one(key: str, text: str, out_path) -> tuple[str, Exception | None]:
                    if (cancel and cancel()) or stop.is_set():
                        return key, InterruptedError()   # no network work started
                    async with sem:
                        if (cancel and cancel()) or stop.is_set():
                            return key, InterruptedError()
                        for delay in (*retry_delays, None):
                            try:
                                comm = edge_tts.Communicate(
                                    text, voice=voice, rate=rate, connector=connector)
                                await comm.save(str(out_path))
                                return key, None
                            except Exception as exc:  # noqa: BLE001
                                if _no_audio(exc):
                                    return key, exc      # deterministic → no retry
                                if delay is None:
                                    stop.set()           # dead endpoint → stop admitting
                                    return key, exc
                                await _asyncio.sleep(delay)
                        return key, None                 # unreachable (loop always returns)

                tasks = [_asyncio.create_task(_one(k, t, p)) for k, t, p in items]
                for fut in _asyncio.as_completed(tasks):
                    key, exc = await fut
                    results.append((key, exc))
                    if on_done:
                        on_done(key, exc)                # loop-thread → serialized
            finally:
                await connector.close()

        asyncio.run(_run())
        return results
