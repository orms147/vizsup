"""YouTube — long videos and Shorts through the YouTube Data API v3 (official, the owner's own
Google Cloud project; setup in docs/plan/J-publish-setup.md).

Linking (once, "Kết nối" in Settings): OAuth 2.0 for installed apps — a "Desktop app" OAuth
client, the system browser, loopback redirect ``http://127.0.0.1:<random port>/`` caught by a
one-shot local server, PKCE S256, ``access_type=offline`` + ``prompt=consent`` so Google
returns a refresh token. Stored in ``<storage>/_accounts/youtube.json`` (refresh token, the
current access token, channel id/title, the series → playlist cache).

Posting: ``videos.insert`` resumable upload (8 MiB chunks; 5xx / dropped connection → back
off, ask the session how far it got, resume; 404 = session expired → one fresh session),
snippet (title ≤ 100 chars without < >, description ≤ 5000 bytes, tags ≤ 500 chars, category
1 = Film & Animation), status (public, or private + ``publishAt`` for a scheduled post,
``selfDeclaredMadeForKids`` false). A Short is the same upload of a vertical/square file of at
most 3 minutes — YouTube classifies it; "#Shorts" is added to the description. Optional:
the series' playlist (found by title or created, then ``playlistItems.insert``) and
``thumbnails.set`` when ``meta.thumbnail`` is given — both best-effort, a failure there only
adds a note to the result message.

Facts checked on the web 2026-10-31 (re-check before relying on them):
- videos.insert reference (last updated 2026-10-15),
  https://developers.google.com/youtube/v3/docs/videos/insert — "Quota impact: 100 calls per
  day … 1 unit in the Video Uploads quota bucket"; max file 256 GB; MIME video/* or
  application/octet-stream. It NOW SAYS: "Videos uploaded from unverified API projects are not
  restricted to private viewing mode" and "Certain uploads might default to private based on
  creator account settings". The 2020 rule (revision history, 2020-07-28: unaudited projects
  created after 28 July 2020 locked private) is no longer what the reference states. The
  publisher still CHECKS: when the listed video is private although public / scheduled was
  asked, it returns ``PostResult(status="private")`` telling the owner to publish in YouTube
  Studio (and to request the API audit if it keeps happening).
- videos resource, https://developers.google.com/youtube/v3/docs/videos — title ≤ 100 chars,
  no < >; description ≤ 5000 bytes; tags ≤ 500 chars incl. commas and quotes around spaced
  tags; ``publishAt`` "can only be set if the video's privacy status is private and the video
  has never been published"; a past publishAt publishes at once.
- Resumable upload, https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
  — chunks a multiple of 256 KB; 308 + ``Range: bytes=0-N``; retry 500/502/503/504; status
  query ``Content-Range: bytes */TOTAL``; 404 = session expired.
- OAuth for desktop apps, https://developers.google.com/identity/protocols/oauth2/native-app
  (updated 2026-10-30) — loopback ``http://127.0.0.1:port``, "Always use PKCE", S256.
- https://developers.google.com/identity/protocols/oauth2 (updated 2026-10-29) — a project
  whose consent screen is External + "Testing" gets refresh tokens that expire in 7 DAYS
  (YouTube scopes are not exempt) → the guide tells the owner to press "Publish app".
- Shorts: square or vertical uploads up to three minutes count as Shorts (since 2024-10-15),
  https://support.google.com/youtube/answer/15424877
- thumbnails.set, https://developers.google.com/youtube/v3/docs/thumbnails/set — ≤ 50 MB,
  image/jpeg or image/png; 403 when the user may not set custom thumbnails.
"""
from __future__ import annotations

import re
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.providers.base import PostMeta, PostResult, PublishProvider
from app.providers.publish import _common as cm

PLATFORM = "youtube"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube")     # channel name, playlists
CATEGORY = "1"                       # Film & Animation
CHUNK = 32 * 256 * 1024              # 8 MiB — a multiple of 256 KiB
MAX_FAILS = 8
SCHEDULE_MIN_AHEAD = 300             # nearer than 5 min → just post now
PLAYLIST_WAITS = (2.0, 5.0, 10.0)    # playlistItems.insert 409 "operation aborted" → try again
_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
         ".mkv": "video/x-matroska", ".webm": "video/webm"}

