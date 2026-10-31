"""Shared plumbing of the platform publishers (YouTube / Facebook / TikTok).

- **Linked accounts**: one JSON file per platform under ``<storage>/_accounts/`` (gitignored
  with storage/), written atomically. Tokens are never printed or logged — error messages
  carry the platform's own error text, never a request URL or header.
- **OAuth 2.0 installed-app flow** (RFC 8252): a one-shot HTTP server on
  ``http://127.0.0.1:<random free port><path>`` catches the redirect, PKCE (RFC 7636) S256,
  the system browser opened with ``webbrowser.open``. Runs on a background thread; the caller
  passes ``cancel`` so "Hủy" in Settings stops the wait.
- **HTTP**: httpx (no SDKs). Each publisher has a ``transport`` attribute — tests set an
  ``httpx.MockTransport`` there, so no test ever reaches the network.

Failure contract of ``publish()``: a platform refusal (quota, permission, bad file …) comes
back as ``PostResult(ok=False, status="failed", message=<Vietnamese>)``; a connection failure
that outlived the publisher's own retries is RAISED (``app.net.looks_like_network_error``
recognises it), so the post worker can wait for the network (``app.net.run_resilient``) and
try the post again later.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

from app.config import settings
from app.fsutil import atomic_write_text

TIMEOUT = httpx.Timeout(60.0, connect=20.0)
_sleep = time.sleep            # tests patch this to a no-op (retry back-off)


class _NoQueryInLogs(logging.Filter):
    """httpx logs every request URL at INFO; some documented calls carry a secret in the
    query (Facebook's token exchange). Should anyone turn INFO logging on, the query is cut."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(str(a).split("?", 1)[0] if isinstance(a, httpx.URL) else a
                                for a in record.args)
        return True


logging.getLogger("httpx").addFilter(_NoQueryInLogs())


class PublishError(Exception):
    """A refusal worth showing the owner as is (Vietnamese)."""


class AuthExpired(PublishError):
    """The stored token no longer works — the owner must press "Kết nối" again."""


# --- linked accounts ----------------------------------------------------------------------

def accounts_dir() -> Path:
    return Path(settings.vizsup_storage_dir) / "_accounts"


def account_path(platform: str) -> Path:
    return accounts_dir() / f"{platform}.json"


def load_account(platform: str) -> dict | None:
    """The stored account, or None (missing or unreadable)."""
    p = account_path(platform)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_account(platform: str, data: dict) -> None:
    d = accounts_dir()
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(account_path(platform), json.dumps(data, ensure_ascii=False, indent=2))


def forget_account(platform: str) -> None:
    account_path(platform).unlink(missing_ok=True)


def mark_revoked(platform: str, why: str = "") -> None:
    """The platform refused the stored token: keep the file (labels, playlist cache) but
    make ``connected()`` False until the owner connects again."""
    acc = load_account(platform)
    if acc is not None:
        acc["revoked"] = True
        acc["revoked_why"] = why[:200]
        save_account(platform, acc)


# --- PKCE + loopback redirect ------------------------------------------------------------

def pkce_pair(*, hex_challenge: bool = False) -> tuple[str, str]:
    """(code_verifier, code_challenge). RFC 7636 S256 = base64url(sha256) without padding;
    TikTok's desktop Login Kit wants the SHA-256 HEX digest instead (``hex_challenge``)."""
    verifier = secrets.token_urlsafe(64)[:86]       # 43–128 chars of [A-Za-z0-9-_]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    if hex_challenge:
        return verifier, digest.hex()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


_DONE_PAGE = """<!doctype html><html lang="vi"><meta charset="utf-8"><title>vizsup</title>
<body style="font-family:system-ui,sans-serif;background:#16161d;color:#e8e8f0;display:flex;
align-items:center;justify-content:center;height:90vh"><div style="text-align:center">
<h2>{title}</h2><p>{text}</p></div></body></html>"""


