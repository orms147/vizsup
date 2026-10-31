"""Facebook publisher: the desktop "paste the Success address" login, Page choice, Reels
(start → rupload → finish, resume, 30/24 h), Page videos (chunked start/transfer/finish),
scheduling and error messages. A fake Graph API (httpx.MockTransport) answers everything —
no network, no real upload."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import settings
from app.providers.base import PostMeta
from app.providers.publish import _common as cm
from app.providers.publish import facebook as fb

SUCCESS = ("https://www.facebook.com/connect/login_success.html#access_token=EAAshort123456789"
           "0123456789abcdef&data_access_expiration_time=1800000000&expires_in=5000&state=vizsup")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    monkeypatch.setattr(settings, "facebook_app_id", "1234")
    monkeypatch.setattr(settings, "facebook_app_secret", "appsecret")
    monkeypatch.setattr(cm, "_sleep", lambda _s: None)
    monkeypatch.setattr(cm, "probe_seconds", lambda _p: 0.0)
    return tmp_path


def _multipart(req: httpx.Request) -> tuple[dict, bytes]:
    boundary = re.search(r"boundary=([^;]+)", req.headers["content-type"]).group(1).encode()
    fields, blob = {}, b""
    for part in req.content.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body[:-2] if body.endswith(b"\r\n") else body
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        if b"filename=" in head:
            blob = body
        else:
            fields[name] = body.decode()
    return fields, blob


def _form(req: httpx.Request) -> dict:
    if req.headers.get("content-type", "").startswith("multipart/"):
        return _multipart(req)[0]
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


class FakeMeta:
    def __init__(self, *, rupload_fail_once=False, finish_error=None, start_error=None,
                 chunk=300_000):
        self.rupload_fail_once = rupload_fail_once
        self.finish_error = finish_error
        self.start_error = start_error
        self.chunk = chunk
        self.calls: list[str] = []
        self.reel_bytes = bytearray()
        self.video_bytes = bytearray()
        self.size = 0
        self.finish: dict | None = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        self.calls.append(f"{req.method} {u.host}{u.path}")
        if u.path.endswith("/oauth/access_token"):
            assert u.params["grant_type"] == "fb_exchange_token"
            assert u.params["client_secret"] == "appsecret"
            return httpx.Response(200, json={"access_token": "LONGUSER", "expires_in": 5183944})
        auth = req.headers.get("authorization", "")
        if u.path.endswith("/me/accounts"):
            assert auth == "OAuth LONGUSER"
            if "after" not in u.params:
                return httpx.Response(200, json={
                    "data": [{"id": "P1", "name": "Trang Phim", "access_token": "PAGETOK1",
                              "tasks": ["ANALYZE", "ADVERTISE", "CREATE_CONTENT", "MANAGE"]}],
                    "paging": {"cursors": {"after": "C1"}, "next": "https://graph…"}})
            return httpx.Response(200, json={"data": [
                {"id": "P2", "name": "Trang Chỉ Xem", "access_token": "PAGETOK2", "tasks": ["ANALYZE"]}],
                "paging": {"cursors": {"after": "C2"}}})
        assert auth == "OAuth PAGETOK1", "posting must use the Page token"
        if u.path == "/v26.0/P1/video_reels":
            form = _form(req)
            if form["upload_phase"] == "start":
                if self.start_error:
                    return httpx.Response(400, json={"error": self.start_error})
                return httpx.Response(200, json={
                    "video_id": "R1", "upload_url": "https://rupload.facebook.com/video-upload/v26.0/R1"})
            self.finish = form
            if self.finish_error:
                return httpx.Response(400, json={"error": self.finish_error})
            return httpx.Response(200, json={"success": True})
        if u.host == "rupload.facebook.com":
            offset, size = int(req.headers["offset"]), int(req.headers["file_size"])
            assert offset == len(self.reel_bytes) and int(req.headers["content-length"]) == size - offset
            data = req.read()
            if self.rupload_fail_once:
                self.rupload_fail_once = False
                self.reel_bytes += data[: len(data) // 2]      # half arrived, then it broke
                return httpx.Response(503, json={"error": {"message": "busy"}})
            self.reel_bytes += data
            return httpx.Response(200, json={"success": True})
        if u.path == "/v26.0/R1":
            return httpx.Response(200, json={"status": {"uploading_phase": {
                "status": "in_progress", "bytes_transfered": len(self.reel_bytes)}}})
        if u.host == "graph-video.facebook.com" and u.path == "/v26.0/P1/videos":
            if req.headers["content-type"].startswith("multipart/"):
                fields, blob = _multipart(req)
            else:
                fields, blob = _form(req), b""
            phase = fields["upload_phase"]
            if phase == "start":
                self.size = int(fields["file_size"])
                return httpx.Response(200, json={"upload_session_id": "S1", "video_id": "V1",
                                                 "start_offset": "0",
                                                 "end_offset": str(min(self.chunk, self.size))})
            if phase == "transfer":
                start = int(fields["start_offset"])
                assert start == len(self.video_bytes) and fields["upload_session_id"] == "S1"
                self.video_bytes += blob
                nxt = len(self.video_bytes)
                return httpx.Response(200, json={"start_offset": str(nxt),
                                                 "end_offset": str(min(nxt + self.chunk, self.size))})
            self.finish = fields
            return httpx.Response(200, json={"success": True})
        raise AssertionError(f"unexpected {req.method} {u}")


def _pub(fake) -> fb.FacebookPublisher:
    p = fb.FacebookPublisher()
    p.transport = httpx.MockTransport(fake)
    return p


def _connected(**extra) -> None:
    cm.save_account("facebook", {"page_id": "P1", "page_name": "Trang Phim",
                                 "page_token": "PAGETOK1", **extra})


def _video(tmp, size=700_000):
    p = tmp / "ep.mp4"
    p.write_bytes(bytes(i % 241 for i in range(size)))
    return p


# --- login ---------------------------------------------------------------------------------

def test_pasted_success_address_or_bare_token_gives_the_user_token():
    assert fb.parse_pasted(SUCCESS) == "EAAshort1234567890123456789abcdef"
    assert fb.parse_pasted("  EAAbare0123456789012345678901234  ") == "EAAbare0123456789012345678901234"
    with pytest.raises(cm.PublishError, match="Permissions error"):
        fb.parse_pasted("https://www.facebook.com/connect/login_success.html?error=access_denied"
                        "&error_reason=user_denied&error_description=Permissions+error")
    with pytest.raises(cm.PublishError):
        fb.parse_pasted("hello")
    with pytest.raises(cm.PublishError, match="access_token"):
        fb.parse_pasted("https://www.facebook.com/connect/login_success.html#state=x")


def test_login_url_is_the_documented_desktop_flow(env):
    q = {k: v[0] for k, v in parse_qs(urlsplit(fb.FacebookPublisher().login_url()).query).items()}
    assert q["redirect_uri"] == "https://www.facebook.com/connect/login_success.html"
    assert q["response_type"] == "token" and q["client_id"] == "1234"
    assert set(q["scope"].split(",")) == {"pages_show_list", "pages_read_engagement",
                                          "pages_manage_posts"}


def test_fetch_pages_then_choose_one_stores_only_the_page_token(env):
    p = _pub(FakeMeta())
    pages = p.fetch_pages(SUCCESS)
    assert [(x["id"], x["can_post"]) for x in pages] == [("P1", True), ("P2", False)]
    assert not p.connected()
    assert p.choose_page("P1") == "Trang Phim"
    assert p.connected() and p.account_label() == "Trang Phim"
    raw = (env / "_accounts" / "facebook.json").read_text(encoding="utf-8")
    assert "PAGETOK1" in raw and "LONGUSER" not in raw and "EAAshort" not in raw
    with pytest.raises(cm.PublishError):
        p.choose_page("P2")                      # the pending list is cleared after a pick
    p.disconnect()
    assert not p.connected()


def test_available_needs_id_and_secret(env, monkeypatch):
    assert fb.FacebookPublisher().available()
    monkeypatch.setattr(settings, "facebook_app_secret", " ")
    assert not fb.FacebookPublisher().available()


# --- Reels ---------------------------------------------------------------------------------

def test_reel_resumes_from_bytes_transfered_and_publishes_with_the_ai_label(env):
    _connected()
    video = _video(env)
    fake = FakeMeta(rupload_fail_once=True)
    seen: list[float] = []
    res = _pub(fake).publish(video, PostMeta(title="Tập 3", description="Hay lắm",
                                             tags=["#phim", "review"], kind="short"),
                             progress=seen.append)
    assert res.ok and res.status == "published", res.message
    assert bytes(fake.reel_bytes) == video.read_bytes()
    assert res.url == "https://www.facebook.com/reel/R1" and res.remote_id == "R1"
    f = fake.finish
    assert f["upload_phase"] == "finish" and f["video_state"] == "PUBLISHED"
    assert f["is_ai_generated"] == "true"
    assert f["description"].startswith("Tập 3") and "#phim #review" in f["description"]
    assert seen[-1] == 1.0
    assert len(cm.load_account("facebook")["reels"]) == 1


def test_scheduled_reel_sends_the_unix_time(env):
    _connected()
    when = cm.now() + 3 * 86400
    fake = FakeMeta()
    res = _pub(fake).publish(_video(env, 5000), PostMeta(title="T", kind="short", publish_at=when))
    assert res.status == "scheduled"
    assert fake.finish["video_state"] == "SCHEDULED"
    assert fake.finish["scheduled_publish_time"] == str(int(when))


def test_a_reel_too_far_ahead_or_too_long_is_refused_before_uploading(env, monkeypatch):
    _connected()
    fake = FakeMeta()
    res = _pub(fake).publish(_video(env, 5000),
                             PostMeta(title="T", kind="short", publish_at=cm.now() + 40 * 86400))
    assert not res.ok and "29 ngày" in res.message
    monkeypatch.setattr(cm, "probe_seconds", lambda _p: 120.0)
    res = _pub(fake).publish(_video(env, 5000), PostMeta(title="T", kind="short"))
    assert not res.ok and "3–90 giây" in res.message
    assert fake.calls == []


def test_thirty_reels_a_day_are_enforced_locally(env):
    now = cm.now()
    _connected(reels=[now - 60 * i for i in range(30)])
    fake = FakeMeta()
    res = _pub(fake).publish(_video(env, 5000), PostMeta(title="T", kind="short"))
    assert not res.ok and "30 Reels" in res.message and fake.calls == []
    _connected(reels=[now - 90000] * 30)           # yesterday's do not count
    assert _pub(FakeMeta()).publish(_video(env, 5000), PostMeta(title="T", kind="short")).ok


def test_meta_rate_limit_is_explained(env):
    _connected()
    res = _pub(FakeMeta(start_error={"code": 4, "message": "Application request limit reached"})) \
        .publish(_video(env, 5000), PostMeta(title="T", kind="short"))
    assert not res.ok and "30 Reels" in res.message


def test_an_expired_page_token_asks_to_reconnect(env):
    _connected()
    p = _pub(FakeMeta(finish_error={"code": 190, "message": "Error validating access token"}))
    res = p.publish(_video(env, 5000), PostMeta(title="T", kind="short"))
    assert not res.ok and "Kết nối lại" in res.message
    assert not p.connected()


# --- Page video ----------------------------------------------------------------------------

def test_long_video_uses_the_chunked_page_upload_at_the_servers_offsets(env):
    _connected()
    video = _video(env)
    fake = FakeMeta(chunk=250_000)
    res = _pub(fake).publish(video, PostMeta(title="Tập 4 — full", description="Mô tả",
                                             tags=["phim"]))
    assert res.ok and res.status == "published", res.message
    assert bytes(fake.video_bytes) == video.read_bytes()
    assert fake.finish["title"] == "Tập 4 — full" and fake.finish["description"] == "Mô tả\n\n#phim"
    assert "published" not in fake.finish
    assert res.url == "https://www.facebook.com/P1/videos/V1"


def test_scheduled_page_video_is_unpublished_with_a_time(env):
    _connected()
    when = cm.now() + 86400
    fake = FakeMeta()
    res = _pub(fake).publish(_video(env, 5000), PostMeta(title="T", publish_at=when))
    assert res.status == "scheduled"
    assert fake.finish["published"] == "false"
    assert fake.finish["scheduled_publish_time"] == str(int(when))


def test_a_slot_less_than_ten_minutes_away_posts_now(env):
    _connected()
    fake = FakeMeta()
    res = _pub(fake).publish(_video(env, 5000), PostMeta(title="T", publish_at=cm.now() + 120))
    assert res.status == "published" and "scheduled_publish_time" not in fake.finish


def test_not_connected(env):
    res = _pub(FakeMeta()).publish(_video(env, 10), PostMeta(title="T"))
    assert not res.ok and "Chưa kết nối" in res.message


def test_registered():
    from app.providers.registry import get_publisher

    assert isinstance(get_publisher("facebook"), fb.FacebookPublisher)
    assert json.dumps(get_publisher("facebook").info())
