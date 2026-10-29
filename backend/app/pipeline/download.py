"""Stage: download. yt-dlp (Python API), domain-routed, cookie-aware.

Scope (v1): Douyin + Bilibili. Fallbacks: BBDown for Bilibili (when configured) and,
for Douyin, an embedded browser (app/douyin_capture.py): since Oct 2026 Douyin answers
yt-dlp's unsigned ``aweme/detail`` call with HTTP 403 whatever the cookies, so the page is
opened in QtWebEngine, which runs Douyin's own signing code, and the media URLs it plays
are fetched with ffmpeg.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
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


def download(job: Job, *, log=None) -> Job:
    """Download ``job.url`` into ``job.source_video``. yt-dlp is the baseline;
    Bilibili gets a BBDown fallback when configured, Douyin the embedded browser."""
    if not job.url:
        raise ValueError("Job has no URL to download.")
    url = normalize_url(job.url)
    try:
        return _ytdlp(job, url)
    except Exception as exc:
        if platform_of(job.url) == "douyin" and web_available():
            if log:
                log("yt-dlp bị Douyin chặn — tải qua trình duyệt nhúng…")
            try:
                job = _douyin_web(job, url)
            except Exception as exc2:  # noqa: BLE001 - report BOTH failures
                raise RuntimeError(f"yt-dlp thất bại ({exc}) và trình duyệt nhúng cũng không "
                                   f"tải được ({exc2}).") from exc2
            if log and job.metadata.get("suspect"):
                log(f"⚠ Kiểm tra lại video vừa tải: {job.metadata['suspect']} "
                    f"(“{job.metadata.get('title', '')[:40]}”).")
            return job
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
        **_source_fields(info, job.url),
    }
    return job


def _source_fields(info: dict, url: str | None) -> dict:
    """Who posted it and where — for crediting the source (F-1)."""
    return {k: v for k, v in {
        "uploader_id": info.get("uploader_id") or info.get("channel_id"),
        "channel": info.get("channel"),
        "webpage_url": info.get("webpage_url") or url,
        "upload_date": info.get("upload_date"),            # YYYYMMDD
        "video_id": info.get("id"),
    }.items() if v}


def probe(url: str) -> dict:
    """Title, duration and poster of a link WITHOUT downloading it (yt-dlp metadata only)
    — for the cost / time estimate before Start. Raises like download() does."""
    import yt_dlp

    url = normalize_url(url)
    opts = {"quiet": True, "noprogress": True, "noplaylist": True, "skip_download": True,
            "socket_timeout": 10, **_cookie_opts()}     # closing the app waits for it
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        if platform_of(url) == "douyin" and web_available():
            data = _capture(douyin_id(url) or "")       # the page itself knows the title
            dur = data.get("duration") or _streams_of(data["urls"][0],
                                                      data.get("user_agent") or "")["duration"]
            return {"title": data.get("title"), "duration": dur or None,
                    "uploader": data.get("uploader"), "platform": "douyin",
                    "webpage_url": url, "video_id": douyin_id(url)}
        friendly = _cookie_error(exc)
        if friendly is not None:
            raise friendly from exc
        raise
    return {"title": info.get("title"), "duration": info.get("duration"),
            "uploader": info.get("uploader"), "platform": platform_of(url),
            **_source_fields(info, url)}


# --- Douyin through the embedded browser ---------------------------------------------------
_REFERER = "https://www.douyin.com/"
CAPTURE_FRESH_S = 600      # signed media URLs stay valid for a while; reuse a recent capture


def douyin_id(url: str) -> str | None:
    """The numeric video id of a Douyin link (/video/<id>, ?modal_id=<id>, or a v.douyin.com
    short link, followed to its target). None when there is none."""
    url = normalize_url(url)
    m = re.search(r"/(?:video|note)/(\d{8,})", url)
    if m:
        return m.group(1)
    if "v.douyin.com" in url or "iesdouyin.com" in url:
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                final = r.geturl()
        except Exception:  # noqa: BLE001
            return None
        m = re.search(r"/(?:video|note)/(\d{8,})", final)
        return m.group(1) if m else None
    return None


def web_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("PySide6.QtWebEngineCore") is not None
    except Exception:  # noqa: BLE001
        return False


def _browser_dir() -> Path:
    d = Path(settings.vizsup_storage_dir) / "_browser" / "douyin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _capture(video_id: str) -> dict:
    """Run app.douyin_capture in its own process (QtWebEngine needs the main thread) and
    return what the page played: {urls, user_agent, title, uploader, duration}."""
    if not video_id:
        raise RuntimeError("Không đọc được mã video trong link Douyin.")
    d = _browser_dir()
    out = d / f"{video_id}.json"
    if out.exists() and time.time() - out.stat().st_mtime < CAPTURE_FRESH_S:
        data = json.loads(out.read_text(encoding="utf-8"))
        if data.get("urls"):
            return data
    r = subprocess.run([sys.executable, "-m", "app.douyin_capture", video_id, str(out),
                        str(d / "profile")], capture_output=True, text=True, timeout=150,
                       encoding="utf-8", errors="replace")
    if not out.exists():
        raise RuntimeError(f"Trình duyệt nhúng không chạy được (mã {r.returncode}).")
    data = json.loads(out.read_text(encoding="utf-8"))
    if not data.get("urls"):
        raise RuntimeError("Trang Douyin không phát video trong trình duyệt nhúng (video bị "
                           "xóa / riêng tư, hoặc Douyin đòi xác minh).")
    return data


def capture_problem(data: dict, video_id: str) -> str | None:
    """Why a capture may not be the video asked for (None = looks right). 2026-10-28: the
    page played a 剑网3 ad instead of 《末日校车》09 — the ad had no author, and the next
    morning the same id gave the real episode. Pure."""
    href = str(data.get("href") or "")
    if href and video_id and video_id not in href:
        return "trang Douyin đã chuyển sang một video khác"
    if not str(data.get("uploader") or "").strip():
        return "không thấy người đăng — có thể Douyin phát quảng cáo thay cho video"
    return None


def _capture_checked(video_id: str) -> dict:
    """``_capture`` + one fresh retry when the page looks like another video. A page that
    moved to another video stops the run; a missing author only marks it ``suspect``."""
    data = _capture(video_id)
    if capture_problem(data, video_id) is None:
        return data
    (_browser_dir() / f"{video_id}.json").unlink(missing_ok=True)   # never reuse it
    data = _capture(video_id)
    problem = capture_problem(data, video_id)
    if problem and "chuyển" in problem:
        raise RuntimeError(f"Douyin mở một video khác thay vì {video_id} ({problem}) — "
                           "thử lại sau ít phút.")
    if problem:
        data["suspect"] = problem
    return data


def _http_args(ua: str) -> list[str]:
    return ["-user_agent", ua, "-headers", f"Referer: {_REFERER}\r\n"]


# A CDN stream that stops sending kept ffmpeg waiting for good (2026-10-28: 6 MB in, then
# nothing for minutes): give up after STALL_S without data — reconnecting first — so the
# run fails with a network error and is retried with a fresh link.
STALL_S = 20


def _stream_input_args(ua: str) -> list[str]:
    return [*_http_args(ua), "-rw_timeout", str(STALL_S * 1_000_000), "-reconnect", "1",
            "-reconnect_on_network_error", "1", "-reconnect_delay_max", "10"]


def _streams_of(url: str, ua: str) -> dict:
    """{'video': height or 0, 'audio': bool, 'duration': s} of one media URL (ffprobe, HTTP)."""
    from app.ffmpegutil import ffprobe_bin

    r = subprocess.run([ffprobe_bin(), "-v", "error", *_http_args(ua), "-show_entries",
                        "stream=codec_type,height:format=duration", "-of", "json", url],
                       capture_output=True, text=True, timeout=60)
    try:
        doc = json.loads(r.stdout or "{}")
    except ValueError:
        doc = {}
    streams = doc.get("streams") or []
    return {"video": max((int(s.get("height") or 0) for s in streams
                          if s.get("codec_type") == "video"), default=0),
            "audio": any(s.get("codec_type") == "audio" for s in streams),
            "duration": float((doc.get("format") or {}).get("duration") or 0)}


def pick_streams(info: dict[str, dict]) -> tuple[str, str | None] | None:
    """(video url, separate audio url or None): the sharpest file that has both, unless a
    video-only stream is sharper and an audio-only one exists to go with it. Pure."""
    both = [(v["video"], u) for u, v in info.items() if v["video"] and v["audio"]]
    vonly = [(v["video"], u) for u, v in info.items() if v["video"] and not v["audio"]]
    aonly = [u for u, v in info.items() if v["audio"] and not v["video"]]
    best_both = max(both, default=None)
    best_v = max(vonly, default=None)
    if best_v and aonly and (not best_both or best_v[0] > best_both[0]):
        return best_v[1], aonly[0]
    if best_both:
        return best_both[1], None
    return (best_v[1], None) if best_v else None


def _douyin_web(job: Job, url: str) -> Job:
    from app.ffmpegutil import ffmpeg_bin

    vid = douyin_id(url)
    data = _capture_checked(vid or "")
    ua = data.get("user_agent") or "Mozilla/5.0"
    info = {u: _streams_of(u, ua) for u in data["urls"]}
    pick = pick_streams(info)
    if pick is None:
        raise RuntimeError("Không tìm thấy luồng video trong các yêu cầu của trang.")
    video, audio = pick
    # into a temp name: a stopped / failed download must never pass for source.mp4
    part = job.work_dir / "source.downloading.mp4"
    cmd = [ffmpeg_bin(), "-y", "-v", "error", *_stream_input_args(ua), "-i", video]
    if audio:
        cmd += [*_stream_input_args(ua), "-i", audio, "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c", "copy", "-movflags", "+faststart", str(part)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        part.unlink(missing_ok=True)
        raise RuntimeError("Tải luồng video quá lâu — connection timed out") from exc
    if r.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        tail = (r.stderr or "").strip().splitlines()[-1:] or ["ffmpeg lỗi"]
        stalled = any(w in tail[0].lower() for w in ("timed out", "i/o error", "connection",
                                                     "end of file", "reset"))
        raise RuntimeError(("Tải luồng video bị đứng (mạng chậm hoặc ngắt) — connection timed "
                            "out: " if stalled else "Tải luồng video thất bại: ") + tail[0])
    from app import fsutil

    fsutil.replace(part, job.source_video)
    job.metadata = {
        "title": data.get("title") or f"Douyin {vid}",
        "duration": data.get("duration") or max(v.get("duration", 0) for v in info.values()) or None,
        "uploader": data.get("uploader") or None,
        "platform": "douyin",
        "downloaded_file": job.source_video.name,
        "downloader": "browser",
        "webpage_url": url,
        "video_id": vid,
    }
    if data.get("suspect"):
        job.metadata["suspect"] = data["suspect"]
    return job