class LoopbackReceiver:
    """One-shot HTTP server on ``127.0.0.1:<random free port>`` that catches the OAuth
    redirect's query string. Requests to other paths (favicon …) get a 404 and are ignored."""

    def __init__(self, path: str = "/", host: str = "127.0.0.1") -> None:
        self.path = path
        self.params: dict[str, str] | None = None
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                parts = urlsplit(self.path)
                params = dict(parse_qsl(parts.query, keep_blank_values=True))
                if (parts.path.rstrip("/") != receiver.path.rstrip("/")
                        or not ("code" in params or "error" in params)):
                    self.send_response(404)       # favicon, a reload … — not the redirect
                    self.end_headers()
                    return
                failed = "error" in params
                body = _DONE_PAGE.format(
                    title="Chưa kết nối được" if failed else "Đã nhận đăng nhập",
                    text=html.escape(params.get("error_description") or params.get("error", ""))
                    if failed else "Bạn có thể đóng tab này và quay lại vizsup.").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                receiver.params = params

            def log_message(self, *_a) -> None:   # the query carries the auth code: never log it
                return

        self._server = HTTPServer((host, 0), _Handler)
        self._server.timeout = 0.25
        self.redirect_uri = f"http://{host}:{self._server.server_address[1]}{path}"

    def wait(self, timeout: float, cancel: Callable[[], bool] | None = None) -> dict[str, str]:
        end = time.monotonic() + timeout
        while self.params is None:
            if cancel and cancel():
                raise PublishError("Đã hủy kết nối.")
            if time.monotonic() > end:
                raise PublishError("Hết thời gian chờ đăng nhập trong trình duyệt — bấm Kết nối "
                                   "lại.")
            self._server.handle_request()
        return self.params

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> LoopbackReceiver:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def run_loopback(auth_url_for: Callable[[str, str], str], *, path: str = "/",
                 open_browser: Callable[[str], object] = webbrowser.open,
                 timeout: float = 300.0,
                 cancel: Callable[[], bool] | None = None) -> tuple[str, str]:
    """Open the consent page and wait for the redirect. ``auth_url_for(redirect_uri,
    state)`` builds the authorization URL. Returns (code, redirect_uri)."""
    state = secrets.token_urlsafe(24)
    with LoopbackReceiver(path) as rx:
        url = auth_url_for(rx.redirect_uri, state)
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
        params = rx.wait(timeout, cancel)
        redirect_uri = rx.redirect_uri
    if params.get("error"):
        why = params.get("error_description") or params["error"]
        if params["error"] in ("access_denied", "user_denied"):
            raise PublishError("Bạn đã từ chối cấp quyền trong trình duyệt.")
        raise PublishError(f"Nền tảng từ chối đăng nhập: {why}")
    if params.get("state") != state:
        raise PublishError("Phản hồi đăng nhập không khớp (state) — bấm Kết nối lại.")
    code = params.get("code", "")
    if not code:
        raise PublishError("Trình duyệt không trả về mã đăng nhập — bấm Kết nối lại.")
    return code, redirect_uri


# --- small helpers -----------------------------------------------------------------------

def client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(transport=transport, timeout=TIMEOUT, follow_redirects=False)


def json_or_empty(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def backoff(attempt: int, cap: float = 60.0) -> None:
    _sleep(min(cap, 2.0 ** attempt))


def now() -> float:
    return time.time()


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def cut_utf16(text: str, limit: int) -> str:
    while utf16_len(text) > limit:
        text = text[:-1]
    return text


def caption(meta, *, extra_tags: tuple[str, ...] = ()) -> str:
    """Title, description and the hashtags as one caption (TikTok / Reels)."""
    tags = [t if t.startswith("#") else f"#{t}" for t in (*meta.tags, *extra_tags) if t.strip()]
    tags = [t.replace(" ", "") for t in tags]
    desc = meta.description.strip()
    have = desc.lower()
    tags = [t for t in dict.fromkeys(tags) if t.lower() not in have]
    parts = [meta.title.strip()]
    if desc and desc != meta.title.strip():
        parts.append(desc)
    if tags:
        parts.append(" ".join(tags))
    return "\n\n".join(p for p in parts if p)


def file_chunks(path: Path, start: int, length: int, piece: int = 1 << 20):
    """Stream ``length`` bytes of ``path`` from ``start`` (an httpx request body)."""
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            data = f.read(min(piece, left))
            if not data:
                return
            left -= len(data)
            yield data


def read_range(path: Path, start: int, length: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(length)


def probe_seconds(path: Path) -> float:
    """Video length, 0.0 when ffprobe can't tell (the check is then skipped)."""
    try:
        from app.pipeline.assemble import probe_duration

        return float(probe_duration(Path(path)) or 0.0)
    except Exception:  # noqa: BLE001 - no ffprobe / unreadable file: no length check
        return 0.0
