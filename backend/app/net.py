"""Losing the internet mid-run: wait for it and carry on, instead of failing.

Downloads, cloud ASR, translation, the AI review and edge-tts all need the network.
Every stage is resumable (it skips what is already on disk; TTS / ASR / OCR keep
per-item caches), so when a run fails WHILE the machine is offline, the right move is
to wait until the connection is back and run it again — the work done so far is kept.

``is_online`` opens a TCP connection to a few well-known hosts (no data sent, a couple
of seconds at most). ``looks_like_network_error`` recognises connection failures by the
exception chain's type names and messages (requests / httpx / aiohttp / openai /
yt-dlp all word them differently) — a failure that is not about the network is never
retried here."""
from __future__ import annotations

import socket
import time
from collections.abc import Callable

PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53), ("openrouter.ai", 443))
POLL = 5.0                   # seconds between checks while offline

_NET_TYPES = {"ConnectionError", "ConnectError", "ConnectTimeout", "ConnectionResetError",
              "ConnectionAbortedError", "ConnectionRefusedError", "APIConnectionError",
              "APITimeoutError", "ClientConnectorError", "ClientConnectionError",
              "ServerDisconnectedError", "ClientOSError", "gaierror", "TimeoutError",
              "ReadTimeout", "ReadTimeoutError", "RemoteDisconnected", "ProtocolError",
              "NewConnectionError", "MaxRetryError", "WSServerHandshakeError",
              "TransportError", "NetworkError", "TimeoutException"}
_NET_WORDS = ("getaddrinfo", "name resolution", "failed to resolve", "temporary failure",
              "network is unreachable", "no route to host", "connection reset",
              "connection aborted", "connection refused", "timed out", "unable to connect",
              "cannot connect", "failed to establish", "không kết nối", "mất mạng",
              "[winerror 10051]", "[winerror 10060]", "[winerror 10065]", "[winerror 11001]",
              "unable to download", "transporterror", "connecterror")


def is_online(timeout: float = 2.0) -> bool:
    for host, port in PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def looks_like_network_error(exc: BaseException) -> bool:
    """True when ``exc`` or anything in its cause / context chain is a connection
    failure. Pure."""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if type(e).__name__ in _NET_TYPES:
            return True
        text = str(e).lower()
        if any(w in text for w in _NET_WORDS):
            return True
        e = e.__cause__ or e.__context__
    return False


def wait_online(*, cancel: Callable[[], bool] | None = None,
                on_wait: Callable[[float], None] | None = None,
                online: Callable[[], bool] | None = None,
                sleep: Callable[[float], None] | None = None) -> bool:
    """Block until the network is back (True) or ``cancel()`` (False). ``on_wait`` gets
    the seconds waited so far, about every POLL seconds."""
    online = online or is_online
    sleep = sleep or time.sleep
    start = time.monotonic()
    while True:
        if cancel and cancel():
            return False
        if online():
            return True
        if on_wait:
            on_wait(time.monotonic() - start)
        waited = 0.0
        while waited < POLL:                 # stay responsive to Hủy
            if cancel and cancel():
                return False
            sleep(0.5)
            waited += 0.5


def run_resilient(body: Callable[[], object], *, cancel: Callable[[], bool] | None = None,
                  on_wait: Callable[[float], None] | None = None,
                  on_back: Callable[[], None] | None = None,
                  online: Callable[[], bool] | None = None,
                  sleep: Callable[[float], None] | None = None,
                  max_rounds: int = 50, blip_retries: int = 2):
    """Run ``body`` (a resumable stage); when it fails with a connection error while
    the machine is offline, wait for the connection and run it again. A connection
    error while still online (a blip) is retried ``blip_retries`` times after a short
    pause; any other error, or a cancel during the wait, propagates at once. Returns
    what ``body`` returns."""
    online = online or (lambda: is_online())
    sleep = sleep or (lambda s: time.sleep(s))
    blips = 0
    for _ in range(max_rounds):
        try:
            return body()
        except (InterruptedError, KeyboardInterrupt):
            raise
        except Exception as exc:  # noqa: BLE001 - classified below, else re-raised
            if not looks_like_network_error(exc):
                raise                    # a real error: report it now, don't wait
            if online():
                if blips >= blip_retries:
                    raise
                blips += 1
                sleep(3.0)
                continue
            if not wait_online(cancel=cancel, on_wait=on_wait, online=online, sleep=sleep):
                raise InterruptedError("Đã hủy.") from exc
            blips = 0
            if on_back:
                on_back()
    return body()
