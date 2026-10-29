"""Download stage: URL normalization, platform routing, the BBDown / browser fallbacks.

`download.py` had zero tests. The risky parts are all pure or thin: the Douyin
feed-URL rewrite (yt-dlp rejects the modal form), which platform a URL maps to,
and the yt-dlp -> BBDown fallback chain, which must report BOTH failures instead
of hiding the first. No network and no yt-dlp import here — `_ytdlp`/`_bbdown`
are patched at the seam.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Job
from app.pipeline import download


# --- normalize_url ------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    # the case that motivated the function: a feed/modal URL yt-dlp cannot fetch
    ("https://www.douyin.com/jingxuan?modal_id=7412345678901234567",
     "https://www.douyin.com/video/7412345678901234567"),
    ("https://www.douyin.com/discover?modal_id=123",
     "https://www.douyin.com/video/123"),
    # already canonical -> untouched
    ("https://www.douyin.com/video/7412345678901234567",
     "https://www.douyin.com/video/7412345678901234567"),
    # non-numeric modal_id is not an id -> leave it alone rather than build garbage
    ("https://www.douyin.com/jingxuan?modal_id=abc",
     "https://www.douyin.com/jingxuan?modal_id=abc"),
    # no modal_id at all
    ("https://www.douyin.com/jingxuan", "https://www.douyin.com/jingxuan"),
    # other platforms are never rewritten
    ("https://www.bilibili.com/video/BV1xx411c7mD",
     "https://www.bilibili.com/video/BV1xx411c7mD"),
    ("https://b23.tv/abcdef", "https://b23.tv/abcdef"),
])
def test_normalize_url(url, expected):
    assert download.normalize_url(url) == expected


# --- platform_of --------------------------------------------------------------

@pytest.mark.parametrize("url, platform", [
    ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili"),
    ("https://b23.tv/abcdef", "bilibili"),
    ("https://www.douyin.com/video/123", "douyin"),
    ("https://www.iesdouyin.com/share/video/123", "douyin"),
    ("https://www.youtube.com/watch?v=x", "unknown"),
    ("not-a-url", "unknown"),
])
def test_platform_of(url, platform):
    assert download.platform_of(url) == platform


# --- cookie options -----------------------------------------------------------

def test_cookie_opts_empty_by_default(monkeypatch):
    monkeypatch.setattr(download.settings, "ytdlp_cookies_file", "")
    monkeypatch.setattr(download.settings, "ytdlp_cookies_from_browser", "")
    assert download._cookie_opts() == {}


def test_cookie_opts_browser_is_wrapped_in_a_tuple(monkeypatch):
    """yt-dlp wants cookiesfrombrowser as a tuple; a bare string silently fails."""
    monkeypatch.setattr(download.settings, "ytdlp_cookies_file", "")
    monkeypatch.setattr(download.settings, "ytdlp_cookies_from_browser", "edge")
    assert download._cookie_opts() == {"cookiesfrombrowser": ("edge",)}


def test_cookie_opts_file(monkeypatch):
    monkeypatch.setattr(download.settings, "ytdlp_cookies_file", "C:/tmp/cookies.txt")
    monkeypatch.setattr(download.settings, "ytdlp_cookies_from_browser", "")
    assert download._cookie_opts() == {"cookiefile": "C:/tmp/cookies.txt"}


# --- _find_produced -----------------------------------------------------------

def test_find_produced_normalizes_a_webm_to_source_mp4(tmp_path):
    """Every downstream stage hardcodes source.mp4; ffmpeg reads by content."""
    job = Job(id="j", work_dir=tmp_path)
    (tmp_path / "source.webm").write_bytes(b"v")
    out = download._find_produced(job)
    assert out == job.source_video
    assert job.source_video.read_bytes() == b"v"
    assert not (tmp_path / "source.webm").exists()


def test_find_produced_ignores_partial_downloads(tmp_path):
    job = Job(id="j", work_dir=tmp_path)
    (tmp_path / "source.mp4.part").write_bytes(b"half")
    with pytest.raises(RuntimeError, match="no output file"):
        download._find_produced(job)


# --- download() routing + fallback -------------------------------------------

def test_download_without_url_raises(tmp_path):
    with pytest.raises(ValueError, match="no URL"):
        download.download(Job(id="j", work_dir=tmp_path))


def test_download_passes_the_normalized_url_to_ytdlp(tmp_path, monkeypatch):
    seen = {}

    def fake_ytdlp(job, url):
        seen["url"] = url
        return job

    monkeypatch.setattr(download, "_ytdlp", fake_ytdlp)
    job = Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/jingxuan?modal_id=999")
    download.download(job)
    assert seen["url"] == "https://www.douyin.com/video/999"


def test_bilibili_falls_back_to_bbdown_when_configured(tmp_path, monkeypatch):
    calls = []

    def boom(job, url):
        calls.append("ytdlp")
        raise RuntimeError("yt-dlp exploded")

    def fake_bbdown(job, url):
        calls.append("bbdown")
        job.metadata["downloader"] = "bbdown"
        return job

    monkeypatch.setattr(download, "_ytdlp", boom)
    monkeypatch.setattr(download, "_bbdown", fake_bbdown)
    monkeypatch.setattr(download.settings, "bbdown_path", "C:/tools/BBDown.exe")

    job = Job(id="j", work_dir=tmp_path, url="https://www.bilibili.com/video/BV1xx411c7mD")
    assert download.download(job).metadata["downloader"] == "bbdown"
    assert calls == ["ytdlp", "bbdown"]


def test_douyin_without_the_browser_reraises(tmp_path, monkeypatch):
    """No QtWebEngine -> nothing to fall back to; BBDown is Bilibili-only."""
    def boom(job, url):
        raise RuntimeError("douyin needs fresh cookies")

    monkeypatch.setattr(download, "_ytdlp", boom)
    monkeypatch.setattr(download, "web_available", lambda: False)
    monkeypatch.setattr(download, "_bbdown", lambda job, url: pytest.fail("must not be called"))
    monkeypatch.setattr(download.settings, "bbdown_path", "C:/tools/BBDown.exe")

    job = Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/video/123")
    with pytest.raises(RuntimeError, match="fresh cookies"):
        download.download(job)


def test_douyin_falls_back_to_the_embedded_browser(tmp_path, monkeypatch):
    """Oct 2026: Douyin 403s yt-dlp's unsigned API call whatever the cookies."""
    def boom(job, url):
        raise RuntimeError("Fresh cookies (not necessarily logged in) are needed")

    seen, logs = {}, []

    def fake_web(job, url):
        seen["url"] = url
        job.metadata = {"downloader": "browser"}
        return job

    monkeypatch.setattr(download, "_ytdlp", boom)
    monkeypatch.setattr(download, "web_available", lambda: True)
    monkeypatch.setattr(download, "_douyin_web", fake_web)
    job = Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/jingxuan?modal_id=7699")
    assert download.download(job, log=logs.append).metadata["downloader"] == "browser"
    assert seen["url"] == "https://www.douyin.com/video/7699"
    assert logs and "trình duyệt" in logs[0]


