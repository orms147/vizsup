"""edge-tts — free Microsoft neural Vietnamese voices (vi-VN). The v1 default.

Voices: vi-VN-HoaiMyNeural (female), vi-VN-NamMinhNeural (male).
Note: occasionally returns HTTP 403; retry/fallback if it does.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.providers.base import TTSProvider, _installed, has_speakable

DEFAULT_VOICE = "vi-VN-HoaiMyNeural"
VOICES = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]


def _no_audio(exc: Exception) -> bool:
    # NoAudioReceived — matched by class name to keep the retry decision free of an
    # edge_tts import at the top. Deterministic ONLY for text the voice can't speak
    # (pure Chinese on a vi-VN voice); for a normal line it is Microsoft throttling a
    # burst (2026-10-27: 417 of 717 lines came back empty, then worked on a retry).
    return type(exc).__name__ == "NoAudioReceived"


THROTTLE_DELAYS = (5.0, 15.0, 30.0, 60.0)   # "no audio" on a speakable line: back off
GIVE_UP_AFTER = 5     # lines in a row still empty after every retry → stop the batch
# Oct 2026: edge-tts's endpoint answers many valid requests with no audio (rany2/edge-tts
# #473; ~1 in 4 succeeded in mid-October) — patience works, hammering does not.
SAVE_TIMEOUT = 90.0                  # one line never takes this long; a stuck socket does
CANCEL_POLL = 0.5                    # how often the batch checks the render's Hủy


class TTSThrottled(RuntimeError):
    """edge-tts kept answering "no audio" for normal text — the service is limiting us."""


def _keep_alive_connector(limit: int):
    """A TCPConnector whose close() is a NO-OP. edge_tts opens a fresh
    ClientSession(connector_owner=True) for EVERY save(), so a plain shared
    connector would be torn down the moment the first segment's session exits —
    every later cue then fails with "Session is closed" and the batch stage crashes
    after one segment. Neutralizing close() lets the pool (and its TLS keep-alive)
    survive the whole job; call shutdown() once at the end to close it for real."""
    import aiohttp

    class _KeepAliveConnector(aiohttp.TCPConnector):
        async def close(self) -> None:      # ignore edge_tts's per-save session teardown
            return

        async def shutdown(self) -> None:
            await super().close()

    return _KeepAliveConnector(limit=limit)


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

        import edge_tts

        voice = voice or DEFAULT_VOICE
        rate = rate or "+0%"
        _guard_no_running_loop("synthesize_many")
        results: list[tuple[str, Exception | None]] = []

        async def _run() -> None:
            connector = _keep_alive_connector(concurrency)
            try:
                sem = _asyncio.Semaphore(concurrency)
                stop = _asyncio.Event()   # set after the first fatal → stop admitting
                inflight: set = set()     # tasks holding a slot (the only ones Hủy cancels)
                loop = _asyncio.get_running_loop()
                cool = {"until": 0.0}     # shared: one throttled answer pauses EVERY worker
                streak = {"n": 0}         # consecutive lines that stayed empty after retries

                async def _one(key: str, text: str, out_path) -> tuple[str, Exception | None]:
                    try:
                        return await _attempts(key, text, out_path)
                    except _asyncio.CancelledError:      # Hủy while a request was in flight
                        return key, InterruptedError()

                async def _attempts(key: str, text: str, out_path):
                    if (cancel and cancel()) or stop.is_set():
                        return key, InterruptedError()   # no network work started
                    async with sem:
                        if (cancel and cancel()) or stop.is_set():
                            return key, InterruptedError()
                        me = _asyncio.current_task()
                        inflight.add(me)
                        try:
                            return await _speak(key, text, out_path)
                        finally:
                            inflight.discard(me)

                async def _speak(key: str, text: str, out_path):
                    delays = list(retry_delays)
                    throttled = 0
                    while True:
                        wait = cool["until"] - loop.time()
                        if wait > 0:
                            await _asyncio.sleep(wait)
                        try:
                            comm = edge_tts.Communicate(
                                text, voice=voice, rate=rate, connector=connector)
                            # 2026-10-27: under throttling a request hung for good and
                            # froze the render (and its Hủy) at "TTS 20%"
                            await _asyncio.wait_for(comm.save(str(out_path)), SAVE_TIMEOUT)
                            streak["n"] = 0
                            return key, None
                        except Exception as exc:  # noqa: BLE001
                            if _no_audio(exc):
                                if not has_speakable(text):
                                    return key, exc  # nothing to say → skip, no retry
                                if throttled >= len(THROTTLE_DELAYS):
                                    streak["n"] += 1
                                    if streak["n"] >= GIVE_UP_AFTER:
                                        stop.set()   # sustained limiting → stop, keep done
                                    return key, TTSThrottled(
                                        "máy chủ giọng đọc (edge-tts) đang giới hạn — "
                                        "không trả âm thanh")
                                pause = THROTTLE_DELAYS[throttled]
                                cool["until"] = max(cool["until"], loop.time() + pause)
                                throttled += 1
                                continue                 # waits out the shared cooldown
                            if not delays:
                                stop.set()           # dead endpoint → stop admitting
                                return key, exc
                            await _asyncio.sleep(delays.pop(0))

                tasks = [_asyncio.create_task(_one(k, t, p)) for k, t, p in items]

                async def _watch_cancel() -> None:
                    while cancel and not all(t.done() for t in tasks):
                        if cancel():
                            stop.set()                   # queued ones return on their own
                            for t in list(inflight):
                                t.cancel()               # in-flight requests too
                            return
                        await _asyncio.sleep(CANCEL_POLL)

                watcher = _asyncio.create_task(_watch_cancel())
                for fut in _asyncio.as_completed(tasks):
                    key, exc = await fut
                    results.append((key, exc))
                    if on_done:
                        on_done(key, exc)                # loop-thread → serialized
                watcher.cancel()
            finally:
                await connector.shutdown()               # close the shared pool for real

        asyncio.run(_run())
        return results
