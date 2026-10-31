"""TikTok publisher: desktop Login Kit (hex PKCE, /callback/), creator_info → direct post with
chunked FILE_UPLOAD → status, the unaudited-client paths (SELF_ONLY on a private account;
inbox draft on a public one), token refresh and errors. A fake TikTok (httpx.MockTransport)
answers everything — no network, no real post."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.request
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import settings
from app.providers.base import PostMeta
from app.providers.publish import _common as cm
from app.providers.publish import tiktok as tt

ALL_SCOPES = "user.info.basic,video.publish,video.upload"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    monkeypatch.setattr(settings, "tiktok_client_key", "ckey")
    monkeypatch.setattr(settings, "tiktok_client_secret", "csecret")
    monkeypatch.setattr(cm, "_sleep", lambda _s: None)
    monkeypatch.setattr(cm, "probe_seconds", lambda _p: 0.0)
    monkeypatch.setattr(tt, "CHUNK", 100_000)
    return tmp_path


class FakeTikTok:
    def __init__(self, *, options=("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"),
                 audited=True, creator_error=None, fail_reason=None, refresh_error=None,
                 put_fail_once=False):
        self.options = list(options)
        self.audited = audited
        self.creator_error = creator_error
        self.fail_reason = fail_reason
        self.refresh_error = refresh_error
        self.put_fail_once = put_fail_once
        self.calls: list[str] = []
        self.token_forms: list[dict] = []
        self.init: dict | None = None
        self.init_path = ""
        self.received = bytearray()
        self.ranges: list[str] = []
        self.polls = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        self.calls.append(f"{req.method} {u.path}")
        if u.path == "/v2/oauth/token/":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.token_forms.append(form)
            if form["grant_type"] == "refresh_token" and self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error,
                                                 "error_description": "expired"})
            n = len(self.token_forms)
            return httpx.Response(200, json={
                "access_token": f"act.{n}", "expires_in": 86400, "open_id": "OID",
                "refresh_token": f"rft.{n}", "refresh_expires_in": 31536000,
                "scope": ALL_SCOPES, "token_type": "Bearer"})
        if u.path == "/v2/oauth/revoke/":
            return httpx.Response(200)
        if u.host == "upload.example":
            rng = req.headers["content-range"]
            first, last, total = map(int, re.match(r"bytes (\d+)-(\d+)/(\d+)", rng).groups())
            assert first == len(self.received), "chunks must be sequential"
            if self.put_fail_once and first > 0:
                self.put_fail_once = False
                return httpx.Response(502)
            assert len(req.content) == last - first + 1
            self.ranges.append(rng)
            self.received += req.content
            return httpx.Response(201 if len(self.received) == total else 206)
        assert req.headers["authorization"].startswith("Bearer act."), "API call without a token"
        if u.path == "/v2/user/info/":
            return httpx.Response(200, json={"data": {"user": {"open_id": "OID",
                                                               "display_name": "Bé Phim"}},
                                             "error": {"code": "ok"}})
        body = json.loads(req.content or b"{}")
        if u.path == "/v2/post/publish/creator_info/query/":
            if self.creator_error:
                return httpx.Response(200, json={"data": {}, "error": {"code": self.creator_error,
                                                                       "message": "x"}})
            return httpx.Response(200, json={"data": {
                "creator_username": "bephim", "creator_nickname": "Bé Phim",
                "privacy_level_options": self.options, "comment_disabled": False,
                "duet_disabled": True, "stitch_disabled": False,
                "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
        if u.path in ("/v2/post/publish/video/init/", "/v2/post/publish/inbox/video/init/"):
            direct = u.path == "/v2/post/publish/video/init/"
            if direct and not self.audited and "PUBLIC_TO_EVERYONE" in self.options:
                return httpx.Response(403, json={"error": {
                    "code": "unaudited_client_can_only_post_to_private_accounts", "message": "no"}})
            self.init, self.init_path = body, u.path
            return httpx.Response(200, json={"data": {"publish_id": "v_pub_1",
                                                      "upload_url": "https://upload.example/up?id=1"},
                                             "error": {"code": "ok"}})
        if u.path == "/v2/post/publish/status/fetch/":
            self.polls += 1
            if self.polls == 1:
                return httpx.Response(200, json={"data": {"status": "PROCESSING_UPLOAD"},
                                                 "error": {"code": "ok"}})
            if self.fail_reason:
                st = {"status": "FAILED", "fail_reason": self.fail_reason}
            elif "inbox" in self.init_path:
                st = {"status": "SEND_TO_USER_INBOX"}
            else:
                st = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300000000001]}
            return httpx.Response(200, json={"data": st, "error": {"code": "ok"}})
        raise AssertionError(f"unexpected {req.method} {u}")


def _pub(fake) -> tt.TikTokPublisher:
    p = tt.TikTokPublisher()
    p.transport = httpx.MockTransport(fake)
    return p


def _connected(**extra) -> None:
    cm.save_account("tiktok", {"access_token": "act.0", "expires_at": 9e12,
                               "refresh_token": "rft.0", "refresh_expires_at": 9e12,
                               "scope": ALL_SCOPES, "open_id": "OID", "username": "bephim",
                               "display_name": "Bé Phim", **extra})


def _video(tmp, size=250_000):
    p = tmp / "clip.mp4"
    p.write_bytes(bytes(i % 239 for i in range(size)))
    return p


# --- pure ----------------------------------------------------------------------------------

def test_chunk_plan_follows_the_media_transfer_rules():
    assert tt.chunk_plan(4_000_000) == (4_000_000, 1)              # < 5 MB: whole
    assert tt.chunk_plan(25_000_000) == (10_000_000, 2)            # remainder on the last
    size = 20_000_000_000
    chunk, count = tt.chunk_plan(size)
    assert count <= 1000 and chunk <= 64_000_000 and size - (count - 1) * chunk <= 128_000_000


# --- connect -------------------------------------------------------------------------------

def test_connect_uses_the_desktop_rules_hex_pkce_and_callback_path(env):
    seen: dict = {}

    def browser(url):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        seen.update(q)
        target = f"{q['redirect_uri']}?code=CODE9&scopes={ALL_SCOPES}&state={q['state']}"

        def hit():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            opener.open(target, timeout=5).close()
        threading.Thread(target=hit, daemon=True).start()
        return True

    fake = FakeTikTok()
    assert _pub(fake).connect(open_browser=browser, timeout=10) == "Bé Phim"
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/callback/", seen["redirect_uri"])
    assert seen["client_key"] == "ckey" and seen["scope"] == ALL_SCOPES
    form = fake.token_forms[0]
    assert form["code"] == "CODE9" and form["redirect_uri"] == seen["redirect_uri"]
    assert seen["code_challenge"] == hashlib.sha256(form["code_verifier"].encode()).hexdigest()
    acc = cm.load_account("tiktok")
    assert acc["refresh_token"] == "rft.1" and acc["username"] == "bephim"
    assert tt.TikTokPublisher().connected()


def test_disconnect_revokes_and_forgets(env):
    _connected()
    fake = FakeTikTok()
    _pub(fake).disconnect()
    assert "POST /v2/oauth/revoke/" in fake.calls and cm.load_account("tiktok") is None


# --- publish -------------------------------------------------------------------------------

def test_direct_post_public_uploads_chunks_and_reports_the_post_url(env):
    _connected()
    video = _video(env)
    fake = FakeTikTok(put_fail_once=True)
    seen: list[float] = []
    res = _pub(fake).publish(video, PostMeta(title="Tập 1", description="Mô tả",
                                             tags=["phim", "#review"], kind="short"),
                             progress=seen.append)
    assert res.ok and res.status == "published", res.message
    assert res.url == "https://www.tiktok.com/@bephim/video/7300000000001"
    info = fake.init["post_info"]
    assert info["privacy_level"] == "PUBLIC_TO_EVERYONE" and info["is_aigc"] is True
    assert info["disable_duet"] is True                      # creator_info says duets are off
    assert info["title"] == "Tập 1\n\nMô tả\n\n#phim #review"
    src = fake.init["source_info"]
    assert src == {"source": "FILE_UPLOAD", "video_size": 250_000, "chunk_size": 100_000,
                   "total_chunk_count": 2}
    assert fake.ranges == ["bytes 0-99999/250000", "bytes 100000-249999/250000"]
    assert bytes(fake.received) == video.read_bytes()
    assert seen[-1] == 1.0


def test_unaudited_app_on_a_public_account_falls_back_to_the_inbox_draft(env):
    _connected()
    fake = FakeTikTok(audited=False)
    res = _pub(fake).publish(_video(env), PostMeta(title="T"))
    assert res.ok and res.status == "draft" and "HỘP THƯ" in res.message
    assert fake.init_path == "/v2/post/publish/inbox/video/init/"
    assert "post_info" not in fake.init


def test_unaudited_without_the_draft_scope_explains_the_audit(env):
    _connected(scope="user.info.basic,video.publish")
    res = _pub(FakeTikTok(audited=False)).publish(_video(env), PostMeta(title="T"))
    assert not res.ok and "audit" in res.message and "RIÊNG TƯ" in res.message


def test_a_private_account_posts_self_only_and_says_why(env):
    _connected()
    fake = FakeTikTok(options=("FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"),
                      audited=False)
    res = _pub(fake).publish(_video(env), PostMeta(title="T"))
    assert res.ok and res.status == "private" and "Chỉ mình tôi" in res.message
    assert fake.init["post_info"]["privacy_level"] == "SELF_ONLY"


def test_only_the_upload_scope_sends_a_draft(env):
    _connected(scope="user.info.basic,video.upload")
    fake = FakeTikTok()
    res = _pub(fake).publish(_video(env), PostMeta(title="T"))
    assert res.status == "draft" and "creator_info" not in " ".join(fake.calls)


def test_no_scheduling_through_the_api(env):
    _connected()
    fake = FakeTikTok()
    res = _pub(fake).publish(_video(env), PostMeta(title="T", publish_at=cm.now() + 3600))
    assert not res.ok and "hẹn giờ" in res.message and fake.calls == []


def test_an_old_access_token_is_refreshed_and_the_new_refresh_token_kept(env):
    _connected(expires_at=0)
    fake = FakeTikTok()
    assert _pub(fake).publish(_video(env), PostMeta(title="T")).ok
    assert fake.token_forms[0]["grant_type"] == "refresh_token"
    assert fake.token_forms[0]["refresh_token"] == "rft.0"
    assert cm.load_account("tiktok")["refresh_token"] == "rft.1"


def test_a_dead_refresh_token_asks_to_reconnect(env):
    _connected(expires_at=0)
    p = _pub(FakeTikTok(refresh_error="invalid_grant"))
    res = p.publish(_video(env), PostMeta(title="T"))
    assert not res.ok and "Kết nối lại" in res.message and not p.connected()


def test_errors_and_failures_are_explained(env, monkeypatch):
    _connected()
    res = _pub(FakeTikTok(creator_error="spam_risk_too_many_posts")).publish(_video(env),
                                                                             PostMeta(title="T"))
    assert not res.ok and "24 giờ" in res.message
    res = _pub(FakeTikTok(fail_reason="duration_check_failed")).publish(_video(env),
                                                                        PostMeta(title="T"))
    assert not res.ok and "độ dài" in res.message
    monkeypatch.setattr(cm, "probe_seconds", lambda _p: 900.0)
    res = _pub(FakeTikTok()).publish(_video(env), PostMeta(title="T"))
    assert not res.ok and "600 giây" in res.message


def test_state_without_network(env, monkeypatch):
    p = tt.TikTokPublisher()
    assert p.available() and not p.connected() and not p.can_schedule
    _connected(refresh_expires_at=1.0)
    assert not p.connected()
    monkeypatch.setattr(settings, "tiktok_client_key", "")
    assert not p.available()
    from app.providers.registry import get_publisher

    assert isinstance(get_publisher("tiktok"), tt.TikTokPublisher)