def test_douyin_both_paths_failing_reports_both(tmp_path, monkeypatch):
    def boom(job, url):
        raise RuntimeError("yt-dlp 403")

    def boom2(job, url):
        raise RuntimeError("captcha")

    monkeypatch.setattr(download, "_ytdlp", boom)
    monkeypatch.setattr(download, "web_available", lambda: True)
    monkeypatch.setattr(download, "_douyin_web", boom2)
    job = Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/video/1")
    with pytest.raises(RuntimeError) as exc:
        download.download(job)
    assert "yt-dlp 403" in str(exc.value) and "captcha" in str(exc.value)


@pytest.mark.parametrize("url, vid", [
    ("https://www.douyin.com/jingxuan?modal_id=7699070929850207498", "7699070929850207498"),
    ("https://www.douyin.com/video/7699070929850207498?previous_page=x", "7699070929850207498"),
    ("https://www.douyin.com/note/7412345678901234567", "7412345678901234567"),
    ("https://www.douyin.com/jingxuan", None),
])
def test_douyin_id(url, vid):
    assert download.douyin_id(url) == vid


def test_pick_streams_prefers_the_sharpest_file_with_sound():
    info = {"v576": {"video": 576, "audio": False}, "a": {"video": 0, "audio": True},
            "both1080": {"video": 1080, "audio": True}, "both720": {"video": 720, "audio": True}}
    assert download.pick_streams(info) == ("both1080", None)


def test_pick_streams_pairs_a_sharper_video_only_stream_with_the_audio():
    info = {"v1080": {"video": 1080, "audio": False}, "a": {"video": 0, "audio": True},
            "both540": {"video": 540, "audio": True}}
    assert download.pick_streams(info) == ("v1080", "a")


def test_pick_streams_video_only_without_audio_and_nothing():
    assert download.pick_streams({"v": {"video": 720, "audio": False}}) == ("v", None)
    assert download.pick_streams({"a": {"video": 0, "audio": True}}) is None
    assert download.pick_streams({}) is None


