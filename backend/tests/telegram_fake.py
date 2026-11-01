"""A fake Telegram Bot API for the remote-control tests: an httpx.MockTransport that answers
getMe / getUpdates / sendMessage / sendPhoto / getFile (+ the file download) /
setMyCommands / answerCallbackQuery / editMessageReplyMarkup / deleteWebhook from memory.
Nothing ever reaches api.telegram.org."""
from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import HTTP

import httpx

TOKEN = "123456789:AAH-fake_token_for_tests_only_0123456"
OWNER = 777
STRANGER = 888


def _multipart(request: httpx.Request) -> dict:
    """The form fields of a multipart request ({name: text, or (filename, bytes)})."""
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    msg = BytesParser(policy=HTTP).parsebytes(head + request.content)
    out: dict = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        fname = part.get_filename()
        out[name] = (fname, data) if fname else data.decode("utf-8")
    return out


class FakeTelegram:
    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.updates: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.sent: list[dict] = []
        self.photos: list[dict] = []          # sendPhoto: chat_id, caption, reply_markup, photo
        self.acks: list[dict] = []
        self.edits: list[dict] = []
        self.commands: list[list[dict]] = []
        self.files: dict[str, bytes] = {}     # file_id → the bytes the owner sent
        self.downloads: list[str] = []
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

    def photo(self, data: bytes, caption: str = "", chat: int = OWNER, *,
              as_file: bool = False) -> dict:
        """The owner sends a picture (three sizes, like Telegram — or "as a file")."""
        self.uid += 1
        fid = f"F{self.uid}"
        self.files[fid] = data
        msg = {"message_id": self.uid, "chat": {"id": chat, "type": "private"},
               "from": {"id": chat, "is_bot": False, "first_name": "Bách"}}
        if as_file:
            msg["document"] = {"file_id": fid, "file_name": "bia.png", "mime_type": "image/png",
                               "file_size": len(data)}
        else:
            msg["photo"] = [{"file_id": f"small{self.uid}", "width": 90, "height": 51,
                             "file_size": 900},
                            {"file_id": fid, "width": 1280, "height": 720,
                             "file_size": len(data)},
                            {"file_id": f"mid{self.uid}", "width": 320, "height": 180,
                             "file_size": 9000}]
        if caption:
            msg["caption"] = caption
        u = {"update_id": self.uid, "message": msg}
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
        markup = payload.get("reply_markup") or {}
        if isinstance(markup, str):
            markup = json.loads(markup)
        rows = markup.get("inline_keyboard") or []
        return [b["callback_data"] for row in rows for b in row]

    def labels(self, payload: dict) -> list[str]:
        markup = payload.get("reply_markup") or {}
        if isinstance(markup, str):
            markup = json.loads(markup)
        return [b["text"] for row in markup.get("inline_keyboard") or [] for b in row]

    def texts(self) -> list[str]:
        return [p["text"] for p in self.sent]

    # the API
    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.telegram.org" and request.url.scheme == "https"
        if request.url.path.startswith("/file/bot"):
            tok, path = request.url.path[len("/file/bot"):].split("/", 1)
            if tok != self.token:
                return httpx.Response(401)
            self.downloads.append(path)
            fid = path.rsplit("/", 1)[-1].split(".")[0]
            if fid not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[fid])
        tok, method = request.url.path[len("/bot"):].split("/", 1)
        if request.headers.get("content-type", "").startswith("multipart/"):
            payload = _multipart(request)
        else:
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
        if method == "sendPhoto":
            assert len(payload.get("caption") or "") <= 1024
            assert isinstance(payload.get("photo"), tuple) and payload["photo"][1]
            self.photos.append(payload)
            self.mid += 1
            return self._ok({"message_id": self.mid})
        if method == "getFile":
            fid = payload.get("file_id")
            if fid not in self.files:
                return httpx.Response(400, json={"ok": False, "error_code": 400,
                                                 "description": "Bad Request: invalid file_id"})
            return self._ok({"file_id": fid, "file_size": len(self.files[fid]),
                             "file_path": f"photos/{fid}.jpg"})
        if method == "setMyCommands":
            self.commands.append(payload["commands"])
            return self._ok(True)
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
