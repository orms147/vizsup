"""Telegram Bot API channel for "Điều khiển qua Telegram" (app/pipeline/remote.py).

The owner makes their OWN bot with @BotFather and pastes its token in Cài đặt → Đăng video;
vizsup never creates accounts or types credentials anywhere. Pairing: "Kết nối" checks the
token (getMe) and shows a one-time 6-digit code; the owner sends ``/start <code>`` to the bot
and that private chat is the only one ever answered. Every update from any other chat is
dropped without a reply (nothing leaks); five wrong codes or 30 minutes end the code.

- **Secrets**: token + chat id live ONLY in ``<storage>/_accounts/telegram.json`` (gitignored
  with storage/), next to the getUpdates offset and the notification toggles. Never printed
  or logged: ``mask()`` for the UI, a logging filter cuts ``bot<token>`` out of httpx's
  request lines, error texts are the API's ``description`` (scrubbed) — never a URL.
- **HTTP**: httpx to https://api.telegram.org only, JSON POST with timeouts; ``transport`` is
  injectable (tests use ``httpx.MockTransport`` — no test reaches Telegram). Long polling
  ``getUpdates`` (no webhook: the PC is behind NAT). A connection failure is raised as the
  httpx error, so ``app.net.looks_like_network_error`` recognises it.
- **Messages** are plain text (no parse_mode — nothing to escape), split under the 4096-
  character limit on line breaks; inline buttons go on the last piece.
"""
from __future__ import annotations

import hmac
import logging
import re
import secrets
import time

import httpx

from app.remote import Button, Event

API = "https://api.telegram.org"
PLATFORM = "telegram"
MAX_CHARS = 3900            # Telegram allows 4096 (UTF-16 units) per message — keep a margin
PAIR_TTL_S = 30 * 60
PAIR_TRIES = 5
NOTIFY_KINDS = ("gate", "planned", "posted", "error")
NOTIFY_LABELS = {"gate": "Tập chờ duyệt", "planned": "Đã lên lịch đăng", "posted": "Đã đăng",
                 "error": "Lỗi"}
_TOKEN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")
_TOKEN_IN_TEXT = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{30,64}")
_START = re.compile(r"^/start(?:@\w+)?\s+(\d{6})$|^(\d{6})$")


