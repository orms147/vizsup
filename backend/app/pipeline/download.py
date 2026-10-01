"""Stage: download. yt-dlp (Python API), domain-routed, cookie-aware.

Scope (v1): Douyin + Bilibili. Douyin is cookie-fragile and breaks even with
fresh cookies (extractor-side) — supply cookies and expect occasional failures;
fallbacks (BBDown / TikTokDownloader) are a later addition.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import settings
from app.models import Job

SUPPORTED = ("douyin.com", "iesdouyin.com", "bilibili.com", "b23.tv")


def platform_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "bilibili" in host or "b23.tv" in host:
        return "bilibili"
    if "douyin" in host:
        return "douyin"
    return "unknown"


def normalize_url(url: str) -> str:
    """Rewrite Douyin feed/modal URLs (which yt-dlp rejects) to the canonical
    video URL. e.g. douyin.com/jingxuan?modal_id=<id> → douyin.com/video/<id>.
    """
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if "douyin.com" in host and "/video/" not in u.path:
        modal_id = parse_qs(u.query).get("modal_id", [None])[0]
        if modal_id and modal_id.isdigit():
            return f"https://www.douyin.com/video/{modal_id}"
    return url


def _cookie_opts() -> dict:
    """A cookies.txt file WINS over browser extraction — they are not combined. With
    both set, yt-dlp still tried the browser first and died on it (Edge/Chrome lock and
    app-bound-encrypt their cookie DB on Windows), so a perfectly good file was never
    used."""
    if settings.ytdlp_cookies_file:
        return {"cookiefile": settings.ytdlp_cookies_file}
    if settings.ytdlp_cookies_from_browser:
        return {"cookiesfrombrowser": (settings.ytdlp_cookies_from_browser,)}
    return {}


_CHROMIUM = ("edge", "chrome", "chromium", "brave", "opera", "vivaldi")
_COOKIE_HELP = (
    "Cách chắc chắn: xuất cookie ra tệp. Trong Edge/Chrome cài tiện ích \"Get cookies.txt "
    "LOCALLY\", mở douyin.com (không cần đăng nhập), bấm tiện ích → Export → lưu tệp, rồi "
    "vào ⚙ Cài đặt → \"…hoặc tệp cookies.txt\" chọn tệp đó (hoặc .env: YTDLP_COOKIES_FILE="
    "<đường dẫn>). Tệp cookie luôn được ưu tiên hơn trình duyệt. Cookie Douyin hết hạn sau "
    "một thời gian — lỗi này quay lại thì xuất lại tệp."
)


def _cookie_error(exc: Exception) -> RuntimeError | None:
    """Map yt-dlp's cookie failures to an actionable Vietnamese message (or None)."""
    m = str(exc).lower()
    browser = (settings.ytdlp_cookies_from_browser or "").lower()
    if "could not copy" in m and "cookie database" in m:
        return RuntimeError(
            f"Không đọc được cookie của trình duyệt '{browser}': trình duyệt đang mở nên khóa "
            f"tệp cookie. Trên Windows, Edge/Chrome bản mới còn MÃ HOÁ cookie (App-Bound) nên "
            f"yt-dlp không đọc được kể cả khi đã đóng hẳn trình duyệt. " + _COOKIE_HELP)
    if "failed to decrypt" in m or ("decrypt" in m and "cookie" in m):
        return RuntimeError(
            f"Không giải mã được cookie của '{browser}' (Edge/Chrome trên Windows dùng mã hoá "
            f"App-Bound mà yt-dlp không hỗ trợ). " + _COOKIE_HELP)
    if "cookie" in m:
        why = ("Cookie trong tệp đã hết hạn hoặc không phải của douyin.com." if
               settings.ytdlp_cookies_file else
               f"Chưa có cookie dùng được (trình duyệt '{browser}' không đọc được)." if
               browser in _CHROMIUM else "Chưa cấu hình cookie.")
        return RuntimeError(f"Douyin cần cookie mới để tải. {why} " + _COOKIE_HELP)
    return None


def _find_produced(job: Job) -> Path:
    """Locate the downloaded file and normalize it to the canonical source.mp4
    (every downstream stage hardcodes that name; ffmpeg reads by content)."""
    produced = job.source_video
    if not produced.exists():
        exts = {".mp4", ".mkv", ".webm", ".flv", ".mov"}
        candidates = [c for c in job.work_dir.iterdir()
                      if c.suffix.lower() in exts and not c.name.endswith(".part")]
        if not candidates:
            raise RuntimeError("downloader finished but no output file was found.")
        produced = max(candidates, key=lambda p: p.stat().st_mtime)
    if produced != job.source_video:
        produced.replace(job.source_video)
    return job.source_video


def _bbdown(job: Job, url: str) -> Job:
    """Bilibili fallback via BBDown (nilaoda/BBDown; `--work-dir` + `-F` verified
    2026-07-02). Only attempted when BBDOWN_PATH is configured."""
    import subprocess

    exe = settings.bbdown_path
    if not exe or not Path(exe).exists():
        raise RuntimeError(f"BBDown chưa cấu hình hoặc không tồn tại: {exe or '(trống)'}")
    subprocess.run([exe, url, "--work-dir", str(job.work_dir), "-F", "source"],
                   check=True, capture_output=True, text=True)
    _find_produced(job)
    job.metadata.setdefault("title", url)
    job.metadata["platform"] = "bilibili"
    job.metadata["downloader"] = "bbdown"
    return job


def download(job: Job) -> Job:
    """Download ``job.url`` into ``job.source_video``. yt-dlp is the baseline;
    Bilibili gets a BBDown fallback when configured (Douyin's fallback tool churns
    too fast to wire blind — still yt-dlp-only)."""
    if not job.url:
        raise ValueError("Job has no URL to download.")
    url = normalize_url(job.url)
    try:
        return _ytdlp(job, url)
    except Exception as exc:
        if platform_of(job.url) == "bilibili" and settings.bbdown_path:
            try:
                return _bbdown(job, url)
            except Exception as exc2:  # noqa: BLE001 - report BOTH failures
                raise RuntimeError(
                    f"yt-dlp thất bại ({exc}) và BBDown cũng thất bại ({exc2}).") from exc2
        raise


def _ytdlp(job: Job, url: str) -> Job:
    import yt_dlp

    outtmpl = str(job.work_dir / "source.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        **_cookie_opts(),
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 - give a fixable message for the cookie case
        friendly = _cookie_error(exc)
        if friendly is not None:
            raise friendly from exc
        raise

    # Locate the produced file (merge yields source.mp4; otherwise pick newest source.*).
    produced = job.work_dir / "source.mp4"
    if not produced.exists():
        candidates = sorted(job.work_dir.glob("source.*"), key=lambda p: p.stat().st_mtime)
        candidates = [c for c in candidates if c.suffix.lower() != ".part"]
        if not candidates:
            raise RuntimeError("yt-dlp finished but no output file was found.")
        produced = candidates[-1]

    # Every downstream stage hardcodes source.mp4 (ffmpeg reads by content, not
    # extension), so normalize the fallback (.webm/.mkv) to the canonical name.
    if produced != job.source_video:
        produced.replace(job.source_video)
        produced = job.source_video

    job.metadata = {
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "description": (info.get("description") or "")[:500],
        "platform": platform_of(job.url),
        "downloaded_file": produced.name,
        "downloader": "yt-dlp",
    }
    return job
