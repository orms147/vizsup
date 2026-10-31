"""Theo dõi tập mới (pipeline/watch.py): which followed pages can be read, which listed
videos are a series' new episodes, the watch state, the library scan — and the Douyin list
mode's card parsing (app/douyin_capture.py). Every network / browser call is faked."""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app import douyin_capture
from app.config import settings
from app.pipeline import series, watch

USER = "https://www.douyin.com/user/MS4wLjABAAAA-fjbtESaHwMcQ7_MYwdz6vhfPcWQbQ7lhVayouaq9ow"
TAIL = " 我一个普通大巴司机，竟带着满车美女大学生一起穿越到了丧尸末世#ai动漫#二次元#原创动画#末世#大学生"

# 阿熊动漫's first page as Douyin showed it on 2026-10-31 (pinned first, then newest)
PAGE = [
    ("7697478442144615718", "《末日校车》后续... #qq阅读", 1792208865),
    ("7699919082623110419", "《末日校车》08" + TAIL, 1792794600),
    ("7699345563585678602", "《末日校车》07" + TAIL, 1792643589),
    ("7698967392357076265", "《末日校车》06" + TAIL, 1792555538),
    ("7698601409347996928", "《末日校车》1-5" + TAIL, 1792470333),
    ("7698219205040016678", "《末日校车》03" + TAIL, 1792381336),
    ("7697700401310666010", "《末日校车》02" + TAIL, 1792276200),
    ("7697370406453366057", "《末日校车》" + TAIL, 1792191032),
    ("7694502441542290734", "《萧族降世》03 #AI漫剧#斗破同人#原创动画#大学生", 1791515959),
    ("7690555316777340206", "《暴击契约》p30 回来了~#动漫#ai动漫#机甲#原创动漫#漫剧", 1790596951),
]


def videos(rows=PAGE):
    return [{"id": i, "url": f"https://www.douyin.com/video/{i}", "title": t, "published": p}
            for i, t, p in rows]


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


# --- sources ---------------------------------------------------------------------------------

@pytest.mark.parametrize("url, kind", [
    (USER, "douyin_user"),
    (USER + "?from_tab_name=main&vid=7699919082623110419", "douyin_user"),
    ("https://www.iesdouyin.com/share/user/MS4wLjABAAAA-fjbtESaHwMcQ7", "douyin_user"),
    ("https://www.douyin.com/user/self?showTab=like", ""),
    ("https://www.douyin.com/video/7699919082623110419", "douyin_video"),
    ("https://www.iesdouyin.com/share/video/7699919082623110419/", "douyin_video"),
    ("https://www.douyin.com/collection/7699176142011385907", "douyin_mix"),
    ("https://www.iesdouyin.com/share/mix/detail/7699176142011385907/", "douyin_mix"),
    ("https://www.douyin.com/jingxuan", ""),
    ("https://space.bilibili.com/3985676", "bilibili_space"),
    ("https://space.bilibili.com/3985676/video", "bilibili_space"),
    ("https://space.bilibili.com/3985676/upload/video", "bilibili_space"),
    ("https://space.bilibili.com/2142762/lists/3662502?type=season", "bilibili_season"),
    ("https://space.bilibili.com/2142762/lists/3662502", "bilibili_season"),
    ("https://space.bilibili.com/2142762/channel/collectiondetail?sid=57445", "bilibili_season"),
    ("https://space.bilibili.com/1958703906/lists/547718?type=series", "bilibili_series"),
    ("https://space.bilibili.com/1958703906/channel/seriesdetail?sid=547718", "bilibili_series"),
    ("https://www.bilibili.com/video/BV1o84y1y7Hd", ""),
    ("https://space.bilibili.com/3985676/favlist?fid=1", ""),
    ("not a link", ""),
])
def test_source_kind(url, kind):
    assert watch.source_kind(url) == kind


def test_normalize_drops_the_douyin_tracking_query():
    assert watch.normalize_source(USER + "?from_tab_name=main") == USER
    assert watch.normalize_source(" https://space.bilibili.com/1 ") == "https://space.bilibili.com/1"
    assert watch.normalize_source("https://www.iesdouyin.com/share/mix/detail/7699176142011385907/"
                                  "?u_code=x") == "https://www.douyin.com/collection/7699176142011385907"
    assert watch.normalize_source("https://www.iesdouyin.com/share/video/7699919082623110419/?x=1") \
        == "https://www.douyin.com/video/7699919082623110419"