def test_douyin_web_downloads_the_picked_streams(tmp_path, monkeypatch):
    """The capture -> probe -> ffmpeg chain, with every process faked."""
    monkeypatch.setattr(download, "_capture", lambda vid: {
        "urls": ["https://x.zjcdn.com/v", "https://x.zjcdn.com/a"], "user_agent": "UA/1",
        "title": "标题", "uploader": "作者", "duration": 12.5})
    monkeypatch.setattr(download, "_streams_of", lambda u, ua: (
        {"video": 1080, "audio": False} if u.endswith("/v") else {"video": 0, "audio": True}))
    cmds = []

    class R:
        returncode, stderr = 0, ""

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"mp4")
        return R()

    monkeypatch.setattr(download.subprocess, "run", fake_run)
    job = Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/video/76990000001")
    download._douyin_web(job, "https://www.douyin.com/video/76990000001")
    cmd = cmds[0]
    assert cmd.count("-i") == 2 and "https://x.zjcdn.com/a" in cmd
    assert "UA/1" in cmd and any("Referer: https://www.douyin.com/" in c for c in cmd)
    assert job.source_video.read_bytes() == b"mp4"
    assert job.metadata["title"] == "标题" and job.metadata["video_id"] == "76990000001"
    assert job.metadata["platform"] == "douyin" and job.metadata["duration"] == 12.5


