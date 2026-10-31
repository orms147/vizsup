"""A fake Telegram Bot API for the remote-control tests: an httpx.MockTransport that answers
getMe / getUpdates / sendMessage / answerCallbackQuery / editMessageReplyMarkup /
deleteWebhook from memory. Nothing ever reaches api.telegram.org."""
from __future__ import annotations

import json

import httpx

TOKEN = "123456789:AAH-fake_token_for_tests_only_0123456"
OWNER = 777
STRANGER = 888


class FakeTelegram:
    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.updates: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.sent: list[dict] = []
        self.acks: list[dict] = []
        self.edits: list[dict] = []
        self.mid = 100
        self.uid = 0

    # what the owner (or a stranger) does on the phone
    def say(self, text: str, chat: int = OWNER, first: str = "Bách") -> dict:
        self.uid += 1
        u = {"update_id": self.uid, "message": {
            "message_id": self.uid, "text": text, "chat": {"id": chat, "type": "private"},
            "from": {"id": chat, "is_bot": False, "first_name": first}}}
        self.updates.append(u)
        return u

    def press(self, data: str, chat: int = OWNER, message_id: int = 101) -> dict:
        self.uid += 1
        u = {"update_id": self.uid, "callback_query": {
            "id": f"cb{self.uid}", "data": data, "from": {"id": chat, "is_bot": False},
            "message": {"message_id": message_id, "chat": {"id": chat, "type": "private"}}}}
        self.updates.append(u)
        return u

    def buttons(self, payload: dict) -> list[str]:
        rows = (payload.get("reply_markup") or {}).get("inline_keyboard") or []
        return [b["callback_data"] for row in rows for b in row]

    def texts(self) -> list[str]:
        return [p["text"] for p in self.sent]

    # the API
    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.telegram.org" and request.url.scheme == "https"
        tok, method = request.url.path[len("/bot"):].split("/", 1)
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if tok != self.token:
            return httpx.Response(401, json={"ok": False, "error_code": 401,
                                             "description": "Unauthorized"})
        if method == "getMe":
            return self._ok({"id": 42, "is_bot": True, "username": "vizsup_test_bot"})
        if method == "deleteWebhook":
            return self._ok(True)
        if method == "getUpdates":
            off = int(payload.get("offset") or 0)
            return self._ok([u for u in self.updates if u["update_id"] >= off])
        if method == "sendMessage":
            assert len(payload["text"]) <= 4096
            self.sent.append(payload)
            self.mid += 1
            return self._ok({"message_id": self.mid, "chat": {"id": payload["chat_id"]}})
        if method == "answerCallbackQuery":
            self.acks.append(payload)
            return self._ok(True)
        if method == "editMessageReplyMarkup":
            self.edits.append(payload)
            return self._ok(True)
        return httpx.Response(404, json={"ok": False, "error_code": 404,
                                         "description": "Not Found"})

    @staticmethod
    def _ok(result) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": result})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def pair(fake: FakeTelegram, chat: int = OWNER) -> None:
    """Connect the fake bot and pair ``chat`` the way the owner does (Kết nối → /start <mã>)."""
    from app.remote import telegram

    code = telegram.connect(TOKEN)["code"]
    ch = telegram.TelegramChannel()
    ev = ch.accept(fake.say(f"/start {code}", chat=chat))
    assert ev is not None and ev.kind == "paired"
    ch.commit(fake.uid)