def test_list_videos_refuses_a_page_it_cannot_read():
    with pytest.raises(ValueError, match="trang người đăng"):
        watch.list_videos("https://www.douyin.com/jingxuan?modal=1")


# --- new episodes ----------------------------------------------------------------------------

def test_belongs_by_the_name_in_brackets_or_the_match_text():
    p = series.Profile(slug="末日校车", name="末日校车", match="末日校车")
    assert watch.belongs(p, "《末日校车》08 …") and not watch.belongs(p, "《萧族降世》03")
    assert not watch.belongs(p, "《末世车王》01 末日校车的续作")      # another series' name decides
    assert watch.belongs(p, "末日校车 第9集")                         # no 《…》: the match text
    q = series.Profile(slug="mdxc", name="Xe buýt tận thế", match="末日校车")
    assert watch.belongs(q, "《末日校车》08")                          # match inside the 《…》


def test_new_episodes_on_the_real_page_oldest_first():
    p = series.Profile(slug="末日校车", name="末日校车", match="末日校车")
    got = watch.new_episodes(p, videos(), set())
    # not the "后续" teaser, the 1-5 compilation, the unnumbered first post or other series
    assert [v["episode"] for v in got] == [2, 3, 6, 7, 8]
    assert got[0]["id"] == "7697700401310666010" and got[-1]["url"].endswith("7699919082623110419")


def test_new_episodes_skips_seen_ids_and_what_the_library_has():
    p = series.Profile(slug="末日校车", name="末日校车", match="末日校车")
    got = watch.new_episodes(p, videos(), {"7699345563585678602"}, after=6)
    assert [v["episode"] for v in got] == [8]


def test_a_reupload_of_the_same_episode_counts_once_newest_wins():
    p = series.Profile(slug="s", name="剧", match="剧")
    rows = [("1", "《剧》05 a", 100), ("2", "《剧》05 b", 200), ("3", "《剧》04", None)]
    got = watch.new_episodes(p, videos(rows), set())
    assert [(v["episode"], v["id"]) for v in got] == [(4, "3"), (5, "2")]


# --- the Douyin list mode (cards → videos) ------------------------------------------------------

def test_merge_cards_takes_titles_and_times_from_the_caught_posts():
    raw = {"author": "阿熊动漫", "cards": [
        {"href": "//www.douyin.com/video/7697478442144615718", "text": "置顶\n439\n\n《末日校车》后续... #qq阅读",
         "alt": "阿熊动漫：《末日校车》后续... #qq阅读"},
        {"href": "/video/7699919082623110419?previous_page=x", "text": "1229\n\n《末日校车》08 …",
         "alt": "阿熊动漫：《末日校车》08   我一个"},
        {"href": "/video/7699919082623110419", "text": "dup", "alt": ""},
        {"href": "/user/xyz", "text": "not a video", "alt": ""},
    ], "posts": [{"id": "7699919082623110419", "title": "《末日校车》08 exact", "published": 1792794600},
                 {"id": "7600000000000000001", "title": "drawn later", "published": 1}]}
    got = douyin_capture.merge_cards(raw)
    assert [v["id"] for v in got] == ["7697478442144615718", "7699919082623110419",
                                      "7600000000000000001"]
    assert got[0] == {"id": "7697478442144615718",
                      "url": "https://www.douyin.com/video/7697478442144615718",
                      "title": "《末日校车》后续... #qq阅读", "published": None, "pinned": True}
    assert got[1]["title"] == "《末日校车》08 exact" and got[1]["published"] == 1792794600
    assert douyin_capture.merge_cards(raw, limit=1)[0]["id"] == "7697478442144615718"


def test_card_title_without_alt_skips_the_counters():
    card = {"text": "置顶\n1.3万\n\n《末日校车》1-5 我一个普通大巴司机\n", "alt": ""}
    assert douyin_capture._card_title(card, "") == "《末日校车》1-5 我一个普通大巴司机"
    assert douyin_capture._card_title({"text": "439", "alt": ""}, "") == ""


