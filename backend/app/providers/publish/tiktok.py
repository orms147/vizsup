"""TikTok — Login Kit (desktop) + Content Posting API with the owner's own TikTok developer
app (setup in docs/plan/J-publish-setup.md).

Linking (once, "Kết nối" in Settings): the system browser opens TikTok's consent page; the
redirect goes to ``http://127.0.0.1:<random port>/callback/`` (register
``http://127.0.0.1:*/callback/`` in the app's Login Kit settings — desktop apps may use only
localhost / 127.0.0.1, a port is required, ``*`` matches any port); PKCE S256 where the
challenge is the SHA-256 HEX digest (TikTok's desktop rule, not base64url). The access token
(24 h) and refresh token (365 days, may be replaced on refresh) go to
``<storage>/_accounts/tiktok.json``.

Posting: ``/post/publish/creator_info/query/`` (the creator's privacy options, max length) →
``/post/publish/video/init/`` with FILE_UPLOAD → PUT the chunks (Content-Range) → poll
``/post/publish/status/fetch/``. Privacy: PUBLIC_TO_EVERYONE when the account offers it, else
SELF_ONLY. An UNAUDITED app may post only to a PRIVATE account and only as SELF_ONLY: a public
account gets ``unaudited_client_can_only_post_to_private_accounts`` at init → vizsup sends the
video to the creator's TikTok INBOX instead (``/post/publish/inbox/video/init/``, scope
video.upload; the owner finishes the post in the app) → ``status="draft"``; a SELF_ONLY post on
a private account → ``status="private"`` with the audit explained. The API cannot schedule:
a ``publish_at`` more than 15 minutes ahead is refused (the post queue calls at the slot).

Facts checked on the web 2026-10-31:
- Login Kit for desktop, https://developers.tiktok.com/doc/login-kit-desktop/ — redirect
  host only localhost / 127.0.0.1, port required, ``*`` wildcard port, static URI, registered;
  "You must use hex encoding of SHA256 to generate the code challenge"; S256 only.
- Tokens, https://developers.tiktok.com/doc/oauth-user-access-token-management —
  POST /v2/oauth/token/ (form), code_verifier "Required for mobile and desktop app"; access
  24 h, refresh 365 days; a refresh may return a new refresh token; revoke /v2/oauth/revoke/.
- Direct Post, https://developers.tiktok.com/doc/content-posting-api-reference-direct-post —
  scope video.publish, 6 requests/min per token, title ≤ 2200 UTF-16 runes, privacy_level must
  be one of creator_info's options, is_aigc label; errors unaudited_client_can_only_post_to_
  private_accounts, spam_risk_too_many_posts, privacy_level_option_mismatch, reached_active_
  user_cap …; no scheduling field.
- Media transfer, https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide —
  chunks 5–64 MB, last chunk up to 128 MB, < 5 MB as one chunk, total_chunk_count =
  floor(size / chunk_size), ≤ 1000 chunks, sequential; 206 = more, 201 = complete.
- Sharing guidelines, https://developers.tiktok.com/doc/content-sharing-guidelines —
  unaudited: ≤ 5 posting users / 24 h, account must be private at posting time, SELF_ONLY
  only; ~15 posts / day / creator via Direct Post; "Not acceptable: An app that copies
  arbitrary contents from other platforms to TikTok".
- Upload (inbox), https://developers.tiktok.com/doc/content-posting-api-reference-upload-video
  — scope video.upload, at most 5 pending shares per 24 h; no unaudited restriction stated.
"""
from __future__ import annotations

import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.providers.base import PostMeta, PostResult, PublishProvider
from app.providers.publish import _common as cm

PLATFORM = "tiktok"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
API = "https://open.tiktokapis.com/v2"
SCOPES = ("user.info.basic", "video.publish", "video.upload")
CALLBACK = "/callback/"
CHUNK = 10_000_000            # within 5 MiB … 64 MB whichever way "MB" is read
MAX_CHUNK = 64_000_000
MAX_CHUNKS = 1000
CAPTION_MAX = 2200
MAX_FAILS = 5
STATUS_POLL = 5.0
STATUS_WAIT = 300.0
SCHEDULE_GRACE = 15 * 60
AI_GENERATED = True           # is_aigc: the voice-over is an AI voice

PRIVATE_MSG = ("Đã đăng lên TikTok ở chế độ “Chỉ mình tôi”: app TikTok của bạn chưa qua kiểm "
               "duyệt (audit) nên chỉ đăng được như vậy, lên tài khoản đang riêng tư. Muốn công "
               "khai: chuyển tài khoản sang công khai rồi đổi quyền xem video thành “Mọi người” "
               "trong app TikTok.")