LOCKED_MSG = ("YouTube để video ở chế độ RIÊNG TƯ dù vizsup yêu cầu công khai/hẹn giờ — do cài "
              "đặt kênh, hoặc dự án Google Cloud chưa qua kiểm duyệt API (audit). Mở YouTube "
              "Studio → Nội dung để đặt Công khai. Nếu lần nào cũng vậy, xin audit theo "
              "docs/plan/J-publish-setup.md.")


# --- metadata (pure) -----------------------------------------------------------------------

def _no_angle(text: str) -> str:
    return re.sub(r"[<>]", "", text or "")


def yt_title(title: str) -> str:
    t = " ".join(_no_angle(title).split())
    return t[:100].rstrip() or "Video"


def _cut_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit].decode("utf-8", "ignore").rstrip()


def yt_description(description: str, kind: str) -> str:
    d = _no_angle(description).strip()
    if kind == "short" and "#shorts" not in d.lower():
        tail = "\n\n#Shorts" if d else "#Shorts"
        return _cut_bytes(d, 5000 - len(tail.encode("utf-8"))) + tail
    return _cut_bytes(d, 5000)


def yt_tags(tags: list[str]) -> list[str]:
    """Tags without '#', commas or < >, de-duplicated, ≤ 500 chars the way YouTube counts
    them (commas between tags, quotes around a tag with a space)."""
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in tags:
        t = " ".join(_no_angle(raw).lstrip("#").replace(",", " ").split())
        if not t or t.lower() in seen:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if total + cost > 500:
            break
        out.append(t)
        seen.add(t.lower())
        total += cost
    return out


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def video_body(meta: PostMeta, *, at: float | None = None) -> tuple[dict, bool]:
    """(videos.insert body, scheduled?). ``at`` = now (tests pin it)."""
    at = time.time() if at is None else at
    scheduled = bool(not meta.private and meta.publish_at
                     and meta.publish_at - at >= SCHEDULE_MIN_AHEAD)
    status: dict = {"privacyStatus": "private" if scheduled or meta.private else "public",
                    "selfDeclaredMadeForKids": False}
    if scheduled:
        status["publishAt"] = _iso_utc(meta.publish_at)
    snippet = {"title": yt_title(meta.title),
               "description": yt_description(meta.description, meta.kind),
               "categoryId": CATEGORY}
    tags = yt_tags(meta.tags)
    if tags:
        snippet["tags"] = tags
    return {"snippet": snippet, "status": status}, scheduled


def _range_next(resp: httpx.Response) -> int:
    """First byte the server still needs, from a 308's ``Range: bytes=0-N`` (none → 0)."""
    m = re.search(r"bytes=\d+-(\d+)", resp.headers.get("range", ""))
    return int(m.group(1)) + 1 if m else 0


def _g_error(resp: httpx.Response) -> tuple[str, str]:
    """(reason, message) of a Google API / OAuth error body."""
    data = cm.json_or_empty(resp)
    err = data.get("error")
    if isinstance(err, str):                       # OAuth endpoints
        return err, str(data.get("error_description", ""))
    err = err if isinstance(err, dict) else {}
    reasons = [e.get("reason", "") for e in err.get("errors") or [] if isinstance(e, dict)]
    return (reasons[0] if reasons else str(err.get("status", ""))), str(err.get("message", ""))


def explain(resp: httpx.Response) -> str:
    reason, msg = _g_error(resp)
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        return ("Hết hạn mức API YouTube hôm nay (quota) — hạn mức đặt lại lúc 0 giờ giờ Thái "
                "Bình Dương (khoảng 14–15 giờ chiều giờ Việt Nam). Video sẽ đăng lại sau.")
    if reason == "uploadLimitExceeded":
        return "Kênh đã chạm giới hạn số video tải lên trong ngày của YouTube — đăng lại ngày mai."
    if reason == "youtubeSignupRequired":
        return "Tài khoản Google này chưa có kênh YouTube — tạo kênh rồi Kết nối lại."
    if reason in ("insufficientPermissions", "forbidden", "accessNotConfigured"):
        extra = (" (YouTube Data API v3 chưa bật trong dự án Google Cloud)"
                 if reason == "accessNotConfigured" else "")
        return f"YouTube từ chối quyền{extra}: {msg or reason}. Kết nối lại và cho phép đủ quyền."
    if reason.startswith("invalid"):
        return f"YouTube từ chối thông tin video ({reason}): {msg}"
    return f"YouTube từ chối ({resp.status_code} {reason}): {msg}".rstrip(": ")


