"""Dịch một loạt video (app/pipeline/batch_import.py, desktop/widgets/batch_import_dialog.py):
which links are pages, what the library already has (by link or video id), the run order,
the queue items with the batch's series, readable errors, and the dialog (offscreen Qt) —
which links get queued with which series. The page listing is always faked: no network."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from app.config import settings
from app.douyin_capture import merge_cards, merge_mix
from app.pipeline import batch_import, remote, series, watch

MIX = "https://www.douyin.com/collection/7699176142011385907"
USER = "https://www.douyin.com/user/MS4wLjABAAAAx1y2z3"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _video(vid: str, title: str, published: int | None = None, **extra) -> dict:
    return {"id": vid, "url": f"https://www.douyin.com/video/{vid}", "title": title,
            "published": published, **extra}


PAGE = [_video("7699919754521283890", "《末日校车》09", 1792881000, duration=58.0),
        _video("7699919082623110419", "《末日校车》08", 1792794600, duration=61.5),
        _video("7699345563585678602", "《末日校车》07", 1792643589)]


def _project(storage: Path, jid: str, url: str, meta: dict | None = None, *, srt=False) -> Path:
    d = storage / jid
    d.mkdir()
    (d / "job.json").write_text(json.dumps({"id": jid, "url": url, "opts": {"url": url}}),
                                encoding="utf-8")
    if meta is not None:
        (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if srt:
        (d / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào.\n", encoding="utf-8")
    return d


# --- pure helpers ------------------------------------------------------------------------------

def test_is_page_tells_a_page_from_one_video():
    assert batch_import.is_page(MIX)
    assert batch_import.is_page(USER + "?from_tab_name=main")
    assert batch_import.is_page("https://space.bilibili.com/12345")
    assert batch_import.is_page("https://space.bilibili.com/1/lists/9?type=season")
    assert not batch_import.is_page("https://www.douyin.com/video/7699919082623110419")
    assert not batch_import.is_page("https://www.bilibili.com/video/BV1xx411c7mD")
    assert not batch_import.is_page(f"{MIX} {USER}")            # several links: the E-9 queue
    assert not batch_import.is_page("")


def test_project_index_matches_by_link_and_by_video_id(storage):
    # made from a share link: only its metadata knows the video id
    _project(storage, "aaa111", "https://v.douyin.com/iRNBho6u/",
             {"webpage_url": "https://www.douyin.com/video/7699919082623110419",
              "video_id": "7699919082623110419"}, srt=True)
    _project(storage, "bbb222", "https://www.douyin.com/video/7699345563585678602?modal_id=1")
    (storage / "_series").mkdir()                               # not a project
    index = remote.project_index()
    assert index["https://v.douyin.com/iRNBho6u/"] == "aaa111"
    assert index["7699919082623110419"] == "aaa111"
    assert index["7699345563585678602"] == "bbb222"
    rows = batch_import.mark_local(PAGE, index)
    assert [r["have"] for r in rows] == [None, "aaa111", "bbb222"]
    # project_for_url keeps matching the exact link only
    assert remote.project_for_url("https://v.douyin.com/iRNBho6u/").id == "aaa111"
    assert remote.project_for_url(PAGE[1]["url"]) is None


def test_project_state(storage):
    _project(storage, "gate1", "u1", srt=True)
    _project(storage, "none1", "u2")
    done = _project(storage, "done1", "u3", srt=True)
    (done / "output.mp4").write_bytes(b"mp4")
    assert batch_import.project_state("gate1") == "gate"
    assert batch_import.project_state("none1") == "unfinished"
    assert batch_import.project_state("done1") == "rendered"


def test_oldest_first_by_publish_time_else_the_page_reversed():
    assert [v["id"][-3:] for v in batch_import.oldest_first(PAGE)] == ["602", "419", "890"]
    no_time = [{**v, "published": None} for v in PAGE]
    assert batch_import.oldest_first(no_time) == list(reversed(no_time))


def test_queue_items_carry_the_options_and_the_series_oldest_first():
    base = {"subtitle_source": "srt", "srt": "C:/x.srt", "translator": "openrouter",
            "url": MIX, "file": "C:/v.mp4", "series": "auto", "context_note": "vlog"}
    items = batch_import.queue_items(base, PAGE + [PAGE[0]], "末日校车")
    assert [i["url"] for i in items] == [PAGE[2]["url"], PAGE[1]["url"], PAGE[0]["url"]]
    for it in items:
        assert it["series"] == "末日校车" and it["file"] is None and "srt" not in it
        assert it["subtitle_source"] == "auto" and it["translator"] == "openrouter"
        assert it["context_note"] == "vlog"
    assert batch_import.queue_items({}, PAGE[:1], "")[0]["series"] == "auto"


def test_series_slug_keeps_keys_and_creates_a_new_name(storage):
    assert batch_import.series_slug("auto") == "auto"
    assert batch_import.series_slug("none") == "none"
    assert batch_import.series_slug("  ") == "auto"
    old = series.create("末日校车")
    assert batch_import.series_slug(old.slug) == old.slug
    slug = batch_import.series_slug("  Hài   Douyin ")
    assert slug == series.slugify("Hài Douyin")
    assert series.load(slug).name == "Hài Douyin"
    assert batch_import.series_slug("Hài Douyin") == slug          # the same profile again


def test_suggested_series_from_the_mix_name_or_the_titles(storage):
    assert batch_import.suggested_series(PAGE) is None              # no profile yet
    series.create("末日校车")
    assert batch_import.suggested_series(PAGE) == "末日校车"
    mixed = [_video("1" * 10, "abc", mix={"id": "9" * 10, "name": "末日校车", "url": MIX})]
    assert batch_import.suggested_series(mixed) == "末日校车"
    assert batch_import.suggested_series([_video("2" * 10, "random vlog")]) is None


def test_friendly_errors_are_vietnamese_lines():
    assert batch_import.friendly_error(RuntimeError("Douyin đòi xác minh (captcha) — thử lại sau.")) \
        == "Douyin đòi xác minh (captcha) — thử lại sau."
    assert "kiểm tra mạng" in batch_import.friendly_error(ConnectionError("getaddrinfo failed"))
    assert "kiểm tra mạng" in batch_import.friendly_error(_chained())   # a network cause
    assert batch_import.friendly_error(KeyError("x")).startswith("Không đọc được danh sách video")


def _chained() -> RuntimeError:
    try:
        try:
            raise ConnectionResetError("reset")
        except ConnectionResetError as inner:
            raise RuntimeError("Không đọc được danh sách Bilibili: reset") from inner
    except RuntimeError as exc:
        return exc


def test_load_marks_the_library_and_never_raises(storage):
    _project(storage, "aaa111", PAGE[1]["url"], srt=True)
    seen = {}

    def lister(url, limit):
        seen["url"], seen["limit"] = url, limit
        return [*PAGE, {"id": "x"}]                                  # a row without a link is dropped

    res = batch_import.load(MIX, lister=lister)
    assert seen == {"url": MIX, "limit": batch_import.LIMIT}
    assert res["error"] == "" and res["kind"] == "douyin_mix" and len(res["videos"]) == 3
    assert [(v["have"], v["state"]) for v in res["videos"]] == [(None, ""), ("aaa111", "gate"),
                                                               (None, "")]

    def broken(url, limit):
        raise ValueError("Không đọc được link này — cần 合集 Douyin …")

    bad = batch_import.load("https://example.com/x", lister=broken)
    assert bad["videos"] == [] and bad["error"].startswith("Không đọc được link này")
    assert batch_import.load(MIX, lister=lambda u, limit: [])["error"]


def test_listings_carry_the_length_when_the_page_told_it():
    cards = merge_cards({"cards": [{"href": "/video/7699919082623110419", "alt": "t"}],
                         "posts": [{"id": "7699919082623110419", "title": "t", "duration": 61500}]})
    assert cards[0]["duration"] == 61.5
    mix = merge_mix([{"items": [{"id": "1", "title": "a", "duration": 15000, "index": 1},
                                {"id": "2", "title": "b", "index": 2}]}])
    assert mix[0]["duration"] == 15.0 and "duration" not in mix[1]
    assert watch._clean({"id": "1", "duration": 15.0})["duration"] == 15.0
    assert "duration" not in watch._clean({"id": "1", "duration": 0})
    assert watch._duration("03:25") == {"duration": 205.0}
    assert watch._duration("1:02:03") == {"duration": 3723.0}
    assert watch._duration("abc") == {} and watch._duration(True) == {}


# --- the dialog (offscreen Qt) --------------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))       # repo root → `desktop`


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait(qapp, cond, timeout=8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _dialog(qapp, url=MIX, lister=None, **kw):
    from desktop.widgets.batch_import_dialog import BatchImportDialog

    dlg = BatchImportDialog(None, url=url, base={"translator": "openrouter", "url": url,
                                                 "series": kw.pop("start", "auto")},
                            lister=lister or (lambda u, limit: list(PAGE)), **kw)
    assert _wait(qapp, lambda: not dlg._loading)
    return dlg


def test_dialog_lists_ticks_the_new_ones_and_queues_them_in_a_new_series(qapp, storage):
    from PySide6.QtCore import Qt

    _project(storage, "aaa111", PAGE[1]["url"], srt=True)
    dlg = _dialog(qapp)
    t = dlg.tree
    assert t.topLevelItemCount() == 3
    assert [t.topLevelItem(i).checkState(0) == Qt.Checked for i in range(3)] == [True, False, True]
    assert t.topLevelItem(0).text(1) == "00:58" and t.topLevelItem(2).text(1) == "—"
    assert t.topLevelItem(1).text(2) == "đã có trên máy · chờ duyệt"
    assert t.topLevelItem(0).text(2) == "mới"
    assert dlg.go.text() == "Dịch 2 video" and dlg.go.isEnabled()
    assert "1 đã có trên máy" in dlg.status.text()
    dlg.series.setEditText("Hài Douyin")                             # a new series for the batch
    assert "Tạo bộ phim mới" in dlg.series_note.text()
    dlg.accept()
    slug = series.slugify("Hài Douyin")
    assert series.load(slug) is not None
    assert [(i["url"], i["series"]) for i in dlg.result_items] == [
        (PAGE[2]["url"], slug), (PAGE[0]["url"], slug)]              # oldest first
    assert all(i["translator"] == "openrouter" and i["file"] is None for i in dlg.result_items)
    dlg.deleteLater()


def test_dialog_select_all_none_and_an_existing_series(qapp, storage):
    from PySide6.QtCore import Qt

    series.create("末日校车")
    dlg = _dialog(qapp)
    assert dlg.series_choice() == "末日校车"                        # the titles' 《…》 has a profile
    assert "Tự đăng: tắt" in dlg.series_note.text() and dlg.plan_btn.isEnabled()
    dlg.set_all(Qt.Unchecked)
    assert dlg.go.text() == "Dịch 0 video" and not dlg.go.isEnabled()
    dlg.accept()                                                     # nothing picked: stays open
    assert dlg.result_items == []
    dlg.set_all(Qt.Checked)
    dlg.series.setCurrentIndex(dlg.series.findData("none"))
    dlg.accept()
    assert len(dlg.result_items) == 3 and {i["series"] for i in dlg.result_items} == {"none"}
    dlg.deleteLater()


def test_dialog_keeps_the_start_pages_series_pick(qapp, storage):
    series.create("末日校车")
    other = series.create("Phim khác")
    dlg = _dialog(qapp, start=other.slug, series_choice=other.slug)
    assert dlg.series_choice() == other.slug
    dlg.deleteLater()


def test_dialog_shows_errors_and_a_changed_link_needs_a_new_load(qapp, storage):
    def captcha(url, limit):
        raise RuntimeError("Douyin đòi xác minh (captcha) — thử lại sau.")

    dlg = _dialog(qapp, url=USER, lister=captcha)
    assert "captcha" in dlg.status.text() and dlg.status.objectName() == "errText"
    assert dlg.tree.topLevelItemCount() == 0 and not dlg.go.isEnabled()
    assert dlg.load_btn.isEnabled()                                  # try again

    def crash(url, limit):
        raise KeyError("boom")

    dlg2 = _dialog(qapp, url=USER, lister=crash)
    assert "Không đọc được danh sách video" in dlg2.status.text()
    dlg3 = _dialog(qapp, url=USER)
    assert "~18–36 video" in dlg3.status.text()                      # a creator page's limit
    assert dlg3.go.isEnabled()
    dlg3.url.setText(MIX)                                            # the list is not this link's
    assert not dlg3.go.isEnabled()
    dlg3.accept()
    assert dlg3.result_items == []
    for d in (dlg, dlg2, dlg3):
        d.deleteLater()


def test_dialog_without_a_link_waits_for_one(qapp, storage):
    from desktop.widgets.batch_import_dialog import BatchImportDialog

    dlg = BatchImportDialog(None, url="", lister=lambda u, limit: list(PAGE))
    assert "Dán link" in dlg.status.text() and not dlg.go.isEnabled()
    dlg.reload()
    assert dlg.status.objectName() == "warnText"
    dlg.url.setText(MIX)
    dlg.reload()
    assert _wait(qapp, lambda: not dlg._loading) and dlg.go.text() == "Dịch 3 video"
    dlg.deleteLater()


# --- the start page ----------------------------------------------------------------------------

@pytest.fixture
def view(qapp, storage):
    from app.providers.registry import list_providers
    from desktop.views import input_view

    v = input_view.InputView(list_providers())
    yield v
    v.shutdown()
    v.deleteLater()


def test_start_on_a_page_link_opens_the_picker_and_queues_its_videos(view, qapp, monkeypatch):
    from desktop.widgets import batch_import_dialog as bid

    opened = {}

    def fake_exec(dlg):
        opened["url"], opened["series"] = dlg.url.text(), dlg._start_choice
        dlg._lister = lambda u, limit: list(PAGE)
        dlg.reload()
        assert _wait(qapp, lambda: not dlg._loading)
        dlg.accept()
        return 1

    monkeypatch.setattr(bid.BatchImportDialog, "exec", fake_exec)
    queued, started = [], []
    view.queueRequested.connect(queued.append)
    view.startRequested.connect(started.append)
    view.url.setText(MIX)
    assert view.hint.isVisibleTo(view) and "trang / 合集" in view.hint.text()
    assert view._estimate_source() is None                           # no cost guess for a page
    view._emit_start()
    assert opened == {"url": MIX, "series": "auto"} and started == []
    (items,) = queued
    assert [i["url"] for i in items] == [v["url"] for v in reversed(PAGE)]
    assert {i["series"] for i in items} == {"auto"}
    # one video link still starts that video
    view.url.setText(PAGE[0]["url"])
    view._emit_start()
    assert started and started[-1]["url"] == PAGE[0]["url"] and len(queued) == 1
    # a .srt belongs to one video: a page link is not held back by a missing one
    view.source.setCurrentIndex(view.source.findData("srt"))
    assert not view.start.isEnabled()
    view.url.setText(MIX)
    assert view.start.isEnabled()


def test_the_button_opens_the_picker_with_the_typed_link(view, monkeypatch):
    from desktop.widgets import batch_import_dialog as bid

    seen = []
    monkeypatch.setattr(bid.BatchImportDialog, "exec",
                        lambda dlg: seen.append(dlg.url.text()) or 0)
    monkeypatch.setattr(watch, "list_videos", lambda u, limit: list(PAGE))
    view.url.setText(PAGE[0]["url"])                                 # an episode → its 合集
    assert view.open_batch_import() is False                         # cancelled: nothing queued
    view.url.setText("")
    view.open_batch_import()
    assert seen == [PAGE[0]["url"], ""]
