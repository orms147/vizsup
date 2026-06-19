"""Stage: download. yt-dlp (Python API), domain-routed, cookie-aware.

Scope (v1): Douyin + Bilibili. Douyin is cookie-fragile and breaks even with
fresh cookies (extractor-side) — supply cookies and expect occasional failures;
fallbacks (BBDown / TikTokDownloader) are a later addition.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

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
        info = ydl.extract_info(job.url, download=True)

    # Locate the produced file (merge yields source.mp4; otherwise pick newest source.*).
    produced = job.work_dir / "source.mp4"
    if not produced.exists():
        candidates = sorted(job.work_dir.glob("source.*"), key=lambda p: p.stat().st_mtime)
        candidates = [c for c in candidates if c.suffix.lower() != ".part"]
        if not candidates:
            raise RuntimeError("yt-dlp finished but no output file was found.")
        produced = candidates[-1]

    job.metadata = {
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "description": (info.get("description") or "")[:500],
        "platform": platform_of(job.url),
        "downloaded_file": produced.name,
    }
    return job
