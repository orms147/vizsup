"""Stage: download. yt-dlp (Python API), domain-routed, cookie-aware.

Scope (v1): Douyin + Bilibili. Douyin is cookie-fragile and breaks even with
fresh cookies (extractor-side) — supply cookies and expect occasional failures;
fallbacks (BBDown / TikTokDownloader) are a later addition.
"""
from __future__ import annotations

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
    opts: dict = {}
    if settings.ytdlp_cookies_file:
        opts["cookiefile"] = settings.ytdlp_cookies_file
    if settings.ytdlp_cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.ytdlp_cookies_from_browser,)
    return opts


def download(job: Job) -> Job:
    """Download ``job.url`` into ``job.source_video`` and record metadata."""
    import yt_dlp

    if not job.url:
        raise ValueError("Job has no URL to download.")
    url = normalize_url(job.url)

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

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

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
    }
    return job
