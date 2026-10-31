"""Series profile ``autopilot`` block ("Theo dõi tập mới") and the Bộ phim dialog's "Theo dõi"
tab: defaults for old profiles, unknown keys kept, the links box, the dry "Kiểm tra ngay"
(background thread → widgets on the GUI thread). No network: the look is faked."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from app.config import settings
from app.pipeline import series, watch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


# --- the block -----------------------------------------------------------------------------------

def test_an_old_profile_gets_the_defaults_and_saving_keeps_them_backward_compatible(storage):
    d = storage / "_series" / "old"
    d.mkdir(parents=True)
    (d / "series.json").write_text(json.dumps({"name": "旧", "glossary": {"a": "b"}}),
                                   encoding="utf-8")
    p = series.load("old")
    assert p.autopilot == {}
    assert series.autopilot_of(p) == {"on": False, "sources": [], "every_h": 6, "max_per_check": 3}
    assert series.load_autopilot("old")["on"] is False and series.load_autopilot("nope") is None
    series.save(p)
    assert json.loads((d / "series.json").read_text(encoding="utf-8"))["glossary"] == {"a": "b"}


def test_normalize_cleans_sources_clamps_numbers_and_keeps_other_keys():
    ap = series.normalize_autopilot({"on": 1, "sources": [" https://a ", "", "https://a", "https://b"],
                                     "every_h": "0", "max_per_check": 99, "platforms": ["youtube"]})
    assert ap == {"on": True, "sources": ["https://a", "https://b"], "every_h": 1,
                  "max_per_check": 20, "platforms": ["youtube"]}
    assert series.normalize_autopilot({"every_h": "x", "sources": "https://a https://b"})[
        "sources"] == ["https://a", "https://b"]
    assert series.normalize_autopilot(None)["every_h"] == 6


def test_save_autopilot_merges_into_the_fresh_profile(storage):
    p = series.create("末日校车")
    p.autopilot = {"slots": ["20:00"]}
    series.save(p)
    other = series.load(p.slug)
    other.glossary = {"校车": "xe buýt"}           # someone else saved the profile meanwhile
    series.save(other)
    q = series.save_autopilot(p.slug, on=True, sources=["https://www.douyin.com/user/x"])
    again = series.load(p.slug)
    assert again.glossary == {"校车": "xe buýt"} and again.autopilot["slots"] == ["20:00"]
    assert series.autopilot_of(again)["on"] is True and q.autopilot == again.autopilot
    assert series.save_autopilot("nope", on=True) is None


def test_followed_needs_on_and_a_source(storage):
    a = series.create("甲")
    series.save_autopilot(a.slug, on=True, sources=["https://space.bilibili.com/1"])
    b = series.create("乙")
    series.save_autopilot(b.slug, on=True)                       # no page to look at
    c = series.create("丙")
    series.save_autopilot(c.slug, sources=["https://space.bilibili.com/2"])   # off
    assert [p.slug for p in series.followed()] == [a.slug]


# --- the dialog: pure helpers ------------------------------------------------------------------------

pytest.importorskip("PySide6.QtWidgets")
sd = pytest.importorskip("desktop.series_dialog")

from PySide6.QtWidgets import QApplication  # noqa: E402


def test_parse_sources_finds_the_links_once_each():
    text = ("https://www.douyin.com/user/AAA\n\n  https://space.bilibili.com/1/lists/2?type=season \n"
            "长按复制此条消息，打开抖音搜索 https://v.douyin.com/abc/ 查看TA的更多作品。\n"
            "https://www.douyin.com/user/AAA\nrác")
    assert sd.parse_sources(text) == ["https://www.douyin.com/user/AAA",
                                      "https://space.bilibili.com/1/lists/2?type=season",
                                      "https://v.douyin.com/abc/"]
    assert sd.parse_sources("") == []


def test_apply_watch_keeps_other_autopilot_keys():
    p = series.Profile(slug="s", name="s", autopilot={"platforms": ["tiktok"], "on": False})
    sd.apply_watch(p, on=True, sources=["https://a"], every_h=12)
    assert p.autopilot == {"platforms": ["tiktok"], "on": True, "sources": ["https://a"],
                           "every_h": 12, "max_per_check": 3}


def test_format_preview_lists_pages_and_what_would_be_fetched():
    res = {"sources": [{"url": "https://a", "n": 18, "error": ""},
                       {"url": "https://b", "n": 0, "error": "Douyin đòi xác minh"}],
           "new": [{"episode": 9, "title": "《末日校车》09", "published": None},
                   {"episode": 10, "title": "《末日校车》10", "published": 1792794600}],
           "last": 8}
    text = sd.format_preview(res, name="末日校车", cap=1)
    assert "✓ https://a: đọc được 18 video" in text and "✗ https://b: Douyin đòi xác minh" in text
    assert "Sẽ lấy 1 tập" in text and "Tập 9: 《末日校车》09" in text
    assert "Tập 10" in text and text.rstrip().endswith("lần kiểm tra sau")
    none = sd.format_preview({"sources": [], "new": [], "last": 8}, name="末日校车", cap=3)
    assert "Không có tập mới" in none and "tập 8" in none
    mix = {"id": "7699176142011385907", "name": "《末日校车》",
           "url": "https://www.douyin.com/collection/7699176142011385907"}
    text = sd.format_preview({"sources": [{"url": "https://v", "n": 7, "error": "", "mix": mix}],
                              "new": [], "last": 9}, name="末日校车", cap=3)
    assert "đọc được 7 video — 合集 《末日校车》" in text


def test_mix_sources_swaps_an_episode_link_for_its_mix():
    mix = {"id": "1", "name": "《末日校车》", "url": "https://www.douyin.com/collection/7699176142011385907"}
    ep8 = "https://www.douyin.com/video/7699919082623110419"
    ep9 = "https://www.douyin.com/video/7699919754521283890"
    user = "https://www.douyin.com/user/MS4wLjABAAAA-fjbtESaHwMcQ7"
    res = {"sources": [{"url": ep8, "n": 7, "error": "", "mix": mix},
                       {"url": ep9, "n": 7, "error": "", "mix": mix},
                       {"url": user, "n": 30, "error": ""}]}
    assert sd.mix_sources([ep8, user, ep9], res) == [mix["url"], user]
    assert sd.mix_sources([user], {"sources": []}) == [user]


# --- the dialog: the tab -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_tab_shows_and_saves_the_watch_settings(qapp, storage):
    p = series.create("末日校车")
    series.save_autopilot(p.slug, on=True, sources=["https://www.douyin.com/user/A"], every_h=8,
                          platforms=["youtube"])
    dlg = sd.SeriesDialog(series.load(p.slug))
    assert dlg.watch_on.isChecked() and dlg.watch_every.value() == 8
    assert dlg.watch_sources.toPlainText() == "https://www.douyin.com/user/A"
    assert "chưa có" in dlg.watch_last.text()
    dlg.watch_on.setChecked(False)
    dlg.watch_sources.setPlainText("https://space.bilibili.com/9\nhttps://space.bilibili.com/9")
    dlg.watch_every.setValue(3)
    dlg._save()
    ap = series.load_autopilot(p.slug)
    assert ap["on"] is False and ap["sources"] == ["https://space.bilibili.com/9"]
    assert ap["every_h"] == 3 and ap["platforms"] == ["youtube"]


def test_tab_shows_the_last_check_time(qapp, storage):
    p = series.create("乙")
    st = watch.load_state()
    watch.series_state(st, p.slug)["checked"] = time.time()
    watch.save_state(st)
    dlg = sd.SeriesDialog(series.load(p.slug))
    assert "chưa có" not in dlg.watch_last.text()
    dlg.reject()


def test_check_now_without_links_says_so(qapp, storage):
    dlg = sd.SeriesDialog(series.create("丙"))
    dlg._check_now()
    assert "Chưa có link" in dlg.watch_status.text() and dlg.watch_check.isEnabled()
    dlg.reject()


def test_check_now_runs_off_the_gui_thread_and_shows_the_result_on_it(qapp, storage, monkeypatch):
    p = series.create("末日校车")
    threads = {}

    def fake_find_new(prof, sources, **kw):
        threads["look"] = threading.current_thread()
        assert prof.slug == p.slug and sources == ["https://www.douyin.com/user/A"]
        return {"sources": [{"url": sources[0], "n": 18, "error": ""}],
                "new": [{"episode": 9, "title": "《末日校车》09", "published": None}], "last": 8}

    monkeypatch.setattr(watch, "find_new", fake_find_new)
    dlg = sd.SeriesDialog(series.load(p.slug))
    dlg.watch_sources.setPlainText("https://www.douyin.com/user/A")     # typed, not saved
    shown = dlg._checked

    def on_gui(res):
        threads["shown"] = threading.current_thread()
        shown(res)

    monkeypatch.setattr(dlg, "_checked", on_gui)
    dlg._check_now()
    assert not dlg.watch_check.isEnabled()
    deadline = time.time() + 5
    while "shown" not in threads and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert threads["look"] is not threading.main_thread()
    assert threads["shown"] is threading.main_thread()
    assert dlg.watch_check.isEnabled() and not dlg.watch_result.isHidden()
    assert "Tập 9" in dlg.watch_result.toPlainText()
    assert series.load_autopilot(p.slug)["sources"] == []                # a look saves nothing
    dlg.reject()


def test_a_failed_check_is_shown(qapp, storage, monkeypatch):
    dlg = sd.SeriesDialog(series.create("丁"))

    class SyncRunner:
        def run(self, fn, on_done, on_fail=None):
            try:
                on_done(fn())
            except Exception as exc:  # noqa: BLE001
                on_fail(f"{type(exc).__name__}: {exc}")

        def shutdown(self):
            pass

    dlg._bg = SyncRunner()
    monkeypatch.setattr(watch, "find_new", lambda *a, **k: (_ for _ in ()).throw(OSError("mạng")))
    dlg.watch_sources.setPlainText("https://space.bilibili.com/1")
    dlg._check_now()
    assert "mạng" in dlg.watch_status.text() and dlg.watch_check.isEnabled()
    dlg.reject()