# --- the Douyin listing process (faked) ---------------------------------------------------------

@pytest.fixture()
def browser(storage, monkeypatch):
    from app.pipeline import download

    monkeypatch.setattr(download, "web_available", lambda: True)
    calls = {}

    def answer(data, code=0):
        def fake_run(cmd, **kw):
            calls["cmd"], calls["kw"] = cmd, kw
            if data is not None:
                Path(cmd[5]).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, code, "", "")
        monkeypatch.setattr(watch.subprocess, "run", fake_run)
    return calls, answer


def test_douyin_listing_runs_the_browser_in_list_mode(browser):
    calls, answer = browser
    answer({"videos": [{"id": "7699919082623110419", "url": "", "title": " 《末日校车》08 ",
                        "published": 1792794600, "pinned": False}, {"id": ""}],
            "login_wall": True})
    got = watch.list_videos(USER + "?from_tab_name=main", limit=5)
    assert got == [{"id": "7699919082623110419", "url": "https://www.douyin.com/video/7699919082623110419",
                    "title": "《末日校车》08", "published": 1792794600}]
    cmd = calls["cmd"]
    assert cmd[1:5] == ["-m", "app.douyin_capture", "--list", USER] and cmd[-1] == "5"
    assert Path(cmd[6]).name == "profile" and "_browser" in cmd[6]
    backend = str(Path(watch.__file__).resolve().parents[2])
    assert calls["kw"]["env"]["PYTHONPATH"].split(";" if sys.platform == "win32" else ":")[0] == backend
    assert calls["kw"]["timeout"] == watch.LIST_TIMEOUT_S


# 《末日校车》's 合集 as Douyin answered on 2026-10-31 (the 合集's order: oldest first); 09 is not
# on the creator page (PAGE) at all
MIX_ID = "7699176142011385907"
MIX = [("7697700401310666010", "《末日校车》02" + TAIL, 1792276200, 1),
       ("7698219205040016678", "《末日校车》03" + TAIL, 1792381336, 2),
       ("7698601409347996928", "《末日校车》1-5" + TAIL, 1792470333, 3),
       ("7698967392357076265", "《末日校车》06" + TAIL, 1792555538, 4),
       ("7699345563585678602", "《末日校车》07" + TAIL, 1792643589, 5),
       ("7699919082623110419", "《末日校车》08" + TAIL, 1792794600, 6),
       ("7699919754521283890", "《末日校车》09" + TAIL, 1792881000, 7)]


def mix_page(rows, **extra):
    return {"status": 200, "code": 0, "items": [{"id": i, "title": t, "published": p, "index": n}
                                                for i, t, p, n in rows], **extra}


def test_merge_mix_keeps_the_mix_order_once_each_and_the_newest():
    pages = [mix_page(MIX[4:]), mix_page(MIX[:5])]           # answers in any order, one overlap
    got = douyin_capture.merge_mix(pages)
    assert [v["index"] for v in got] == [1, 2, 3, 4, 5, 6, 7]
    assert got[-1] == {"id": "7699919754521283890",
                       "url": "https://www.douyin.com/video/7699919754521283890",
                       "title": " ".join(("《末日校车》09" + TAIL).split()),
                       "published": 1792881000, "pinned": False, "index": 7}
    assert [v["index"] for v in douyin_capture.merge_mix(pages, limit=2)] == [6, 7]
    no_index = [{"items": [{"id": "b", "index": None}, {"id": "a", "index": 1}, {"id": ""}]}]
    assert [v["id"] for v in douyin_capture.merge_mix(no_index)] == ["b", "a"]   # as answered


def test_mix_ask_js_asks_one_page_of_that_mix_only():
    js = douyin_capture.mix_ask_js(MIX_ID, 20)
    assert f"mix_id={MIX_ID}&cursor=20&count={douyin_capture.MIX_PAGE}" in js
    assert "__" not in js.replace("__vzMixPage", "")
    with pytest.raises(ValueError):
        douyin_capture.mix_ask_js("1'); alert(1); ('")


