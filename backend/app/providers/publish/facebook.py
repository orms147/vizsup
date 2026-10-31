"""Facebook — Page videos (kind "long") and Page Reels (kind "short") through the Graph API
v26.0 with the owner's own Meta app (setup in docs/plan/J-publish-setup.md).

Linking (once, "Kết nối" in Settings) — the flow Meta documents for DESKTOP apps, which has
no loopback redirect: the login dialog opens in the system browser with
``redirect_uri=https://www.facebook.com/connect/login_success.html`` and
``response_type=token``; Facebook lands on that "Success" page with ``#access_token=…`` in the
address bar and the owner pastes that address into vizsup (or pastes a user token made in the
Graph API Explorer for the same app — the fallback when the redirect is refused). vizsup then
swaps it for a long-lived user token (``fb_exchange_token``, needs the app secret), lists the
Pages (``/me/accounts``), the owner picks one, and ONLY that Page's token is stored in
``<storage>/_accounts/facebook.json`` — a Page token got from a long-lived user token has no
expiry date, so nothing needs refreshing. Plain-HTTP localhost redirects are accepted by Meta
only while the app is in development mode (forum reports, not in the docs) and dev-mode posts
are seen only by people with a role on the app, so loopback is NOT used.

Posting: long → ``POST graph-video…/{page}/videos`` chunked upload (upload_phase start →
transfer chunks at the server's offsets → finish with title / description and, for a
scheduled post, ``published=false`` + ``scheduled_publish_time`` 10 min – 6 months ahead).
short → the Reels API: ``/{page}/video_reels`` start → binary POST to
``rupload.facebook.com/video-upload/v26.0/{video_id}`` (resumed from ``bytes_transfered``) →
finish with ``video_state=PUBLISHED`` or ``SCHEDULED`` + ``scheduled_publish_time``
(> 10 min, ≤ 29 days ahead), ``is_ai_generated=true`` (the dub is an AI voice).

Facts checked on the web 2026-10-31:
- Reels publishing, https://developers.facebook.com/docs/video-api/guides/reels-publishing —
  permissions pages_show_list, pages_read_engagement, pages_manage_posts (Page token of a user
  with the CREATE_CONTENT task); 9:16, ≥ 540×960, 24–60 fps, 3–90 s; "30 API-published posts
  per 24-hour moving period" per Page, enforced on POST /{page_id}/video_reels (also enforced
  here from the stored post times); errors 1363040 / 1363127 / 1363128 / 1363129.
- Page videos, https://developers.facebook.com/docs/graph-api/reference/page/videos/ —
  upload_phase start/transfer/finish, upload_session_id, start_offset, video_file_chunk,
  file_size; scheduled_publish_time "between 10 mins and 6 months"; same three permissions
  (``publish_video`` is not listed any more).
- Desktop login, https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow
  — desktop apps use ``https://www.facebook.com/connect/login_success.html``, the token comes in
  the URI fragment.
- Long-lived tokens,
  https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived —
  user token ~60 days; "Long-lived Page access token do not have an expiration date".
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from app.config import settings
from app.providers.base import PostMeta, PostResult, PublishProvider
from app.providers.publish import _common as cm

PLATFORM = "facebook"
VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{VERSION}"
GRAPH_VIDEO = f"https://graph-video.facebook.com/{VERSION}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{VERSION}"
DIALOG = f"https://www.facebook.com/{VERSION}/dialog/oauth"
REDIRECT = "https://www.facebook.com/connect/login_success.html"
SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts")
REELS_PER_DAY = 30
REEL_SECONDS = (3.0, 90.0)
SCHEDULE_MIN_AHEAD = 11 * 60          # Meta: more than 10 minutes ahead
REEL_SCHEDULE_MAX = 29 * 86400
VIDEO_SCHEDULE_MAX = 180 * 86400      # "6 months"
MAX_FAILS = 6
AI_GENERATED = True                   # Reels: disclose the AI voice-over

REELS_LIMIT_MSG = ("Trang đã đăng đủ 30 Reels qua API trong 24 giờ (giới hạn của Meta) — "
                   "Reel này sẽ đăng lại khi qua 24 giờ.")
RECONNECT_MSG = ("Mã truy cập Trang Facebook đã hết hạn hoặc bị thu hồi — Cài đặt → Đăng video → "
                 "Kết nối lại.")


def parse_pasted(text: str) -> str:
    """The user token from what the owner pasted: the whole "Success" address
    (``…login_success.html#access_token=…``) or a bare token from the Graph API Explorer."""
    text = (text or "").strip().strip('"').strip()
    if not text:
        raise cm.PublishError("Chưa dán gì.")
    if "://" in text or "access_token=" in text or "error=" in text:
        parts = urlsplit(text if "://" in text else "x://x/?" + text.lstrip("#?"))
        params = dict(parse_qsl(parts.fragment)) | dict(parse_qsl(parts.query))
        if params.get("error") or params.get("error_reason"):
            why = params.get("error_description") or params.get("error_reason") or params["error"]
            raise cm.PublishError(f"Facebook báo lỗi đăng nhập: {why.replace('+', ' ')}")
        token = params.get("access_token", "")
        if not token:
            raise cm.PublishError("Địa chỉ đã dán không có access_token — sau khi bấm Tiếp tục, "
                                  "chép địa chỉ của trang “Success” (có #access_token=…).")
        return token
    if re.fullmatch(r"[A-Za-z0-9_\-|.]{30,}", text):
        return text
    raise cm.PublishError("Không nhận ra mã truy cập trong nội dung đã dán.")


def _err(data: dict) -> dict:
    e = data.get("error")
    return e if isinstance(e, dict) else ({"message": e} if e else {})


def explain(data: dict, doing: str = "", *, reel: bool = False) -> str:
    err = _err(data)
    code = err.get("code")
    msg = str(err.get("error_user_msg") or err.get("message") or "")
    if code == 190:
        return RECONNECT_MSG
    if code in (10, 200) or (isinstance(code, int) and 200 <= code < 300):
        return (f"App Meta thiếu quyền ({msg}) — app cần pages_show_list, pages_read_engagement, "
                "pages_manage_posts và bạn phải có quyền tạo nội dung trên Trang. Kết nối lại.")
    if code in (4, 17, 32, 613) or (reel and "limit" in msg.lower()):
        return REELS_LIMIT_MSG if reel else ("Facebook tạm giới hạn số lần gọi API — thử lại "
                                             "sau ít phút.")
    if code == 368:
        return f"Facebook tạm chặn Trang đăng bài: {msg}"
    if code in (1363040, 1363127, 1363128, 1363129):
        return (f"Video không hợp chuẩn Reels của Facebook (9:16, ≥ 540×960, 24–60 fps, 3–90 "
                f"giây): {msg}")
    where = f" khi {doing}" if doing else ""
    return f"Facebook từ chối{where}: {msg or code or 'không rõ lý do'}"


class FacebookPublisher(PublishProvider):
    name = PLATFORM
    display_name = "Facebook (Trang)"
    cost_note = "miễn phí · video dài lên Trang + Reels (≤ 90 giây, 30 Reels/24 giờ)"
    unavailable_note = "cần App ID / App secret (Cài đặt → Đăng video)"
    kinds = ("long", "short")
    can_schedule = True
    auth_kind = "paste"                 # Settings: open the dialog, paste the Success address
    transport: httpx.BaseTransport | None = None

    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}    # page id → page (with its token) until picked

    # --- account ----------------------------------------------------------------------------
    def available(self) -> bool:
        return bool(settings.facebook_app_id.strip() and settings.facebook_app_secret.strip())

    def connected(self) -> bool:
        acc = cm.load_account(PLATFORM)
        return bool(acc and acc.get("page_token") and acc.get("page_id") and not acc.get("revoked"))

    def account_label(self) -> str:
        return (cm.load_account(PLATFORM) or {}).get("page_name", "") if self.connected() else ""

    def account_hint(self) -> str:
        acc = cm.load_account(PLATFORM) or {}
        return "Kết nối cũ đã hết hạn hoặc bị gỡ — bấm Kết nối lại." if acc.get("revoked") else ""

    def login_url(self, state: str = "vizsup") -> str:
        return DIALOG + "?" + urlencode({
            "client_id": settings.facebook_app_id.strip(), "redirect_uri": REDIRECT,
            "response_type": "token", "scope": ",".join(SCOPES), "state": state})

    def fetch_pages(self, pasted: str) -> list[dict]:
        """Pasted address / token → the Pages this account can post to:
        ``[{"id", "name", "can_post"}]`` (their tokens stay inside, for ``choose_page``)."""
        if not self.available():
            raise cm.PublishError("Chưa nhập App ID / App secret của Facebook.")
        token = parse_pasted(pasted)
        with cm.client(self.transport) as c:
            r = c.get(f"{GRAPH}/oauth/access_token", params={
                "grant_type": "fb_exchange_token", "client_id": settings.facebook_app_id.strip(),
                "client_secret": settings.facebook_app_secret.strip(), "fb_exchange_token": token})
            data = cm.json_or_empty(r)
            if r.status_code != 200 or not data.get("access_token"):
                raise cm.PublishError(explain(data, "đổi mã dài hạn"))
            auth = {"Authorization": f"OAuth {data['access_token']}"}
            pages: list[dict] = []
            params = {"fields": "id,name,access_token,tasks", "limit": "100"}
            for _ in range(20):
                r = c.get(f"{GRAPH}/me/accounts", params=params, headers=auth)
                d = cm.json_or_empty(r)
                if r.status_code != 200:
                    raise cm.PublishError(explain(d, "lấy danh sách Trang"))
                pages += [p for p in d.get("data") or [] if isinstance(p, dict)]
                after = ((d.get("paging") or {}).get("cursors") or {}).get("after")
                if not after or not (d.get("paging") or {}).get("next"):
                    break
                params = {**params, "after": after}
        self._pending = {p["id"]: p for p in pages if p.get("id") and p.get("access_token")}
        return [{"id": p["id"], "name": p.get("name", p["id"]),
                 "can_post": "CREATE_CONTENT" in (p.get("tasks") or ["CREATE_CONTENT"])}
                for p in self._pending.values()]

    def choose_page(self, page_id: str) -> str:
        """Store the picked Page (from the last ``fetch_pages``). Returns its name."""
        p = self._pending.get(page_id)
        if not p:
            raise cm.PublishError("Không thấy Trang này trong danh sách vừa lấy — Kết nối lại.")
        old = cm.load_account(PLATFORM) or {}
        acc = {"page_id": p["id"], "page_name": p.get("name", p["id"]),
               "page_token": p["access_token"], "connected_at": time.time(),
               "reels": old.get("reels", []) if old.get("page_id") == p["id"] else []}
        cm.save_account(PLATFORM, acc)
        self._pending = {}
        return acc["page_name"]

    def disconnect(self) -> None:
        """Forget the Page token. (The app's access is removed for good in Facebook →
        Cài đặt → Tiện ích tích hợp doanh nghiệp.)"""
        self._pending = {}
        cm.forget_account(PLATFORM)

    # --- helpers ----------------------------------------------------------------------------
    def _fail(self, data: dict, doing: str, *, reel: bool = False):
        if _err(data).get("code") == 190:
            cm.mark_revoked(PLATFORM, "190")
            raise cm.AuthExpired(RECONNECT_MSG)
        raise cm.PublishError(explain(data, doing, reel=reel))

    @staticmethod
    def _desc(meta: PostMeta) -> str:
        """Description + hashtags (the title is its own field for Page videos)."""
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in meta.tags if t.strip())
        d = meta.description.strip()
        tags = " ".join(t for t in tags.split() if t.lower() not in d.lower())
        return "\n\n".join(x for x in (d, tags) if x)

    # --- Reels ------------------------------------------------------------------------------
    def _reel_offset(self, c: httpx.Client, auth: dict, vid: str, fallback: int) -> int:
        try:
            r = c.get(f"{GRAPH}/{vid}", params={"fields": "status"}, headers=auth)
            st = (cm.json_or_empty(r).get("status") or {}).get("uploading_phase") or {}
            return int(st.get("bytes_transfered", fallback))
        except (httpx.TransportError, ValueError, TypeError):
            return fallback

    def _reel(self, c: httpx.Client, acc: dict, video: Path, meta: PostMeta, when: int | None,
              progress) -> PostResult:
        t0 = time.time()
        recent = [t for t in acc.get("reels", []) if t > t0 - 86400]
        if len(recent) >= REELS_PER_DAY:
            raise cm.PublishError(REELS_LIMIT_MSG)
        if when and when - t0 > REEL_SCHEDULE_MAX:
            raise cm.PublishError("Facebook chỉ cho hẹn giờ Reel trong vòng 29 ngày.")
        dur = cm.probe_seconds(video)
        if dur and not (REEL_SECONDS[0] <= dur <= REEL_SECONDS[1]):
            raise cm.PublishError(f"Reels trên Facebook phải dài 3–90 giây (video này {dur:.0f} "
                                  "giây) — đăng dạng video thường, hoặc cắt Short ngắn hơn.")
        page = acc["page_id"]
        auth = {"Authorization": f"OAuth {acc['page_token']}"}
        r = c.post(f"{GRAPH}/{page}/video_reels", data={"upload_phase": "start"}, headers=auth)
        d = cm.json_or_empty(r)
        if r.status_code != 200 or not d.get("video_id"):
            self._fail(d, "mở phiên đăng Reel", reel=True)
        vid = str(d["video_id"])
        up = d.get("upload_url") or f"{RUPLOAD}/{vid}"
        size = video.stat().st_size
        offset, fails, last = 0, 0, None
        while True:
            try:
                r = c.post(up, content=cm.file_chunks(video, offset, size - offset),
                           headers={**auth, "offset": str(offset), "file_size": str(size),
                                    "Content-Type": "application/octet-stream",
                                    "Content-Length": str(size - offset)})
                body = cm.json_or_empty(r)
                if r.status_code == 200 and body.get("success"):
                    break
                if r.status_code < 500 and r.status_code != 429:
                    self._fail(body, "tải Reel lên", reel=True)
            except httpx.TransportError as e:
                last = e
            fails += 1
            if fails > MAX_FAILS:
                if last is not None:
                    raise last
                raise cm.PublishError("Máy chủ tải lên của Facebook lỗi liên tục — thử lại sau.")
            cm.backoff(fails)
            offset = self._reel_offset(c, auth, vid, offset)
        if progress:
            progress(0.9)
        finish = {"upload_phase": "finish", "video_id": vid,
                  "video_state": "SCHEDULED" if when else "PUBLISHED",
                  "description": cm.caption(meta), "title": meta.title.strip()[:255],
                  "is_ai_generated": "true" if AI_GENERATED else "false"}
        if when:
            finish["scheduled_publish_time"] = str(when)
        r = c.post(f"{GRAPH}/{page}/video_reels", data=finish, headers=auth)
        d = cm.json_or_empty(r)
        if r.status_code != 200 or not d.get("success", True):
            self._fail(d, "đăng Reel", reel=True)
        acc["reels"] = [*recent, t0]
        cm.save_account(PLATFORM, acc)
        url = f"https://www.facebook.com/reel/{vid}"
        if when:
            at = time.strftime("%H:%M %d/%m/%Y", time.localtime(when))
            return PostResult(True, "scheduled", url=url, remote_id=vid,
                              message=f"Đã hẹn giờ Reel trên “{acc['page_name']}” lúc {at}")
        return PostResult(True, "published", url=url, remote_id=vid,
                          message=f"Đã đăng Reel lên “{acc['page_name']}”: {url}")

    # --- Page video -------------------------------------------------------------------------
    def _long(self, c: httpx.Client, acc: dict, video: Path, meta: PostMeta, when: int | None,
              progress) -> PostResult:
        if when and when - time.time() > VIDEO_SCHEDULE_MAX:
            raise cm.PublishError("Facebook chỉ cho hẹn giờ video trong vòng 6 tháng.")
        page = acc["page_id"]
        auth = {"Authorization": f"OAuth {acc['page_token']}"}
        size = video.stat().st_size
        url = f"{GRAPH_VIDEO}/{page}/videos"
        r = c.post(url, data={"upload_phase": "start", "file_size": str(size)}, headers=auth)
        d = cm.json_or_empty(r)
        if r.status_code != 200 or not d.get("upload_session_id"):
            self._fail(d, "mở phiên tải video")
        sess, vid = str(d["upload_session_id"]), str(d.get("video_id", ""))
        start, end = int(d.get("start_offset", 0)), int(d.get("end_offset", size))
        fails, last = 0, None
        while start < end:
            try:
                r = c.post(url, headers=auth,
                           data={"upload_phase": "transfer", "upload_session_id": sess,
                                 "start_offset": str(start)},
                           files={"video_file_chunk": ("chunk", cm.read_range(video, start, end - start),
                                                       "application/octet-stream")})
                d = cm.json_or_empty(r)
                if r.status_code == 200 and "start_offset" in d:
                    start, end, fails = int(d["start_offset"]), int(d["end_offset"]), 0
                    if progress:
                        progress(min(0.95, start / size))
                    continue
                if r.status_code < 500 and not _err(d).get("is_transient"):
                    self._fail(d, "tải video lên")
            except httpx.TransportError as e:
                last = e
            fails += 1
            if fails > MAX_FAILS:
                if last is not None:
                    raise last
                raise cm.PublishError("Máy chủ video của Facebook lỗi liên tục — thử lại sau.")
            cm.backoff(fails)
        finish = {"upload_phase": "finish", "upload_session_id": sess,
                  "title": meta.title.strip()[:255], "description": self._desc(meta)}
        if when:
            finish |= {"published": "false", "scheduled_publish_time": str(when)}
        r = c.post(url, data=finish, headers=auth)
        d = cm.json_or_empty(r)
        if r.status_code != 200 or not d.get("success", True):
            self._fail(d, "hoàn tất video")
        link = f"https://www.facebook.com/{page}/videos/{vid}" if vid else ""
        if when:
            at = time.strftime("%H:%M %d/%m/%Y", time.localtime(when))
            return PostResult(True, "scheduled", url=link, remote_id=vid,
                              message=f"Đã hẹn giờ video trên “{acc['page_name']}” lúc {at}")
        return PostResult(True, "published", url=link, remote_id=vid,
                          message=f"Đã đăng video lên “{acc['page_name']}”: {link}")

    # --- publish ----------------------------------------------------------------------------
    def publish(self, video, meta: PostMeta, *, progress=None) -> PostResult:
        video = Path(video)
        if not video.is_file():
            return PostResult(False, "failed", message=f"Không thấy tệp video: {video}")
        acc = cm.load_account(PLATFORM)
        if not acc or not self.connected():
            return PostResult(False, "failed", message="Chưa kết nối Trang Facebook — Cài đặt → "
                                                       "Đăng video → Kết nối.")
        when = None
        if meta.publish_at and meta.publish_at - time.time() >= SCHEDULE_MIN_AHEAD:
            when = int(meta.publish_at)
        try:
            with cm.client(self.transport) as c:
                fn = self._reel if meta.kind == "short" else self._long
                res = fn(c, acc, video, meta, when, progress)
        except cm.PublishError as e:
            return PostResult(False, "failed", message=str(e))
        if progress:
            progress(1.0)
        return res