INBOX_MSG = ("App TikTok chưa qua kiểm duyệt (audit) nên không đăng thẳng lên tài khoản công khai "
             "được — vizsup đã gửi video vào HỘP THƯ TikTok của bạn: mở app TikTok, bấm thông báo "
             "để chỉnh và đăng.")
DRAFT_MSG = ("Đã gửi video vào hộp thư TikTok của bạn — mở app TikTok, bấm thông báo để chỉnh và "
             "đăng.")
AUDIT_MSG = ("App TikTok chưa qua kiểm duyệt (audit): TikTok chỉ cho đăng thẳng lên tài khoản đang "
             "để RIÊNG TƯ, ở chế độ “Chỉ mình tôi”. Chuyển tài khoản sang riêng tư, hoặc Kết nối "
             "lại và cho phép quyền gửi bản nháp (video.upload), hoặc xin audit "
             "(docs/plan/J-publish-setup.md).")
RECONNECT_MSG = "Kết nối TikTok đã hết hạn hoặc bị gỡ — Cài đặt → Đăng video → Kết nối lại."

_ERRORS = {
    "spam_risk_too_many_posts": "TikTok: tài khoản đã đăng đủ số bài cho phép trong 24 giờ "
                                "(thường ~15 bài/ngày qua API) — đăng lại sau.",
    "spam_risk_user_banned_from_posting": "TikTok đang chặn tài khoản này đăng bài.",
    "reached_active_user_cap": "App TikTok đã chạm giới hạn số người đăng trong ngày (app chưa "
                               "audit: 5 người / 24 giờ) — thử lại ngày mai.",
    "privacy_level_option_mismatch": "Quyền xem video không nằm trong các lựa chọn TikTok cho "
                                     "tài khoản này — Kết nối lại rồi thử lại.",
    "scope_not_authorized": "Thiếu quyền video.publish / video.upload — Kết nối lại và bật đủ quyền.",
    "spam_risk_too_many_pending_share": "Đã có 5 video chờ trong hộp thư TikTok trong 24 giờ — "
                                        "mở app TikTok đăng hoặc xóa bớt rồi thử lại.",
    "rate_limit_exceeded": "Gọi TikTok quá nhanh (giới hạn mỗi phút) — thử lại sau ít phút.",
    "access_token_invalid": RECONNECT_MSG,
}
_FAIL_REASONS = {
    "file_format_check_failed": "định dạng tệp không hợp lệ",
    "duration_check_failed": "độ dài video không hợp lệ",
    "frame_rate_check_failed": "số khung hình/giây không hợp lệ",
    "picture_size_check_failed": "kích thước khung hình không hợp lệ",
    "spam_risk_too_many_posts": "đã đăng đủ số bài trong ngày",
    "spam_risk_user_banned_from_posting": "tài khoản đang bị chặn đăng",
    "spam_risk_text": "chú thích bị coi là spam",
    "spam_risk": "bị coi là spam",
    "auth_removed": "bạn đã gỡ quyền của app",
    "publish_cancelled": "bài đăng đã bị hủy",
    "internal": "lỗi nội bộ của TikTok (thử lại)",
}


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per the media transfer rules; the remainder rides
    on the last chunk (< 2 × chunk ≤ 128 MB)."""
    if size <= CHUNK:
        return size, 1
    chunk = CHUNK
    if size // chunk > MAX_CHUNKS:
        chunk = min(MAX_CHUNK, -(-size // MAX_CHUNKS))
    return chunk, size // chunk


def explain(code: str, message: str = "") -> str:
    return _ERRORS.get(code) or f"TikTok từ chối ({code}): {message}".rstrip(": ")


class TikTokPublisher(PublishProvider):
    name = PLATFORM
    display_name = "TikTok"
    cost_note = "miễn phí · app chưa audit: chỉ “Chỉ mình tôi” hoặc gửi vào hộp thư nháp"
    unavailable_note = "cần Client key / Client secret (Cài đặt → Đăng video)"
    kinds = ("long", "short")
    can_schedule = False                # the Content Posting API has no schedule field
    auth_kind = "loopback"
    transport: httpx.BaseTransport | None = None

    # --- account ----------------------------------------------------------------------------
    def available(self) -> bool:
        return bool(settings.tiktok_client_key.strip() and settings.tiktok_client_secret.strip())

    def connected(self) -> bool:
        acc = cm.load_account(PLATFORM)
        if not acc or not acc.get("refresh_token") or acc.get("revoked"):
            return False
        return acc.get("refresh_expires_at", 0) > time.time()

    def account_label(self) -> str:
        acc = cm.load_account(PLATFORM) if self.connected() else None
        return (acc or {}).get("display_name", "")

    def account_hint(self) -> str:
        acc = cm.load_account(PLATFORM) or {}
        if acc.get("revoked"):
            return "Kết nối cũ đã hết hạn hoặc bị gỡ — bấm Kết nối lại."
        exp = acc.get("refresh_expires_at", 0)
        if self.connected() and exp - time.time() < 14 * 86400:
            return f"TikTok giữ kết nối tới {time.strftime('%d/%m/%Y', time.localtime(exp))} — " \
                   "nối lại trước ngày đó."
        return ""

    def connect(self, *, open_browser=webbrowser.open, timeout: float = 300.0,
                cancel=None) -> str:
        if not self.available():
            raise cm.PublishError("Chưa nhập Client key / Client secret của TikTok.")
        verifier, challenge = cm.pkce_pair(hex_challenge=True)

        def url_for(redirect_uri: str, state: str) -> str:
            return AUTH_URL + "?" + urlencode({
                "client_key": settings.tiktok_client_key.strip(), "response_type": "code",
                "scope": ",".join(SCOPES), "redirect_uri": redirect_uri, "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256"})

        code, redirect_uri = cm.run_loopback(url_for, path=CALLBACK, open_browser=open_browser,
                                             timeout=timeout, cancel=cancel)
        with cm.client(self.transport) as c:
            r = c.post(TOKEN_URL, data={
                "client_key": settings.tiktok_client_key.strip(),
                "client_secret": settings.tiktok_client_secret.strip(), "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect_uri,
                "code_verifier": verifier})
            tok = cm.json_or_empty(r)
            if r.status_code != 200 or not tok.get("access_token"):
                why = tok.get("error_description") or tok.get("error") or r.status_code
                raise cm.PublishError(f"TikTok từ chối mã đăng nhập: {why}")
            acc = self._account_from(tok)
            acc["connected_at"] = time.time()
            r = c.get(f"{API}/user/info/", params={"fields": "open_id,display_name"},
                      headers=self._auth(acc["access_token"]))
            user = ((cm.json_or_empty(r).get("data") or {}).get("user") or {}) if r.status_code == 200 else {}
            acc["display_name"] = user.get("display_name", "")
            if "video.publish" in acc["scope"]:
                info, err, _msg = self._call(c, acc, "/post/publish/creator_info/query/", {})
                if not err:
                    acc["username"] = info.get("creator_username", "")
                    acc["display_name"] = acc["display_name"] or info.get("creator_nickname", "")
            acc["display_name"] = acc["display_name"] or "tài khoản TikTok"
        cm.save_account(PLATFORM, acc)
        return acc["display_name"]

    def disconnect(self) -> None:
        acc = cm.load_account(PLATFORM) or {}
        if acc.get("access_token") and self.available():
            try:
                with cm.client(self.transport) as c:
                    c.post(REVOKE_URL, timeout=10.0, data={
                        "client_key": settings.tiktok_client_key.strip(),
                        "client_secret": settings.tiktok_client_secret.strip(),
                        "token": acc["access_token"]})
            except httpx.HTTPError:
                pass
        cm.forget_account(PLATFORM)

    # --- tokens / API -----------------------------------------------------------------------
    @staticmethod
    def _auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _account_from(tok: dict, old: dict | None = None) -> dict:
        t0 = time.time()
        acc = dict(old or {})
        acc.update({"access_token": tok["access_token"],
                    "expires_at": t0 + float(tok.get("expires_in", 86400)),
                    "refresh_token": tok.get("refresh_token") or acc.get("refresh_token", ""),
                    "scope": tok.get("scope", acc.get("scope", "")),
                    "open_id": tok.get("open_id", acc.get("open_id", ""))})
        if tok.get("refresh_expires_in"):
            acc["refresh_expires_at"] = t0 + float(tok["refresh_expires_in"])
        acc.pop("revoked", None)
        return acc

    def _token(self, c: httpx.Client, acc: dict, *, force: bool = False) -> str:
        if not force and acc.get("expires_at", 0) > time.time() + 120:
            return acc["access_token"]
        r = c.post(TOKEN_URL, data={
            "client_key": settings.tiktok_client_key.strip(),
            "client_secret": settings.tiktok_client_secret.strip(),
            "grant_type": "refresh_token", "refresh_token": acc.get("refresh_token", "")})
        tok = cm.json_or_empty(r)
        if r.status_code != 200 or not tok.get("access_token"):
            if tok.get("error") in ("invalid_grant", "invalid_request", "access_token_invalid") \
                    or r.status_code in (400, 401):
                cm.mark_revoked(PLATFORM, str(tok.get("error", r.status_code)))
                raise cm.AuthExpired(RECONNECT_MSG)
            raise cm.PublishError(f"Không làm mới được quyền TikTok: {tok.get('error') or r.status_code}")
        acc.update(self._account_from(tok, acc))
        cm.save_account(PLATFORM, acc)
        return acc["access_token"]

    def _call(self, c: httpx.Client, acc: dict, path: str, body: dict) -> tuple[dict, str, str]:
        """POST an API call → (data, error code or "", message). A rejected access token is
        refreshed once."""
        for attempt in (0, 1):
            r = c.post(f"{API}{path}", json=body,
                       headers={**self._auth(self._token(c, acc, force=attempt > 0)),
                                "Content-Type": "application/json; charset=UTF-8"})
            d = cm.json_or_empty(r)
            err = d.get("error") or {}
            code = err.get("code", "") if isinstance(err, dict) else str(err)
            code = "" if code == "ok" else code
            if not code and r.status_code >= 400:
                code = f"http_{r.status_code}"
            if code == "access_token_invalid" and attempt == 0:
                continue
            return d.get("data") or {}, code, (err.get("message", "") if isinstance(err, dict) else "")
        return {}, "access_token_invalid", ""

    # --- upload -----------------------------------------------------------------------------
    def _put_chunks(self, c: httpx.Client, url: str, video: Path, chunk: int, count: int,
                    progress) -> None:
        size = video.stat().st_size
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            fails, last = 0, None
            while True:
                try:
                    r = c.put(url, content=cm.read_range(video, start, end - start + 1),
                              headers={"Content-Type": "video/mp4",
                                       "Content-Range": f"bytes {start}-{end}/{size}"})
                except httpx.TransportError as e:
                    r, last = None, e
                if r is not None and r.status_code in (200, 201, 206):
                    break
                if r is not None and r.status_code == 403:
                    raise cm.PublishError("Link tải lên TikTok đã hết hạn — thử đăng lại.")
                if r is not None and r.status_code < 500:
                    raise cm.PublishError(f"TikTok không nhận phần video ({r.status_code}).")
                fails += 1
                if fails > MAX_FAILS:
                    if last is not None:
                        raise last
                    raise cm.PublishError("Máy chủ tải lên của TikTok lỗi liên tục — thử lại sau.")
                cm.backoff(fails)
            if progress:
                progress(min(0.9, (end + 1) / size * 0.9))

    def _poll(self, c: httpx.Client, acc: dict, publish_id: str) -> dict:
        data: dict = {}
        for _ in range(int(STATUS_WAIT / STATUS_POLL) + 1):
            data, code, msg = self._call(c, acc, "/post/publish/status/fetch/",
                                         {"publish_id": publish_id})
            if code:
                return {"status": "UNKNOWN", "error": explain(code, msg)}
            if data.get("status") in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"):
                return data
            cm._sleep(STATUS_POLL)
        return data

    def _send(self, c: httpx.Client, acc: dict, path: str, body: dict, video: Path,
              chunk: int, count: int, progress) -> tuple[str, dict, str, str]:
        """init → upload → status. (publish_id, final status data, init error, message)."""
        data, code, msg = self._call(c, acc, path, body)
        if code:
            return "", {}, code, msg
        pid, url = data.get("publish_id", ""), data.get("upload_url", "")
        if not pid or not url:
            return "", {}, "no_upload_url", "TikTok không trả link tải lên"
        self._put_chunks(c, url, video, chunk, count, progress)
        return pid, self._poll(c, acc, pid), "", ""

    # --- publish ----------------------------------------------------------------------------
    def publish(self, video, meta: PostMeta, *, progress=None) -> PostResult:
        video = Path(video)
        if not video.is_file():
            return PostResult(False, "failed", message=f"Không thấy tệp video: {video}")
        acc = cm.load_account(PLATFORM)
        if not self.available() or not acc or not self.connected():
            return PostResult(False, "failed", message="Chưa kết nối TikTok — Cài đặt → Đăng "
                                                       "video → Kết nối.")
        if meta.publish_at and meta.publish_at - time.time() > SCHEDULE_GRACE:
            return PostResult(False, "failed", message="TikTok không cho hẹn giờ qua API — hàng "
                                                       "đăng cần gọi đúng giờ đăng.")
        size = video.stat().st_size
        chunk, count = chunk_plan(size)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                  "total_chunk_count": count}
        scopes = set(acc.get("scope", "").replace(" ", ",").split(","))
        try:
            with cm.client(self.transport) as c:
                if "video.publish" in scopes:
                    res = self._direct(c, acc, video, meta, source, scopes, progress)
                elif "video.upload" in scopes:
                    res = self._inbox(c, acc, video, source, progress, DRAFT_MSG)
                else:
                    res = PostResult(False, "failed", message=_ERRORS["scope_not_authorized"])
        except cm.PublishError as e:
            return PostResult(False, "failed", message=str(e))
        if progress and res.ok:
            progress(1.0)
        return res

    def _direct(self, c, acc, video: Path, meta: PostMeta, source: dict, scopes: set,
                progress) -> PostResult:
        info, code, msg = self._call(c, acc, "/post/publish/creator_info/query/", {})
        if code:
            return PostResult(False, "failed", message=explain(code, msg))
        options = info.get("privacy_level_options") or []
        limit = float(info.get("max_video_post_duration_sec") or 0)
        dur = cm.probe_seconds(video)
        if limit and dur and dur > limit + 0.5:
            return PostResult(False, "failed", message=f"Tài khoản TikTok này chỉ đăng được video "
                                                       f"tối đa {limit:.0f} giây (video dài "
                                                       f"{dur:.0f} giây).")
        privacy = next((p for p in ("PUBLIC_TO_EVERYONE", "SELF_ONLY") if p in options), "")
        if not privacy:
            return PostResult(False, "failed", message="TikTok không cho chọn quyền xem công khai "
                                                       f"hay riêng tư ({', '.join(options)}).")
        post_info = {"title": cm.cut_utf16(cm.caption(meta), CAPTION_MAX), "privacy_level": privacy,
                     "disable_duet": bool(info.get("duet_disabled")),
                     "disable_stitch": bool(info.get("stitch_disabled")),
                     "disable_comment": bool(info.get("comment_disabled")),
                     "brand_content_toggle": False, "brand_organic_toggle": False,
                     "is_aigc": AI_GENERATED}
        pid, st, code, msg = self._send(c, acc, "/post/publish/video/init/",
                                        {"post_info": post_info, "source_info": source},
                                        video, source["chunk_size"], source["total_chunk_count"],
                                        progress)
        if code == "unaudited_client_can_only_post_to_private_accounts":
            if "video.upload" in scopes:
                return self._inbox(c, acc, video, source, progress, INBOX_MSG)
            return PostResult(False, "failed", message=AUDIT_MSG)
        if code:
            return PostResult(False, "failed", message=explain(code, msg))
        status = st.get("status", "")
        if status == "FAILED":
            why = _FAIL_REASONS.get(st.get("fail_reason", ""), st.get("fail_reason", ""))
            return PostResult(False, "failed", remote_id=pid, message=f"TikTok không nhận video: {why}")
        ids = st.get("publicaly_available_post_id") or []
        url = (f"https://www.tiktok.com/@{acc['username']}/video/{ids[0]}"
               if ids and acc.get("username") else "")
        if privacy == "SELF_ONLY":
            return PostResult(True, "private", url=url, remote_id=pid, message=PRIVATE_MSG)
        note = "" if status == "PUBLISH_COMPLETE" else " (TikTok vẫn đang xử lý — xem trong app)"
        return PostResult(True, "published", url=url, remote_id=pid,
                          message=f"Đã đăng lên TikTok{note}" + (f": {url}" if url else ""))

    def _inbox(self, c, acc, video: Path, source: dict, progress, message: str) -> PostResult:
        pid, st, code, msg = self._send(c, acc, "/post/publish/inbox/video/init/",
                                        {"source_info": source}, video, source["chunk_size"],
                                        source["total_chunk_count"], progress)
        if code:
            return PostResult(False, "failed", message=explain(code, msg))
        if st.get("status") == "FAILED":
            why = _FAIL_REASONS.get(st.get("fail_reason", ""), st.get("fail_reason", ""))
            return PostResult(False, "failed", remote_id=pid, message=f"TikTok không nhận video: {why}")
        return PostResult(True, "draft", remote_id=pid, message=message)
