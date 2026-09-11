"""Download stage: URL normalization, platform routing, and the BBDown fallback.

`download.py` had zero tests. The risky parts are all pure or thin: the Douyin
feed-URL rewrite (yt-dlp rejects the modal form), which platform a URL maps to,
and the yt-dlp -> BBDown fallback chain, which must report BOTH failures instead
of hiding the first. No network and no yt-dlp import here — `_ytdlp`/`_bbdown`
are patched at the seam.
"""
from __future__ import annotations

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


def test_douyin_has_no_fallback_and_reraises(tmp_path, monkeypatch):
    """Douyin's fallback tool churns too fast to wire blind — yt-dlp only."""
    def boom(job, url):
        raise RuntimeError("douyin needs fresh cookies")

    monkeypatch.setattr(download, "_ytdlp", boom)
    monkeypatch.setattr(download, "_bbdown", lambda job, url: pytest.fail("must not be called"))
    monkeypatch.setattr(download.settings, "bbdown_path", "C:/tools/BBDown.exe")

    job = Job(id="j", work_dir=tmp_path, url="https://www.douyin.com/video/123")
    with pytest.raises(RuntimeError, match="fresh cookies"):
        download.download(job)


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
