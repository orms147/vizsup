"""YouTube publisher: OAuth loopback + PKCE, resumable upload with resume, the private-lock
check, playlist / thumbnail, error messages. A fake Google (httpx.MockTransport) answers
every request — no test reaches the network, nothing is ever really uploaded."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import settings
from app.providers.base import PostMeta
from app.providers.publish import _common as cm
from app.providers.publish import youtube as yt


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    monkeypatch.setattr(settings, "youtube_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "youtube_client_secret", "csecret")
    monkeypatch.setattr(cm, "_sleep", lambda _s: None)
    monkeypatch.setattr(yt, "CHUNK", 256 * 1024)
    return tmp_path


class FakeGoogle:
    """Enough of oauth2.googleapis.com + the YouTube Data API for the publisher."""

    def __init__(self, *, listed_privacy=None, fail_put_once=False, quota=False,
                 refresh_error=None, expire_session_once=False, playlist_busy=0):
        self.playlist_busy = playlist_busy          # playlistItems answers 409 this many times
        self.listed_privacy = listed_privacy
        self.fail_put_once = fail_put_once
        self.quota = quota
        self.refresh_error = refresh_error
        self.expire_session_once = expire_session_once
        self.received = bytearray()
        self.size = 0
        self.insert_body: dict | None = None
        self.calls: list[str] = []
        self.token_forms: list[dict] = []
        self.playlists: list[dict] = []
        self.playlist_items: list[dict] = []
        self.thumbnails: list[bytes] = []
        self.sessions = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        self.calls.append(f"{req.method} {u.host}{u.path}")
        if u.host == "oauth2.googleapis.com" and u.path == "/token":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.token_forms.append(form)
            if form["grant_type"] == "refresh_token":
                if self.refresh_error:
                    return httpx.Response(400, json={"error": self.refresh_error,
                                                     "error_description": "Token has been expired"})
                return httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "AT1", "expires_in": 3599,
                                             "refresh_token": "RT1", "scope": " ".join(yt.SCOPES)})
        if u.host == "oauth2.googleapis.com" and u.path == "/revoke":
            return httpx.Response(200)
        assert req.headers["authorization"].startswith("Bearer AT"), "API call without a token"
        if u.path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "Kênh Thử"}}]})
        if u.path == "/upload/youtube/v3/videos" and req.method == "POST":
            if self.quota:
                return httpx.Response(403, json={"error": {"code": 403, "message": "quota",
                                                           "errors": [{"reason": "quotaExceeded"}]}})
            assert u.params["uploadType"] == "resumable"
            self.insert_body = json.loads(req.content)
            self.size = int(req.headers["x-upload-content-length"])
            self.sessions += 1
            self.received = bytearray()
            return httpx.Response(200, headers={
                "Location": f"https://www.googleapis.com/upload/youtube/v3/videos?upload_id=S{self.sessions}"})
        if u.path == "/upload/youtube/v3/videos" and req.method == "PUT":
            rng = req.headers["content-range"]
            if self.expire_session_once and self.sessions == 1:
                self.expire_session_once = False
                return httpx.Response(404, json={"error": {"message": "gone"}})
            if rng.startswith("bytes */"):
                return self._progress()
            first, last, total = map(int, re.match(r"bytes (\d+)-(\d+)/(\d+)", rng).groups())
            assert total == self.size and first == len(self.received), "upload must be contiguous"
            if self.fail_put_once and first > 0:
                self.fail_put_once = False
                return httpx.Response(503)
            self.received += req.content
            assert len(req.content) == last - first + 1
            if len(self.received) < total:
                return self._progress()
            st = dict(self.insert_body["status"])
            return httpx.Response(201, json={"id": "VID123", "status": st})
        if u.path == "/youtube/v3/videos" and req.method == "GET":
            st = dict(self.insert_body["status"])
            if self.listed_privacy:
                st = {"privacyStatus": self.listed_privacy, "uploadStatus": "uploaded"}
            return httpx.Response(200, json={"items": [{"id": "VID123", "status": st}]})
        if u.path == "/youtube/v3/playlists" and req.method == "GET":
            return httpx.Response(200, json={"items": self.playlists})
        if u.path == "/youtube/v3/playlists" and req.method == "POST":
            body = json.loads(req.content)
            pl = {"id": f"PL{len(self.playlists) + 1}", "snippet": body["snippet"]}
            self.playlists.append(pl)
            return httpx.Response(200, json=pl)
        if u.path == "/youtube/v3/playlistItems" and self.playlist_busy:
            self.playlist_busy -= 1
            return httpx.Response(409, json={"error": {"code": 409, "message": "The operation was aborted.",
                                                       "errors": [{"reason": "SERVICE_UNAVAILABLE"}]}})
        if u.path == "/youtube/v3/playlistItems":
            self.playlist_items.append(json.loads(req.content))
            return httpx.Response(200, json={"id": "PI1"})
        if u.path == "/upload/youtube/v3/thumbnails/set":
            self.thumbnails.append(req.content)
            return httpx.Response(200, json={"items": []})
        raise AssertionError(f"unexpected request {req.method} {u}")

    def _progress(self) -> httpx.Response:
        n = len(self.received)
        return httpx.Response(308, headers={"Range": f"bytes=0-{n - 1}"} if n else {})


def _connected(tmp, **extra) -> None:
    cm.save_account("youtube", {"refresh_token": "RT1", "access_token": "AT1",
                                "access_expires_at": 9e12, "channel_id": "UC1",
                                "channel_title": "Kênh Thử", **extra})


def _video(tmp, size=700_000):
    p = tmp / "ep.mp4"
    p.write_bytes(bytes(i % 251 for i in range(size)))
    return p


def _pub(fake) -> yt.YouTubePublisher:
    p = yt.YouTubePublisher()
    p.transport = httpx.MockTransport(fake)
    return p


# --- metadata -----------------------------------------------------------------------------

def test_metadata_follows_youtube_limits():
    meta = PostMeta(title="<Tập 1> " + "x" * 200, description="Mô tả <b>", kind="short",
                    tags=["#phim", "phim", "hai từ", "a,b"] + [f"t{i:03d}" for i in range(200)])
    body, scheduled = yt.video_body(meta, at=1000.0)
    sn = body["snippet"]
    assert not scheduled
    assert len(sn["title"]) == 100 and "<" not in sn["title"]
    assert sn["description"].endswith("#Shorts") and "<" not in sn["description"]
    assert sn["categoryId"] == "1"
    assert sn["tags"][:3] == ["phim", "hai từ", "a b"]
    cost = sum(len(t) + (2 if " " in t else 0) for t in sn["tags"]) + len(sn["tags"]) - 1
    assert cost <= 500
    assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}


def test_a_future_publish_time_is_a_private_scheduled_upload():
    meta = PostMeta(title="T", publish_at=1_800_000_000.0)
    body, scheduled = yt.video_body(meta, at=1_799_990_000.0)
    assert scheduled
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2027-01-15T08:00:00Z"
    # a slot that is (nearly) now is just a normal public post
    body, scheduled = yt.video_body(PostMeta(title="T", publish_at=1000.0), at=990.0)
    assert not scheduled and "publishAt" not in body["status"]


def test_long_description_is_cut_at_5000_bytes_keeping_the_shorts_tag():
    d = yt.yt_description("ừ" * 3000, "short")
    assert len(d.encode("utf-8")) <= 5000 and d.endswith("#Shorts")


# --- account state (no network) ------------------------------------------------------------

def test_available_needs_both_credentials_and_connected_needs_a_live_token(env, monkeypatch):
    p = yt.YouTubePublisher()
    assert p.available() and not p.connected() and p.account_label() == ""
    monkeypatch.setattr(settings, "youtube_client_secret", "")
    assert not p.available()
    _connected(env)
    assert p.connected() and p.account_label() == "Kênh Thử"
    cm.mark_revoked("youtube", "invalid_grant")
    assert not p.connected() and "Kết nối lại" in p.account_hint()
    _connected(env, refresh_expires_at=1.0)          # a Testing project's 7-day token, run out
    assert not p.connected()


def test_registered_in_the_registry():
    from app.providers.registry import PUBLISHERS, get_publisher

    assert isinstance(get_publisher("youtube"), yt.YouTubePublisher)
    assert "youtube" in PUBLISHERS


# --- connect: loopback + PKCE --------------------------------------------------------------

def _browser_that_consents(code="CODE1", **extra):
    """A fake browser: reads the consent URL and hits the loopback redirect like Google."""
    seen = {}

    def open_browser(url: str):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        seen.update(q)
        back = {"state": q["state"], "code": code, **extra}
        if "error" in extra:
            back.pop("code")
        target = q["redirect_uri"] + "?" + "&".join(f"{k}={v}" for k, v in back.items())

        def hit():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:                                   # browsers ask for a favicon too: ignored
                opener.open(q["redirect_uri"].rstrip("/") + "/favicon.ico", timeout=5)
            except urllib.error.HTTPError as e:
                seen["favicon"] = e.code
            with opener.open(target, timeout=5) as r:
                seen["page"] = r.read().decode("utf-8")
        threading.Thread(target=hit, daemon=True).start()
        return True
    return open_browser, seen


def test_connect_runs_the_loopback_flow_with_pkce_and_stores_the_account(env):
    fake = FakeGoogle()
    browser, seen = _browser_that_consents()
    label = _pub(fake).connect(open_browser=browser, timeout=10)
    assert label == "Kênh Thử"
    assert seen["redirect_uri"].startswith("http://127.0.0.1:")
    assert seen["code_challenge_method"] == "S256" and seen["access_type"] == "offline"
    assert set(seen["scope"].split()) == set(yt.SCOPES)
    form = fake.token_forms[0]
    assert form["code"] == "CODE1" and form["redirect_uri"] == seen["redirect_uri"]
    digest = hashlib.sha256(form["code_verifier"].encode()).digest()
    assert seen["code_challenge"] == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    acc = cm.load_account("youtube")
    assert acc["refresh_token"] == "RT1" and acc["channel_title"] == "Kênh Thử"
    assert (env / "_accounts" / "youtube.json").is_file()
    assert "vizsup" in seen["page"] and seen["favicon"] == 404


def test_connect_reports_a_denied_consent(env):
    browser, _seen = _browser_that_consents(error="access_denied")
    with pytest.raises(cm.PublishError, match="từ chối"):
        _pub(FakeGoogle()).connect(open_browser=browser, timeout=10)
    assert cm.load_account("youtube") is None


def test_connect_can_be_cancelled(env):
    with pytest.raises(cm.PublishError, match="hủy"):
        _pub(FakeGoogle()).connect(open_browser=lambda _u: True, timeout=10, cancel=lambda: True)


def test_disconnect_revokes_and_forgets(env):
    _connected(env)
    fake = FakeGoogle()
    _pub(fake).disconnect()
    assert "POST oauth2.googleapis.com/revoke" in fake.calls
    assert cm.load_account("youtube") is None


# --- publish -------------------------------------------------------------------------------

def test_publish_uploads_in_chunks_resumes_after_a_5xx_and_adds_playlist_and_thumbnail(env):
    _connected(env)
    video = _video(env)
    thumb = env / "thumb.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    fake = FakeGoogle(fail_put_once=True)
    seen: list[float] = []
    res = _pub(fake).publish(video, PostMeta(title="Tập 1", description="Mô tả",
                                             playlist="Bộ Phim", thumbnail=thumb),
                             progress=seen.append)
    assert res.ok and res.status == "published", res.message
    assert res.url == "https://youtu.be/VID123" and res.remote_id == "VID123"
    assert bytes(fake.received) == video.read_bytes()
    assert seen[-1] == 1.0 and all(a <= b for a, b in zip(seen, seen[1:]))
    assert fake.insert_body["snippet"]["title"] == "Tập 1"
    assert fake.playlists[0]["snippet"]["title"] == "Bộ Phim"
    assert fake.playlist_items[0]["snippet"]["playlistId"] == "PL1"
    assert fake.thumbnails == [b"\xff\xd8jpeg"]
    assert cm.load_account("youtube")["playlists"] == {"Bộ Phim": "PL1"}
    # the second episode reuses the cached playlist
    fake2 = FakeGoogle()
    res2 = _pub(fake2).publish(video, PostMeta(title="Tập 2", playlist="Bộ Phim"))
    assert res2.ok and not fake2.playlists and fake2.playlist_items[0]["snippet"]["playlistId"] == "PL1"


def test_a_busy_playlist_is_tried_again(env, monkeypatch):
    """2026-10-31, the first real upload: a playlist made a moment ago answered 409
    SERVICE_UNAVAILABLE "The operation was aborted" — the insert is tried again."""
    monkeypatch.setattr(yt.time, "sleep", lambda _s: None)
    _connected(env)
    fake = FakeGoogle(playlist_busy=2)
    res = _pub(fake).publish(_video(env, 1000), PostMeta(title="Tập 1", playlist="Bộ Phim"))
    assert res.ok and "đã thêm vào danh sách phát" in res.message, res.message
    assert len(fake.playlist_items) == 1
    fake = FakeGoogle(playlist_busy=9)                       # never frees → said, not raised
    res = _pub(fake).publish(_video(env, 1000), PostMeta(title="Tập 1", playlist="Bộ Phim"))
    assert res.ok and "chưa thêm được vào danh sách phát" in res.message


def test_a_short_gets_the_shorts_url(env):
    _connected(env)
    fake = FakeGoogle()
    res = _pub(fake).publish(_video(env, 1000), PostMeta(title="S", kind="short"))
    assert res.url == "https://www.youtube.com/shorts/VID123"
    assert fake.insert_body["snippet"]["description"] == "#Shorts"


def test_scheduled_post(env):
    _connected(env)
    fake = FakeGoogle()
    res = _pub(fake).publish(_video(env, 1000),
                             PostMeta(title="T", publish_at=cm.now() + 86400))
    assert res.ok and res.status == "scheduled", res.message
    assert fake.insert_body["status"]["privacyStatus"] == "private"
    assert "publishAt" in fake.insert_body["status"]


def test_a_test_post_stays_private_without_a_publish_time(env):
    _connected(env)
    fake = FakeGoogle(listed_privacy="private")
    res = _pub(fake).publish(_video(env, 1000), PostMeta(title="T", private=True,
                                                         publish_at=cm.now() + 86400))
    assert res.ok and res.status == "private" and "đăng thử" in res.message
    assert "audit" not in res.message                         # asked for, not locked
    assert fake.insert_body["status"]["privacyStatus"] == "private"
    assert "publishAt" not in fake.insert_body["status"]      # never goes public by itself


def test_private_although_public_was_asked_is_reported_as_locked(env):
    _connected(env)
    res = _pub(FakeGoogle(listed_privacy="private")).publish(_video(env, 1000), PostMeta(title="T"))
    assert res.ok and res.status == "private"
    assert "YouTube Studio" in res.message and "audit" in res.message


def test_expired_session_starts_once_more(env):
    _connected(env)
    video = _video(env)
    fake = FakeGoogle(expire_session_once=True)
    res = _pub(fake).publish(video, PostMeta(title="T"))
    assert res.ok and fake.sessions == 2 and bytes(fake.received) == video.read_bytes()


def test_quota_is_explained_in_vietnamese(env):
    _connected(env)
    res = _pub(FakeGoogle(quota=True)).publish(_video(env, 1000), PostMeta(title="T"))
    assert not res.ok and res.status == "failed" and "hạn mức" in res.message


def test_a_revoked_refresh_token_asks_to_reconnect(env):
    _connected(env, access_expires_at=0)        # access token stale → refresh → invalid_grant
    p = _pub(FakeGoogle(refresh_error="invalid_grant"))
    res = p.publish(_video(env, 1000), PostMeta(title="T"))
    assert not res.ok and "Kết nối lại" in res.message
    assert not p.connected()


def test_not_connected_and_missing_file(env):
    p = _pub(FakeGoogle())
    assert "Chưa kết nối" in p.publish(_video(env, 10), PostMeta(title="T")).message
    _connected(env)
    assert "Không thấy" in p.publish(env / "nope.mp4", PostMeta(title="T")).message


def test_a_dead_connection_is_raised_after_the_retries(env):
    _connected(env)

    def down(req):
        raise httpx.ConnectError("[WinError 10065] unreachable", request=req)
    p = yt.YouTubePublisher()
    p.transport = httpx.MockTransport(down)
    from app.net import looks_like_network_error

    with pytest.raises(httpx.ConnectError) as ei:
        p.publish(_video(env, 1000), PostMeta(title="T"))
    assert looks_like_network_error(ei.value)