class YouTubePublisher(PublishProvider):
    name = PLATFORM
    display_name = "YouTube"
    cost_note = "miễn phí · tối đa ~100 video/ngày (hạn mức API) · video dài + Shorts"
    unavailable_note = "cần Client ID / Client secret (Cài đặt → Đăng video)"
    kinds = ("long", "short")
    can_schedule = True                 # status.publishAt
    auth_kind = "loopback"              # Settings: Kết nối = browser + local redirect
    transport: httpx.BaseTransport | None = None   # tests: httpx.MockTransport

    # --- account ----------------------------------------------------------------------------
    def available(self) -> bool:
        return bool(settings.youtube_client_id.strip() and settings.youtube_client_secret.strip())

    def connected(self) -> bool:
        acc = cm.load_account(PLATFORM)
        if not acc or not acc.get("refresh_token") or acc.get("revoked"):
            return False
        exp = acc.get("refresh_expires_at")
        return not exp or exp > time.time()

    def account_label(self) -> str:
        acc = cm.load_account(PLATFORM) if self.connected() else None
        return (acc or {}).get("channel_title", "")

    def account_hint(self) -> str:
        """Extra line for Settings: when the refresh token runs out (Testing projects)."""
        acc = cm.load_account(PLATFORM) or {}
        exp = acc.get("refresh_expires_at")
        if self.connected() and exp:
            when = time.strftime("%d/%m/%Y", time.localtime(exp))
            return (f"Google chỉ giữ kết nối tới {when} (dự án ở chế độ Testing) — bấm "
                    "“Publish app” trong Google Cloud để khỏi phải nối lại.")
        if acc.get("revoked"):
            return "Kết nối cũ đã hết hạn hoặc bị gỡ — bấm Kết nối lại."
        return ""

    def connect(self, *, open_browser=webbrowser.open, timeout: float = 300.0,
                cancel=None) -> str:
        """Browser consent → refresh token + channel. Returns the channel title."""
        if not self.available():
            raise cm.PublishError("Chưa nhập Client ID / Client secret của YouTube.")
        verifier, challenge = cm.pkce_pair()

        def url_for(redirect_uri: str, state: str) -> str:
            return AUTH_URL + "?" + urlencode({
                "client_id": settings.youtube_client_id.strip(), "redirect_uri": redirect_uri,
                "response_type": "code", "scope": " ".join(SCOPES), "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256",
                "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})

        code, redirect_uri = cm.run_loopback(url_for, path="/", open_browser=open_browser,
                                             timeout=timeout, cancel=cancel)
        with cm.client(self.transport) as c:
            r = c.post(TOKEN_URL, data={
                "code": code, "client_id": settings.youtube_client_id.strip(),
                "client_secret": settings.youtube_client_secret.strip(),
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                "code_verifier": verifier})
            tok = cm.json_or_empty(r)
            if r.status_code != 200 or not tok.get("access_token"):
                reason, msg = _g_error(r)
                raise cm.PublishError(f"Google từ chối mã đăng nhập: {msg or reason or r.status_code}")
            if not tok.get("refresh_token"):
                raise cm.PublishError("Google không trả refresh token — gỡ quyền của ứng dụng ở "
                                      "https://myaccount.google.com/permissions rồi Kết nối lại.")
            t0 = time.time()
            acc = {"refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
                   "access_expires_at": t0 + float(tok.get("expires_in", 3600)),
                   "scope": tok.get("scope", ""), "connected_at": t0}
            if tok.get("refresh_token_expires_in"):
                acc["refresh_expires_at"] = t0 + float(tok["refresh_token_expires_in"])
            r = c.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                      headers=self._auth(acc["access_token"]))
            if r.status_code != 200:
                raise cm.PublishError(explain(r))
            items = cm.json_or_empty(r).get("items") or []
            if not items:
                raise cm.PublishError("Tài khoản Google này chưa có kênh YouTube — tạo kênh rồi "
                                      "Kết nối lại.")
            acc["channel_id"] = items[0].get("id", "")
            acc["channel_title"] = (items[0].get("snippet") or {}).get("title", "") or "kênh YouTube"
        old = cm.load_account(PLATFORM) or {}
        if old.get("channel_id") == acc["channel_id"]:
            acc["playlists"] = old.get("playlists") or {}
        cm.save_account(PLATFORM, acc)
        return acc["channel_title"]

    def disconnect(self) -> None:
        """Revoke the grant at Google (best effort) and forget the account."""
        acc = cm.load_account(PLATFORM) or {}
        token = acc.get("refresh_token") or acc.get("access_token")
        if token:
            try:
                with cm.client(self.transport) as c:
                    c.post(REVOKE_URL, data={"token": token}, timeout=10.0)
            except httpx.HTTPError:
                pass
        cm.forget_account(PLATFORM)

    # --- tokens -----------------------------------------------------------------------------
    @staticmethod
    def _auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def _token(self, c: httpx.Client, acc: dict, *, force: bool = False) -> str:
        if not force and acc.get("access_token") and acc.get("access_expires_at", 0) > time.time() + 60:
            return acc["access_token"]
        r = c.post(TOKEN_URL, data={
            "client_id": settings.youtube_client_id.strip(),
            "client_secret": settings.youtube_client_secret.strip(),
            "refresh_token": acc.get("refresh_token", ""), "grant_type": "refresh_token"})
        tok = cm.json_or_empty(r)
        if r.status_code != 200 or not tok.get("access_token"):
            reason, msg = _g_error(r)
            if reason in ("invalid_grant", "unauthorized_client", "invalid_client"):
                cm.mark_revoked(PLATFORM, reason)
                raise cm.AuthExpired("Kết nối YouTube đã hết hạn hoặc bị gỡ (dự án ở chế độ "
                                     "Testing chỉ giữ 7 ngày) — Cài đặt → Đăng video → Kết nối lại.")
            raise cm.PublishError(f"Không làm mới được quyền YouTube: {msg or reason or r.status_code}")
        acc["access_token"] = tok["access_token"]
        acc["access_expires_at"] = time.time() + float(tok.get("expires_in", 3600))
        cm.save_account(PLATFORM, acc)
        return acc["access_token"]

    # --- upload -----------------------------------------------------------------------------
    def _start_session(self, c: httpx.Client, acc: dict, body: dict, size: int, mime: str) -> str:
        last: Exception | None = None
        for attempt in range(MAX_FAILS):
            try:
                r = c.post(f"{UPLOAD_API}/videos",
                           params={"uploadType": "resumable", "part": "snippet,status"},
                           json=body, headers={**self._auth(self._token(c, acc)),
                                               "X-Upload-Content-Length": str(size),
                                               "X-Upload-Content-Type": mime})
            except httpx.TransportError as e:
                last = e
                cm.backoff(attempt)
                continue
            if r.status_code == 200 and r.headers.get("location"):
                return r.headers["location"]
            if r.status_code == 401 and attempt == 0:
                self._token(c, acc, force=True)
                continue
            if r.status_code in (500, 502, 503, 504):
                cm.backoff(attempt)
                continue
            raise cm.PublishError(explain(r))
        if last is not None:
            raise last
        raise cm.PublishError("Máy chủ YouTube lỗi liên tục khi mở phiên tải lên — thử lại sau.")

    def _upload(self, c: httpx.Client, acc: dict, video: Path, body: dict, progress) -> dict:
        size = video.stat().st_size
        mime = _MIME.get(video.suffix.lower(), "application/octet-stream")
        session = self._start_session(c, acc, body, size, mime)
        offset, fails, stalls, restarted, need_query = 0, 0, 0, False, False
        last: Exception | None = None
        while True:
            auth = self._auth(self._token(c, acc))
            sent_chunk = not (need_query or offset >= size)
            try:
                if not sent_chunk:
                    resp = c.put(session, headers={**auth, "Content-Range": f"bytes */{size}"})
                else:
                    end = min(offset + CHUNK, size) - 1
                    resp = c.put(session, content=cm.read_range(video, offset, end - offset + 1),
                                 headers={**auth, "Content-Type": mime,
                                          "Content-Range": f"bytes {offset}-{end}/{size}"})
            except httpx.TransportError as e:
                resp, last = None, e
            code = resp.status_code if resp is not None else 0
            if code in (200, 201):
                return cm.json_or_empty(resp)
            if code == 308:
                nxt = _range_next(resp)
                stalls = stalls + 1 if sent_chunk and nxt <= offset else 0
                if stalls > MAX_FAILS:
                    raise cm.PublishError("YouTube không nhận thêm dữ liệu khi tải lên — thử lại "
                                          "sau.")
                offset, fails, need_query = nxt, 0, False
                if progress:
                    progress(min(0.99, offset / size))
                continue
            if code == 404 and not restarted:            # session expired → start over once
                session = self._start_session(c, acc, body, size, mime)
                offset, restarted, need_query = 0, True, False
                continue
            if code == 401 and fails < MAX_FAILS:
                fails += 1
                self._token(c, acc, force=True)
                need_query = True
                continue
            if code in (0, 500, 502, 503, 504):
                fails += 1
                if fails > MAX_FAILS:
                    if resp is None and last is not None:
                        raise last
                    raise cm.PublishError(f"Máy chủ YouTube lỗi liên tục ({code}) khi tải lên — "
                                          "thử lại sau.")
                cm.backoff(fails)
                need_query = True
                continue
            if code == 404:
                raise cm.PublishError("Phiên tải lên YouTube hết hạn hai lần — thử lại sau.")
            raise cm.PublishError(explain(resp))

    # --- after the upload -------------------------------------------------------------------
    def _video_status(self, c: httpx.Client, acc: dict, vid: str) -> dict:
        try:
            r = c.get(f"{API}/videos", params={"part": "status", "id": vid},
                      headers=self._auth(self._token(c, acc)))
        except httpx.TransportError:
            return {}
        items = (cm.json_or_empty(r).get("items") or []) if r.status_code == 200 else []
        return (items[0].get("status") or {}) if items else {}

    def _playlist_id(self, c: httpx.Client, acc: dict, title: str, *, fresh: bool = False) -> str:
        cache = acc.setdefault("playlists", {})
        if not fresh and cache.get(title):
            return cache[title]
        auth = self._auth(self._token(c, acc))
        page = None
        for _ in range(20):
            params = {"part": "snippet", "mine": "true", "maxResults": "50"}
            if page:
                params["pageToken"] = page
            r = c.get(f"{API}/playlists", params=params, headers=auth)
            if r.status_code != 200:
                raise cm.PublishError(explain(r))
            data = cm.json_or_empty(r)
            for it in data.get("items") or []:
                if ((it.get("snippet") or {}).get("title", "").strip().casefold()
                        == title.strip().casefold()):
                    cache[title] = it["id"]
                    cm.save_account(PLATFORM, acc)
                    return it["id"]
            page = data.get("nextPageToken")
            if not page:
                break
        r = c.post(f"{API}/playlists", params={"part": "snippet,status"}, headers=auth,
                   json={"snippet": {"title": _no_angle(title)[:150]},
                         "status": {"privacyStatus": "public"}})
        if r.status_code not in (200, 201):
            raise cm.PublishError(explain(r))
        cache[title] = cm.json_or_empty(r).get("id", "")
        cm.save_account(PLATFORM, acc)
        return cache[title]

    def _add_to_playlist(self, c: httpx.Client, acc: dict, title: str, vid: str) -> str:
        for fresh in (False, True):
            pid = self._playlist_id(c, acc, title, fresh=fresh)
            for wait in (*PLAYLIST_WAITS, None):
                r = c.post(f"{API}/playlistItems", params={"part": "snippet"},
                           headers=self._auth(self._token(c, acc)),
                           json={"snippet": {"playlistId": pid, "resourceId": {
                               "kind": "youtube#video", "videoId": vid}}})
                # a playlist made a moment ago (or a busy backend) answers 409
                # SERVICE_UNAVAILABLE "The operation was aborted" — seen 2026-10-31
                if r.status_code != 409 or wait is None:
                    break
                time.sleep(wait)
            if r.status_code in (200, 201):
                return f"đã thêm vào danh sách phát “{title}”"
            if r.status_code != 404:
                return f"chưa thêm được vào danh sách phát: {explain(r)}"
            acc.get("playlists", {}).pop(title, None)       # deleted on YouTube → look again
        return "chưa thêm được vào danh sách phát (không tìm thấy)"

    def _set_thumbnail(self, c: httpx.Client, acc: dict, vid: str, thumb: Path) -> str:
        mime = "image/png" if thumb.suffix.lower() == ".png" else "image/jpeg"
        r = c.post(f"{UPLOAD_API}/thumbnails/set", params={"videoId": vid, "uploadType": "media"},
                   content=thumb.read_bytes(),
                   headers={**self._auth(self._token(c, acc)), "Content-Type": mime})
        if r.status_code in (200, 201):
            return "đã đặt ảnh bìa"
        if r.status_code == 403:
            return ("chưa đặt được ảnh bìa — kênh cần xác minh (số điện thoại) để dùng ảnh bìa "
                    "riêng: youtube.com/verify")
        return f"chưa đặt được ảnh bìa: {explain(r)}"

    # --- publish ----------------------------------------------------------------------------
    def publish(self, video, meta: PostMeta, *, progress=None) -> PostResult:
        video = Path(video)
        if not video.is_file():
            return PostResult(False, "failed", message=f"Không thấy tệp video: {video}")
        acc = cm.load_account(PLATFORM)
        if not self.available() or not acc or not self.connected():
            return PostResult(False, "failed", message="Chưa kết nối YouTube — Cài đặt → Đăng "
                                                       "video → Kết nối.")
        body, scheduled = video_body(meta)
        try:
            with cm.client(self.transport) as c:
                res = self._upload(c, acc, video, body, progress)
                vid = res.get("id", "")
                if not vid:
                    return PostResult(False, "failed", message="YouTube không trả mã video sau "
                                                               "khi tải lên.")
                st = self._video_status(c, acc, vid) or res.get("status") or {}
                notes: list[str] = []
                if meta.playlist.strip():
                    try:
                        notes.append(self._add_to_playlist(c, acc, meta.playlist.strip(), vid))
                    except cm.PublishError as e:
                        notes.append(f"chưa thêm được vào danh sách phát: {e}")
                if meta.thumbnail and Path(meta.thumbnail).is_file():
                    try:
                        notes.append(self._set_thumbnail(c, acc, vid, Path(meta.thumbnail)))
                    except (cm.PublishError, OSError) as e:
                        notes.append(f"chưa đặt được ảnh bìa: {e}")
        except cm.PublishError as e:
            return PostResult(False, "failed", message=str(e))
        if progress:
            progress(1.0)
        url = (f"https://www.youtube.com/shorts/{vid}" if meta.kind == "short"
               else f"https://youtu.be/{vid}")
        tail = ("; " + "; ".join(notes)) if notes else ""
        if st.get("uploadStatus") == "rejected":
            return PostResult(False, "failed", url=url, remote_id=vid,
                              message=f"YouTube từ chối video ({st.get('rejectionReason', '')})."
                                      + tail)
        privacy = st.get("privacyStatus", body["status"]["privacyStatus"])
        if meta.private:                                 # a test post: private as asked
            return PostResult(True, "private", url=url, remote_id=vid,
                              message="Đã tải lên YouTube ở chế độ RIÊNG TƯ (đăng thử) — chỉ "
                                      "bạn xem được trong YouTube Studio" + tail)
        # nothing listed → trust the request; a scheduled video that lost its publishAt is
        # as locked as a public one that came back private
        locked = (privacy == "private" and not scheduled) or bool(
            scheduled and st and (privacy != "private" or not st.get("publishAt")))
        if locked:
            return PostResult(True, "private", url=url, remote_id=vid, message=LOCKED_MSG + tail)
        if scheduled:
            when = time.strftime("%H:%M %d/%m/%Y", time.localtime(meta.publish_at))
            return PostResult(True, "scheduled", url=url, remote_id=vid,
                              message=f"Đã tải lên YouTube, hẹn giờ công khai lúc {when}" + tail)
        return PostResult(True, "published", url=url, remote_id=vid,
                          message=f"Đã đăng lên YouTube: {url}" + tail)