def test_douyin_mix_listing_runs_the_browser_in_mix_mode(browser):
    calls, answer = browser
    answer({"videos": douyin_capture.merge_mix([mix_page(MIX)]),
            "mix": {"id": MIX_ID, "name": "《末日校车》", "total": 7}, "error": ""})
    got = watch.list_videos("https://www.iesdouyin.com/share/video/7699919082623110419/?u=1")
    cmd = calls["cmd"]
    assert cmd[1:5] == ["-m", "app.douyin_capture", "--mix",
                        "https://www.douyin.com/video/7699919082623110419"]
    assert cmd[-1] == str(watch.MIX_LIMIT) and calls["kw"]["timeout"] == watch.MIX_TIMEOUT_S
    assert [v["id"] for v in got][:2] == ["7699919754521283890", "7699919082623110419"]  # newest first
    assert got[0]["mix"] == {"id": MIX_ID, "name": "《末日校车》",
                             "url": f"https://www.douyin.com/collection/{MIX_ID}"}
    p = series.Profile(slug="末日校车", name="末日校车", match="末日校车")
    res = watch.find_new(p, [f"https://www.douyin.com/collection/{MIX_ID}"],
                         state={"sources": {}, "series": {}}, lister=lambda s, limit: got)
    assert res["sources"][0]["mix"]["url"].endswith(MIX_ID)
    assert [v["episode"] for v in res["new"]] == [2, 3, 6, 7, 8, 9]
    # the creator page would never have offered 09
    assert 9 not in [v["episode"] for v in watch.new_episodes(p, videos(), set())]


def test_douyin_mix_listing_errors(browser):
    _calls, answer = browser
    for data, msg in (({"videos": [], "error": "no_mix"}, "không nằm trong 合集"),
                      ({"videos": [], "error": "captcha"}, "captcha"),
                      ({"videos": [], "error": "no_answer"}, "không trả lời")):
        answer(data)
        with pytest.raises(RuntimeError, match=msg):
            watch.list_videos(f"https://www.douyin.com/collection/{MIX_ID}")


def test_douyin_listing_reports_a_captcha_an_empty_page_and_a_dead_browser(browser, monkeypatch):
    _calls, answer = browser
    answer({"videos": [], "captcha": True})
    with pytest.raises(RuntimeError, match="captcha"):
        watch.list_videos(USER)
    answer({"videos": []})
    with pytest.raises(RuntimeError, match="không hiện video"):
        watch.list_videos(USER)
    answer(None, code=1)
    with pytest.raises(RuntimeError, match="mã 1"):
        watch.list_videos(USER)

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(watch.subprocess, "run", slow)
    with pytest.raises(RuntimeError, match="quá lâu"):
        watch.list_videos(USER)


# --- Bilibili (yt-dlp faked) -------------------------------------------------------------------

class _FakeIE:
    def __init__(self, pages):
        self.pages = pages

    def _download_json(self, *a, **k):
        return self.pages.pop(0)


def fake_ytdlp(monkeypatch, *, key, pages, entries, error=None):
    seen = {}

    class YDL:
        def __init__(self, opts):
            seen["opts"] = opts
            self._ies = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_info_extractor(self, k):
            return self._ies.setdefault(k, _FakeIE(list(pages) if k == key else []))

        def extract_info(self, url, download=True):
            seen["url"], seen["download"] = url, download
            if error:
                raise error
            ie = self._ies[key]
            while ie.pages:
                ie._download_json(url)          # the extractor's own page downloads
            return {"_type": "playlist", "entries": entries}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=YDL))
    return seen


def _bv(n):
    return f"BV1{n:09d}"[:12]