class TelegramError(Exception):
    """A refusal by the Bot API (Vietnamese-ready text, never the token)."""

    def __init__(self, message: str, *, code: int = 0, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def scrub(text) -> str:
    """``text`` with anything shaped like a bot token replaced. Pure."""
    return _TOKEN_IN_TEXT.sub("…", str(text))


class _NoTokenInLogs(logging.Filter):
    """httpx logs every request line at INFO ("POST https://api.telegram.org/bot<token>/…");
    should anyone turn INFO logging on, the token is cut out."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(scrub(a) if isinstance(a, (str, httpx.URL)) else a
                                for a in record.args)
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_NoTokenInLogs())


def mask(secret) -> str:
    """"…ab12" — the most a token or chat id is ever shown. Pure."""
    s = str(secret or "")
    return "…" + s[-4:] if len(s) >= 6 else "…"


def valid_token(token: str) -> bool:
    return bool(_TOKEN.match((token or "").strip()))


# --- the account file (storage/_accounts/telegram.json) ----------------------------------

def load() -> dict:
    from app.providers.publish._common import load_account

    return load_account(PLATFORM) or {}


def save(acc: dict) -> None:
    from app.providers.publish._common import save_account

    save_account(PLATFORM, acc)


def forget() -> None:
    from app.providers.publish._common import forget_account

    forget_account(PLATFORM)


def configured() -> bool:
    return bool(load().get("token"))


def paired() -> bool:
    acc = load()
    return bool(acc.get("token")) and acc.get("chat_id") is not None


def notify_on(kind: str) -> bool:
    return bool((load().get("notify") or {}).get(kind, True))


def set_notify(kind: str, on: bool) -> None:
    acc = load()
    if not acc:
        return
    acc["notify"] = {**(acc.get("notify") or {}), kind: bool(on)}
    save(acc)


def pairing(now: float | None = None) -> dict | None:
    """The pairing code still waiting for the owner's ``/start <code>`` ({code, expires}),
    or None."""
    p = load().get("pair") or {}
    now = time.time() if now is None else now
    return p if p.get("code") and float(p.get("expires") or 0) > now else None


def new_pairing(now: float | None = None) -> str:
    """A fresh one-time code (the previous chat, if any, stays paired until it succeeds)."""
    acc = load()
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = time.time() if now is None else now
    acc["pair"] = {"code": code, "expires": now + PAIR_TTL_S, "tries": 0}
    save(acc)
    return code


def status() -> dict:
    """What Settings shows — never the token or the chat id themselves."""
    acc = load()
    p = pairing()
    return {"configured": bool(acc.get("token")), "paired": acc.get("chat_id") is not None,
            "bot": acc.get("bot") or "", "who": acc.get("chat_name") or "",
            "token_hint": mask(acc.get("token")) if acc.get("token") else "",
            "chat_hint": mask(acc.get("chat_id")) if acc.get("chat_id") is not None else "",
            "code": p.get("code") if p else "", "expires": p.get("expires") if p else 0,
            "notify": {k: bool((acc.get("notify") or {}).get(k, True)) for k in NOTIFY_KINDS}}


def deep_link(bot: str, code: str) -> str:
    """t.me link that opens the bot with ``/start <code>`` typed. Pure."""
    return f"https://t.me/{bot}?start={code}" if bot else ""


# --- Bot API ---------------------------------------------------------------------------------

class BotAPI:
    """Minimal JSON client. ``call`` returns the ``result``; raises TelegramError on ok=false,
    the httpx error on a connection failure."""

    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._token = token
        self.transport = transport

    def call(self, method: str, payload: dict | None = None, *, timeout: float = 30.0):
        url = f"{API}/bot{self._token}/{method}"
        with httpx.Client(transport=self.transport, follow_redirects=False,
                          timeout=httpx.Timeout(timeout, connect=15.0)) as c:
            r = c.post(url, json=payload or {})
        try:
            data = r.json()
        except ValueError:
            data = {}
        if isinstance(data, dict) and data.get("ok"):
            return data.get("result")
        data = data if isinstance(data, dict) else {}
        params = data.get("parameters") or {}
        code = int(data.get("error_code") or r.status_code or 0)
        raise TelegramError(scrub(data.get("description") or f"HTTP {r.status_code}"),
                            code=code, retry_after=float(params.get("retry_after") or 0))


def connect(token: str, *, transport: httpx.BaseTransport | None = None) -> dict:
    """Check ``token`` with getMe and keep it; a new token forgets the previous pairing.
    Returns {"bot": username, "code": the pairing code}. Raises TelegramError (bad token)
    or the httpx error (no connection)."""
    token = (token or "").strip()
    if not valid_token(token):
        raise TelegramError("Token không đúng dạng — chép lại cả dòng @BotFather gửi "
                            "(dạng 123456789:AA…).")
    api = BotAPI(token, transport if transport is not None else TelegramChannel.transport)
    try:
        me = api.call("getMe", timeout=20.0) or {}
    except TelegramError as exc:
        if exc.code in (401, 404):
            raise TelegramError("Telegram không nhận token này (đã thu hồi hoặc chép thiếu)."
                                ) from None
        raise
    try:                                   # a webhook would block getUpdates (409)
        api.call("deleteWebhook", {"drop_pending_updates": False}, timeout=20.0)
    except TelegramError:
        pass
    acc = load()
    if acc.get("token") != token:
        acc = {"notify": acc.get("notify") or {}}
    acc.update(token=token, bot=me.get("username") or "", bot_id=me.get("id"))
    save(acc)
    return {"bot": acc["bot"], "code": new_pairing()}


class TelegramChannel:
    """The paired chat on the owner's bot (app/remote/__init__.py lists the calls)."""

    name = PLATFORM
    display_name = "Telegram"
    transport: httpx.BaseTransport | None = None     # tests: an httpx.MockTransport

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        if transport is not None:
            self.transport = transport

    # state
    def configured(self) -> bool:
        return configured()

    def paired(self) -> bool:
        return paired()

    def notify_on(self, kind: str) -> bool:
        return notify_on(kind)

    def _api(self) -> BotAPI:
        tok = load().get("token")
        if not tok:
            raise TelegramError("Chưa có token bot Telegram (Cài đặt → Đăng video).")
        return BotAPI(tok, self.transport)

    # receiving
    def poll(self, timeout: float = 25.0) -> list[dict]:
        """Pending updates after the saved offset (long poll up to ``timeout`` s). Changes
        nothing: ``commit`` each one when it is handled."""
        res = self._api().call("getUpdates", {
            "offset": int(load().get("offset") or 0), "timeout": max(0, int(timeout)),
            "allowed_updates": ["message", "callback_query"]}, timeout=timeout + 20.0)
        return [u for u in (res or []) if isinstance(u, dict) and "update_id" in u]

    @staticmethod
    def update_id(raw: dict) -> int:
        return int(raw.get("update_id") or 0)

    def commit(self, update_id: int) -> None:
        """Never deliver this update (or an older one) again."""
        acc = load()
        if not acc:
            return
        if int(update_id) + 1 > int(acc.get("offset") or 0):
            acc["offset"] = int(update_id) + 1
            save(acc)

    def accept(self, raw: dict, *, now: float | None = None) -> Event | None:
        """The update as an Event when it comes from the paired private chat; the pairing
        message while a code is waiting; None for everything else (no reply, ever)."""
        acc = load()
        chat_id = acc.get("chat_id")
        msg, cb = raw.get("message"), raw.get("callback_query")
        if isinstance(msg, dict):
            chat, sender = msg.get("chat") or {}, msg.get("from") or {}
            if chat.get("type") != "private" or sender.get("is_bot"):
                return None
            text = str(msg.get("text") or "").strip()
            if pairing(now) and _START.match(text):          # a code is waiting: this is a try
                return self._pair(acc, chat, sender, text, now)
            if chat.get("id") is not None and chat.get("id") == chat_id:
                return Event("text", text=text, message_id=msg.get("message_id"),
                             uid=self.update_id(raw))
            return None
        if isinstance(cb, dict) and chat_id is not None:
            m = cb.get("message") or {}
            if (m.get("chat") or {}).get("id") != chat_id or (cb.get("from") or {}).get("id") != chat_id:
                return None
            return Event("button", data=str(cb.get("data") or ""), ref=str(cb.get("id") or ""),
                         message_id=m.get("message_id"), uid=self.update_id(raw))
        return None

    def _pair(self, acc: dict, chat: dict, sender: dict, text: str, now) -> Event | None:
        now = time.time() if now is None else now
        p = acc.get("pair") or {}
        if not p.get("code") or float(p.get("expires") or 0) <= now or chat.get("id") is None:
            return None
        m = _START.match(text)
        if not m:
            return None                     # chatter from a stranger is not a guess either
        got = m.group(1) or m.group(2)
        if hmac.compare_digest(got, str(p["code"])):
            name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
            acc.pop("pair", None)
            acc.update(chat_id=chat["id"], chat_name=(name or sender.get("username") or "")[:60],
                       paired_at=now)
            save(acc)
            return Event("paired", who=acc["chat_name"])
        p["tries"] = int(p.get("tries") or 0) + 1
        if p["tries"] >= PAIR_TRIES:
            acc.pop("pair", None)           # five wrong codes: the owner makes a new one
        else:
            acc["pair"] = p
        save(acc)
        return None

    # sending (to the paired chat only)
    def _chat(self):
        chat_id = load().get("chat_id")
        if chat_id is None:
            raise TelegramError("Bot chưa ghép với tài khoản Telegram của bạn.")
        return chat_id

    def send(self, text: str, buttons: list[list[Button]] | None = None, *,
             preview: bool = False) -> int | None:
        api, chat_id = self._api(), self._chat()
        parts = split_text(text)
        last = None
        for i, part in enumerate(parts):
            payload: dict = {"chat_id": chat_id, "text": part,
                             "link_preview_options": {"is_disabled": not preview}}
            if buttons and i == len(parts) - 1:
                payload["reply_markup"] = keyboard(buttons)
            res = api.call("sendMessage", payload) or {}
            last = res.get("message_id")
        return last

    def ack(self, event: Event, text: str = "") -> None:
        if event.ref:
            self._api().call("answerCallbackQuery",
                             {"callback_query_id": event.ref, "text": text[:190]})

    def drop_buttons(self, event: Event) -> None:
        if event.message_id is None:
            return
        try:
            self._api().call("editMessageReplyMarkup", {
                "chat_id": self._chat(), "message_id": event.message_id,
                "reply_markup": {"inline_keyboard": []}})
        except TelegramError:
            pass                            # too old to edit / already without buttons


def keyboard(rows: list[list[Button]]) -> dict:
    """Inline keyboard markup. Pure."""
    return {"inline_keyboard": [[{"text": b.text, "callback_data": b.data[:64]} for b in row]
                                for row in rows]}


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """``text`` in pieces of at most ``limit`` characters, cut at line breaks (a paragraph
    stays whole when it fits), a longer line cut hard. Never an empty list. Pure."""
    text = (text or "").strip() or "…"
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        cand = f"{cur}\n{line}" if cur else line
        if len(cand) > limit:
            out.append(cur)
            cur = line
        else:
            cur = cand
    if cur.strip():
        out.append(cur)
    return [p.strip("\n") for p in out if p.strip()] or ["…"]