def test_capture_reuses_a_fresh_capture_and_reports_an_empty_page(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(download, "_browser_dir", lambda: tmp_path)
    (tmp_path / "1.json").write_text(json.dumps({"urls": ["u"]}), encoding="utf-8")
    monkeypatch.setattr(download.subprocess, "run", lambda *a, **k: pytest.fail("no browser"))
    assert download._capture("1")["urls"] == ["u"]

    def empty_page(cmd, **kw):
        Path(cmd[4]).write_text(json.dumps({"urls": []}), encoding="utf-8")

        class R:
            returncode = 2
        return R()

    monkeypatch.setattr(download.subprocess, "run", empty_page)
    with pytest.raises(RuntimeError, match="không phát video"):
        download._capture("2")
    with pytest.raises(RuntimeError, match="mã video"):
        download._capture("")


def test_bilibili_without_bbdown_configured_reraises_ytdlp(tmp_path, monkeypatch):
    def boom(job, url):
        raise RuntimeError("yt-dlp exploded")

    monkeypatch.setattr(download, "_ytdlp", boom)
    monkeypatch.setattr(download.settings, "bbdown_path", "")
    job = Job(id="j", work_dir=tmp_path, url="https://www.bilibili.com/video/BV1")
    with pytest.raises(RuntimeError, match="yt-dlp exploded"):
        download.download(job)


def test_both_downloaders_failing_reports_both(tmp_path, monkeypatch):
    """Hiding the yt-dlp error behind the BBDown error makes this undebuggable."""
    def boom(job, url):
        raise RuntimeError("yt-dlp exploded")

    def boom2(job, url):
        raise RuntimeError("bbdown exploded")

    monkeypatch.setattr(download, "_ytdlp", boom)
    monkeypatch.setattr(download, "_bbdown", boom2)
    monkeypatch.setattr(download.settings, "bbdown_path", "C:/tools/BBDown.exe")

    job = Job(id="j", work_dir=tmp_path, url="https://www.bilibili.com/video/BV1")
    with pytest.raises(RuntimeError) as exc:
        download.download(job)
    assert "yt-dlp exploded" in str(exc.value)
    assert "bbdown exploded" in str(exc.value)


# --- _bbdown itself -----------------------------------------------------------

def test_bbdown_refuses_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(download.settings, "bbdown_path", "")
    with pytest.raises(RuntimeError, match="BBDown"):
        download._bbdown(Job(id="j", work_dir=tmp_path), "https://b23.tv/x")


def test_bbdown_refuses_when_the_exe_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(download.settings, "bbdown_path", str(tmp_path / "nope.exe"))
    with pytest.raises(RuntimeError, match="BBDown"):
        download._bbdown(Job(id="j", work_dir=tmp_path), "https://b23.tv/x")


def test_bbdown_invokes_the_exe_and_tags_metadata(tmp_path, monkeypatch):
    exe = tmp_path / "BBDown.exe"
    exe.write_bytes(b"MZ")
    work = tmp_path / "job"
    job = Job(id="j", work_dir=work)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        (work / "source.flv").write_bytes(b"v")   # BBDown drops its own container
        return None

    monkeypatch.setattr(download.settings, "bbdown_path", str(exe))
    monkeypatch.setattr("subprocess.run", fake_run)

    out = download._bbdown(job, "https://b23.tv/x")
    assert out.metadata["downloader"] == "bbdown"
    assert out.metadata["platform"] == "bilibili"
    assert job.source_video.exists()             # normalized off .flv
    assert str(work) in seen["cmd"]              # --work-dir points at the job dir
    assert "-F" in seen["cmd"]


# --- cookies: file wins, and failures say what actually works ------------------

def test_a_cookie_file_wins_over_the_browser(monkeypatch):
    """With both set, yt-dlp tried the browser first and died on Edge's locked,
    app-bound-encrypted DB — so a perfectly good cookies.txt was never used."""
    monkeypatch.setattr(download.settings, "ytdlp_cookies_file", "C:/c/douyin.cookies.txt")
    monkeypatch.setattr(download.settings, "ytdlp_cookies_from_browser", "edge")
    assert download._cookie_opts() == {"cookiefile": "C:/c/douyin.cookies.txt"}


def _cookie_cfg(monkeypatch, *, file="", browser=""):
    monkeypatch.setattr(download.settings, "ytdlp_cookies_file", file)
    monkeypatch.setattr(download.settings, "ytdlp_cookies_from_browser", browser)


def test_locked_browser_db_points_to_cookies_txt(monkeypatch):
    _cookie_cfg(monkeypatch, browser="edge")
    err = download._cookie_error(RuntimeError(
        "ERROR: Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271"))
    assert err is not None
    msg = str(err)
    assert "đang mở" in msg and "App-Bound" in msg and "cookies.txt" in msg


def test_fresh_cookie_error_without_any_cookies(monkeypatch):
    _cookie_cfg(monkeypatch)
    err = download._cookie_error(RuntimeError(
        "[Douyin] 768: Fresh cookies (not necessarily logged in) are needed"))
    assert "Chưa cấu hình cookie" in str(err) and "cookies.txt" in str(err)


def test_fresh_cookie_error_with_an_expired_file(monkeypatch):
    _cookie_cfg(monkeypatch, file="C:/c/old.txt")
    err = download._cookie_error(RuntimeError("Fresh cookies are needed"))
    assert "hết hạn" in str(err)


def test_non_cookie_errors_pass_through(monkeypatch):
    _cookie_cfg(monkeypatch, browser="edge")
    assert download._cookie_error(RuntimeError("HTTP Error 404: Not Found")) is None


def test_the_friendly_message_reaches_the_caller(tmp_path, monkeypatch):
    """End to end through _ytdlp with yt-dlp itself faked."""
    import sys
    import types

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            raise RuntimeError("ERROR: Could not copy Chrome cookie database. See ...7271")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    _cookie_cfg(monkeypatch, browser="edge")
    job = Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/video/1")
    with pytest.raises(RuntimeError, match="cookies.txt"):
        download._ytdlp(job, job.url)


def test_capture_problem_spots_another_video_or_a_missing_author():
    ok = {"href": "https://www.douyin.com/video/7699919754521283890", "uploader": "阿熊动漫"}
    assert download.capture_problem(ok, "7699919754521283890") is None
    assert "khác" in download.capture_problem({**ok, "href": "https://www.douyin.com/jingxuan"},
                                              "7699919754521283890")
    # 2026-10-28: the page played a 剑网3 ad — no author on it
    assert "quảng cáo" in download.capture_problem({**ok, "uploader": ""}, "7699919754521283890")
    assert download.capture_problem({"uploader": "x"}, "1") is None      # older captures: no href


def test_a_suspicious_capture_is_retried_fresh_then_marked_or_refused(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(download, "_browser_dir", lambda: tmp_path)
    vid = "7699919754521283890"
    good = {"urls": ["u"], "href": f"https://www.douyin.com/video/{vid}", "uploader": "阿熊动漫"}
    ad = {**good, "uploader": ""}
    answers = [ad, good]
    monkeypatch.setattr(download, "_capture", lambda v: answers.pop(0))
    (tmp_path / f"{vid}.json").write_text(json.dumps(ad), encoding="utf-8")
    assert download._capture_checked(vid) == good                  # retried …
    assert not (tmp_path / f"{vid}.json").exists()                  # … without the cached ad
    answers[:] = [ad, ad]
    assert download._capture_checked(vid)["suspect"].startswith("không thấy người đăng")
    moved = {**good, "href": "https://www.douyin.com/jingxuan"}
    answers[:] = [moved, moved]
    with pytest.raises(RuntimeError, match="video khác"):
        download._capture_checked(vid)


def test_download_warns_when_the_video_may_be_an_ad(tmp_path, monkeypatch):
    def no_ytdlp(job, url):
        raise RuntimeError("HTTP 403")

    def web(job, url):
        job.metadata = {"title": "50级新世代！", "suspect": "không thấy người đăng — quảng cáo?"}
        return job

    monkeypatch.setattr(download, "_ytdlp", no_ytdlp)
    monkeypatch.setattr(download, "web_available", lambda: True)
    monkeypatch.setattr(download, "_douyin_web", web)
    logs = []
    download.download(Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/video/1"),
                      log=logs.append)
    assert any(m.startswith("⚠ Kiểm tra lại video vừa tải") for m in logs)