def test_bilibili_season_takes_the_last_items_and_their_titles(monkeypatch):
    pages = [{"code": 0, "data": {"archives": [
        {"bvid": _bv(i), "title": f"《剧》{i:02d}", "pubdate": 1000 + i} for i in range(1, 4)]}}]
    entries = [{"id": _bv(i), "url": f"https://www.bilibili.com/video/{_bv(i)}", "title": None}
               for i in (2, 3)] + [{"id": "1_2", "url": "https://space.bilibili.com/1/lists/2"}]
    seen = fake_ytdlp(monkeypatch, key="BilibiliCollectionList", pages=pages, entries=entries)
    got = watch.list_videos("https://space.bilibili.com/1/lists/9?type=season", limit=2)
    assert seen["opts"]["playlist_items"] == "-2:" and seen["download"] is False
    assert "playlistend" not in seen["opts"] and seen["opts"]["extract_flat"] == "in_playlist"
    assert [(v["id"], v["title"], v["published"]) for v in got] == [
        (_bv(3), "《剧》03", 1003), (_bv(2), "《剧》02", 1002)]          # newest first


def test_bilibili_space_reads_vlist_newest_first(monkeypatch):
    pages = [{"code": 0, "data": {"list": {"vlist": [
        {"bvid": _bv(9), "title": "《剧》09", "created": 2009}]}}}]
    entries = [{"id": _bv(9), "url": f"https://www.bilibili.com/video/{_bv(9)}"},
               {"id": _bv(8), "url": f"https://www.bilibili.com/video/{_bv(8)}", "title": "t8"}]
    seen = fake_ytdlp(monkeypatch, key="BilibiliSpaceVideo", pages=pages, entries=entries)
    got = watch.list_videos("https://space.bilibili.com/5", limit=30)
    assert seen["opts"]["playlistend"] == 30 and "playlist_items" not in seen["opts"]
    assert [(v["title"], v["published"]) for v in got] == [("《剧》09", 2009), ("t8", None)]
    assert got[0]["url"] == f"https://www.bilibili.com/video/{_bv(9)}"


def test_bilibili_rate_limit_is_tried_once_more_then_explained(monkeypatch):
    seen = fake_ytdlp(monkeypatch, key="BilibiliSpaceVideo", pages=[], entries=[],
                      error=RuntimeError("Request is blocked by server (412), please wait"))
    waits = []
    monkeypatch.setattr(watch.time, "sleep", waits.append)
    with pytest.raises(RuntimeError, match="tạm chặn"):
        watch.list_videos("https://space.bilibili.com/5")
    assert waits == [watch.BILI_RETRY_S] and seen["url"] == "https://space.bilibili.com/5"


def test_a_number_inside_the_series_name_is_no_episode():
    p = series.Profile(slug="暖家奇遇第二部", name="暖家奇遇第二部", match="暖家奇遇第二部")
    rows = [("1", "【AI漫剧】《暖家奇遇第二部》", 100), ("2", "《暖家奇遇第二部》第3集", 200)]
    assert [v["episode"] for v in watch.new_episodes(p, videos(rows), set())] == [3]
    q = series.Profile(slug="萧族降世", name="萧族降世", match="萧族降世")
    rows = [("4", "《萧族降世》P1-4 九星斗圣萧玄穿越千年 15分钟合集", 300),   # a compilation
            ("5", "《萧族降世》p05", 400)]
    assert [v["episode"] for v in watch.new_episodes(q, videos(rows), set())] == [5]


# --- state -----------------------------------------------------------------------------------------

def test_state_round_trip_and_a_broken_file_is_a_fresh_state(storage):
    assert watch.load_state() == {"series": {}, "sources": {}}
    st = watch.load_state()
    v = {"id": "7", "title": "《剧》07", "episode": 7, "url": "u"}
    watch.mark(st, "s", v, "failed", error="boom")
    watch.series_state(st, "s")["checked"] = 123.0
    watch.save_state(st)
    assert watch.state_path() == storage / "_autopilot" / "watch.json"
    again = watch.load_state()
    assert again["series"]["s"]["seen"]["7"]["error"] == "boom"
    assert watch.last_checked("s") == 123.0 and watch.last_checked("other") is None
    watch.state_path().write_text("{not json", encoding="utf-8")
    assert watch.load_state() == {"series": {}, "sources": {}}


def test_a_failed_video_is_retried_until_max_tries(storage):
    st = watch.load_state()
    v = {"id": "7", "title": "t", "episode": 7}
    for _ in range(watch.MAX_TRIES - 1):
        watch.mark(st, "s", v, "failed")
        assert "7" not in watch.seen_ids(st, "s")
    watch.mark(st, "s", v, "failed")
    assert "7" in watch.seen_ids(st, "s") and st["series"]["s"]["seen"]["7"]["tries"] == watch.MAX_TRIES
    watch.mark(st, "s", {"id": "8"}, "done")
    assert "8" in watch.seen_ids(st, "s")


# --- the library ---------------------------------------------------------------------------------

def _project(storage: Path, name: str, *, vi=True, slug=None, ep=None, last=None, url=None,
             video_id=None, autopilot=False):
    d = storage / name
    d.mkdir()
    if vi:
        (d / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    if slug:
        (d / "series_link.json").write_text(json.dumps({"slug": slug, "episode": ep, "last": last}),
                                            encoding="utf-8")
    if url:
        (d / "job.json").write_text(json.dumps({"url": url, "opts": {"autopilot": autopilot}}),
                                    encoding="utf-8")
    if video_id:
        (d / "metadata.json").write_text(json.dumps({"video_id": video_id}), encoding="utf-8")
    return d


def test_library_knows_the_last_episode_and_the_ids(storage):
    _project(storage, "a", slug="s", ep=6, last=6, url="https://www.douyin.com/video/7698967392357076265")
    _project(storage, "b", slug="s", ep=1, last=5, video_id="7698601409347996928")
    _project(storage, "c", slug="other", ep=40, last=40)
    _project(storage, "d", vi=False, url="https://www.bilibili.com/video/BV1o84y1y7Hd")   # owner's, mid-run
    _project(storage, "e", vi=False, url="https://www.douyin.com/video/7699919082623110419",
             autopilot=True)                                                     # our own failed run
    _project(storage, "f", vi=False, slug="s", ep=9, last=9)                       # no subtitles yet
    (storage / "_series").mkdir()
    lib = watch.library("s")
    assert lib["last"] == 6
    assert lib["ids"] == {"7698967392357076265", "7698601409347996928", "BV1o84y1y7Hd"}
    assert watch.library("none")["last"] is None


def test_suggested_source_is_the_newest_douyin_episode_of_that_series(storage):
    assert watch.suggested_source("s") is None
    _project(storage, "a", slug="s", ep=8, video_id="7699919082623110419")
    _project(storage, "b", slug="s", ep=9, video_id="7699919754521283890")
    _project(storage, "c", slug="other", ep=40, video_id="7799919754521283890")
    _project(storage, "d", slug="s", ep=2, video_id="BV1o84y1y7Hd")              # Bilibili: not this
    assert watch.suggested_source("s") == "https://www.douyin.com/video/7699919754521283890"
    assert watch.source_kind(watch.suggested_source("s")) == "douyin_video"


def test_ids_in_links_and_bare_ids():
    assert watch.ids_in("https://www.douyin.com/video/7699919082623110419?x=1") == {"7699919082623110419"}
    assert watch.ids_in("https://www.bilibili.com/video/BV1o84y1y7Hd/") == {"BV1o84y1y7Hd"}
    assert watch.ids_in("7699919082623110419") == {"7699919082623110419"}
    assert watch.ids_in(None) == set() and watch.ids_in("12") == set()


def test_find_new_merges_sources_reports_errors_and_skips_the_library(storage):
    p = series.Profile(slug="末日校车", name="末日校车", match="末日校车")
    _project(storage, "ep6", slug=p.slug, ep=6, last=6)
    _project(storage, "ep7", url="https://www.douyin.com/video/7699345563585678602")

    def lister(src, limit):
        if "bad" in src:
            raise RuntimeError("trang lỗi")
        return videos()

    logs = []
    res = watch.find_new(p, ["https://a", "https://bad", "https://a2"], lister=lister, log=logs.append)
    assert [v["episode"] for v in res["new"]] == [8]
    assert [r["n"] for r in res["sources"]] == [len(PAGE), 0, len(PAGE)]
    assert res["sources"][1]["error"] == "trang lỗi" and "trang lỗi" in logs[0]
    assert res["last"] == 6 and res["have"] == ["7699345563585678602"]
    assert not watch.state_path().exists()                    # a look changes nothing
