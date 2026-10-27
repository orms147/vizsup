"""Thư viện (all projects): listing, sizes, the Recycle Bin helper, "chỉ xóa file nặng",
the guard for the open project and the open / watch / delete wiring. Nothing real is
ever deleted: storage is a tmp dir and the Recycle Bin call is mocked."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.models import Job  # noqa: E402

SRT = "1\n00:00:00,000 --> 00:00:01,000\nxin chào\n"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    from app.config import settings

    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


@pytest.fixture()
def trashed(monkeypatch):
    """Record what would go to the Recycle Bin and remove it from the TMP storage only."""
    from desktop import trash

    seen: list[Path] = []

    def fake(paths):
        for p in paths:
            p = Path(p)
            assert "storage" in p.parts           # only ever the tmp storage
            seen.append(p)
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()

    monkeypatch.setattr(trash, "move_to_trash", fake)
    return seen


def make_job(storage: Path, jid: str, *, title: str = "Video", vi: bool = True,
             output: bool = False, preview: bool = False, url: str | None = "https://v.douyin.com/x/",
             heavy: bool = False, size: int = 10) -> Path:
    from desktop import store

    j = Job(id=jid, work_dir=storage / jid, url=url)
    j.metadata = {"title": title, "duration": 65.0, "platform": "douyin"}
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                              encoding="utf-8")
    j.source_video.write_bytes(b"s" * size)
    if vi:
        j.cn_srt.write_text(SRT, encoding="utf-8")
        j.vi_srt.write_text(SRT, encoding="utf-8")
    if output:
        j.output_video.write_bytes(b"o" * 5)
    if preview:
        (j.work_dir / "preview.mp4").write_bytes(b"p" * 3)
    if heavy:
        for d in ("tts", "dub", "stems"):
            (j.work_dir / d).mkdir()
            (j.work_dir / d / "a.flac").write_bytes(b"x" * 7)
        (j.work_dir / "bg.wav").write_bytes(b"b" * 4)
        (j.work_dir / "source.wav").write_bytes(b"w" * 4)
        (j.work_dir / "style.json").write_text("{}", encoding="utf-8")
        (j.work_dir / "commentary").mkdir()
        (j.work_dir / "commentary" / "mine.wav").write_bytes(b"m")
    store.save_job(j, {"url": url, "translator": "passthrough"} if url else {}, "await_edit")
    return j.work_dir


def make_group(storage: Path, gid: str = "parent00001", n: int = 2) -> dict:
    from desktop import store

    (storage / gid).mkdir()
    parts = []
    for k in range(1, n + 1):
        pid = f"part{gid[-4:]}{k}"
        j = Job(id=pid, work_dir=storage / pid)
        j.source_video.write_bytes(b"v" * 6)
        j.metadata = {"title": f"Dài · Phần {k}/{n}",
                      "group": {"id": gid, "index": k, "total": n, "start": 1800.0 * (k - 1)}}
        (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                                  encoding="utf-8")
        (j.work_dir / "tts").mkdir()
        (j.work_dir / "tts" / "1.mp3").write_bytes(b"t" * 2)
        store.save_job(j, {"_job_id": pid, "url": "https://v.douyin.com/long/"}, "queued")
        parts.append({"id": pid, "index": k, "start": 1800.0 * (k - 1)})
    group = {"id": gid, "title": "Video dài", "url": "https://v.douyin.com/long/",
             "duration": 3600.0, "parts": parts}
    (storage / gid / "source.mp4").write_bytes(b"P" * 20)
    (storage / gid / "group.json").write_text(json.dumps(group, ensure_ascii=False),
                                              encoding="utf-8")
    return group


# --- store ------------------------------------------------------------------------------

def test_library_lists_projects_only_and_a_split_video_once(storage):
    from desktop import store

    make_job(storage, "aaaaaaaaaaaa", title="Một")
    group = make_group(storage)
    # not projects: app folders, test leftovers, empty dirs, a leftover part folder
    for name in ("_models", "_browser", "_bench", "_stt_bench", "testboth", "part00001",
                 "bbbbbbbbbbbb"):
        (storage / name).mkdir()
    (storage / "_models" / "vad.onnx").write_bytes(b"m")
    (storage / "smoke").mkdir()
    (storage / "smoke" / "vi.srt").write_text(SRT, encoding="utf-8")
    (storage / "smoke" / "source.mp4").write_bytes(b"s")
    # projects without job.json: an old job id folder with a source still counts
    (storage / "cccccccccccc").mkdir()
    (storage / "cccccccccccc" / "source.mp4").write_bytes(b"s")
    # a run that failed before the download: job.json only
    Job(id="dddddddddddd", work_dir=storage / "dddddddddddd")
    store.save_job(Job(id="dddddddddddd", work_dir=storage / "dddddddddddd",
                       url="https://b23.tv/x"), {"url": "https://b23.tv/x"}, "running")
    items = store.library_items()
    ids = sorted(it["id"] for it in items)
    assert ids == sorted(["aaaaaaaaaaaa", group["id"], "cccccccccccc", "dddddddddddd"])
    g = next(it for it in items if it["kind"] == "group")
    assert [p["index"] for p in g["parts"]] == [1, 2] and g["title"] == "Video dài"
    assert sorted(Path(d).name for d in g["dirs"]) == sorted(
        [p["id"] for p in group["parts"]] + [group["id"]])
    one = next(it for it in items if it["id"] == "aaaaaaaaaaaa")
    assert one["status"] == "review" and one["has_source"] and not one["has_output"]
    assert one["duration"] == 65.0
    assert next(it for it in items if it["id"] == "dddddddddddd")["status"] == "unfinished"
    assert store.library_item(group["parts"][1]["id"])["id"] == group["id"]


def test_sizes_and_heavy_bytes(storage):
    from desktop import store

    make_job(storage, "aaaaaaaaaaaa", heavy=True, size=100, output=True)
    it = store.library_item("aaaaaaaaaaaa")
    d = store.library_details([it])["aaaaaaaaaaaa"]
    assert d["size"] == store.dir_size(storage / "aaaaaaaaaaaa")
    # source 100 + tts/dub/stems 3×7 + bg 4 + source.wav 4
    assert d["heavy"] == 100 + 21 + 8
    assert store.dir_size(storage / "missing") == 0
    assert store.dir_size(storage / "aaaaaaaaaaaa" / "bg.wav") == 4


def test_heavy_paths_keep_subtitles_settings_and_the_render(storage):
    from desktop import store

    make_job(storage, "aaaaaaaaaaaa", heavy=True, output=True, preview=True)
    make_job(storage, "eeeeeeeeeeee", heavy=True, url=None)          # nowhere to re-fetch from
    names = {p.name for p in store.heavy_paths(store.library_item("aaaaaaaaaaaa"))}
    assert names == {"source.mp4", "source.wav", "preview.mp4", "bg.wav", "tts", "dub", "stems"}
    local = {p.name for p in store.heavy_paths(store.library_item("eeeeeeeeeeee"))}
    assert "source.mp4" not in local and "tts" in local
    group = make_group(storage)
    gnames = [p.relative_to(storage).as_posix() for p in
              store.heavy_paths(store.library_item(group["id"]))]
    assert sorted(gnames) == sorted(f"{p['id']}/tts" for p in group["parts"])   # parts keep source


def test_trash_paths_refuses_anything_outside_storage(storage, tmp_path, trashed):
    from desktop import store

    outside = tmp_path / "keep.txt"
    outside.write_text("x")
    with pytest.raises(ValueError):
        store.trash_paths([storage / "aaaaaaaaaaaa", outside])
    with pytest.raises(ValueError):
        store.trash_paths([storage])
    assert trashed == [] and outside.exists()


def test_move_to_trash_uses_the_shell_on_windows(tmp_path, monkeypatch):
    from desktop import trash

    monkeypatch.setitem(sys.modules, "send2trash", None)         # not installed
    monkeypatch.setattr(sys, "platform", "win32")
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b.txt"
    b.write_text("x")
    calls = []
    monkeypatch.setattr(trash, "_shell_delete", lambda items: calls.append(items))
    with pytest.raises(trash.TrashError):                         # still on disk → error
        trash.move_to_trash([a, b, tmp_path / "missing"])
    assert calls == [[str(a.resolve()), str(b.resolve())]]

    def gone(items):
        for p in items:
            shutil.rmtree(p) if Path(p).is_dir() else Path(p).unlink()

    monkeypatch.setattr(trash, "_shell_delete", gone)
    trash.move_to_trash([a, b])
    assert not a.exists() and not b.exists()
    assert trash.FLAGS & trash.FOF_ALLOWUNDO and trash.FLAGS & trash.FOF_WANTNUKEWARNING


def test_move_to_trash_refuses_without_a_recycle_bin(tmp_path, monkeypatch):
    from desktop import trash

    monkeypatch.setitem(sys.modules, "send2trash", None)
    monkeypatch.setattr(sys, "platform", "linux")
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(trash.TrashError):
        trash.move_to_trash([f])
    assert f.exists()


def test_size_format_and_search_folding():
    from desktop.views.library_view import fmt_size, fold

    assert fmt_size(512) == "512 B"
    assert fmt_size(3 * 1024 ** 3 + 200 * 1024 ** 2) == "3,2 GB"
    assert fmt_size(850 * 1024 ** 2) == "850 MB" and fmt_size(12 * 1024) == "12 KB"
    assert fold("Đà Lạt mùa ĐÔNG") == "da lat mua dong"


# --- the view ---------------------------------------------------------------------------

def _wait(cond, n: int = 300) -> None:
    import time

    from PySide6.QtWidgets import QApplication

    for _ in range(n):
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)


def test_view_sorts_filters_and_totals(qapp, storage):
    from desktop.views.library_view import LibraryView

    make_job(storage, "aaaaaaaaaaaa", title="Mèo con", size=10)
    make_job(storage, "bbbbbbbbbbbb", title="Chó lớn", size=5000)
    v = LibraryView()
    v.refresh()
    _wait(lambda: len(v._details) == 2)
    assert "2 dự án" in v.total.text() and "Tổng:" in v.total.text()
    v.set_sort("size")
    assert v.order()[0] == "bbbbbbbbbbbb"
    v.search.setText("meo")
    assert v.visible_ids() == ["aaaaaaaaaaaa"]
    v.search.setText("")
    v.shutdown()


def test_delete_selected_goes_to_the_trash_with_the_right_paths(qapp, storage, trashed):
    from desktop.views.library_view import LibraryView

    make_job(storage, "aaaaaaaaaaaa")
    make_job(storage, "bbbbbbbbbbbb")
    group = make_group(storage)
    v = LibraryView()
    v.refresh()
    plans = []
    v._confirm = lambda plan, heavy_only: plans.append((plan, heavy_only)) or True
    changed = []
    v.changed.connect(lambda: changed.append(1))
    v._rows["aaaaaaaaaaaa"].check.setChecked(True)
    v._rows[group["id"]].check.setChecked(True)
    assert v.delete_btn.isEnabled() and "2 dự án" in v.delete_btn.text()
    assert v.delete_items(v.selected())
    expect = {storage / "aaaaaaaaaaaa", storage / group["id"],
              *(storage / p["id"] for p in group["parts"])}
    assert {Path(p) for p in trashed} == {p.resolve() for p in expect}
    assert plans[0][1] is False and len(plans[0][0]) == 2
    assert (storage / "bbbbbbbbbbbb").exists() and changed
    assert [it["id"] for it in v.items()] == ["bbbbbbbbbbbb"]           # refreshed
    v.shutdown()


def test_cancelled_confirm_deletes_nothing(qapp, storage, trashed):
    from desktop.views.library_view import LibraryView

    make_job(storage, "aaaaaaaaaaaa")
    v = LibraryView()
    v.refresh()
    v._confirm = lambda plan, heavy_only: False
    assert not v.delete_items([v.items()[0]])
    assert trashed == [] and (storage / "aaaaaaaaaaaa").exists()
    v.shutdown()


def test_clean_heavy_keeps_subtitles_settings_and_output(qapp, storage, trashed):
    from desktop.views.library_view import LibraryView

    d = make_job(storage, "aaaaaaaaaaaa", heavy=True, output=True, preview=True)
    v = LibraryView()
    v.refresh()
    v._confirm = lambda plan, heavy_only: heavy_only
    assert v.delete_items(v.items(), heavy_only=True)
    left = sorted(p.name for p in d.iterdir())
    assert left == sorted(["cn.srt", "commentary", "job.json", "metadata.json", "output.mp4",
                           "style.json", "vi.srt"])
    item = v.items()[0]
    assert not item["has_source"] and item["has_output"]                 # still listed
    v.shutdown()


def test_watch_plays_the_best_file_in_app(qapp, storage, monkeypatch):
    from desktop.views import library_view

    make_job(storage, "aaaaaaaaaaaa", output=True, preview=True)
    seen = []

    class FakePlayer:
        def __init__(self, parent, title, videos):
            seen.append((title, [(n, p.name) for n, p in videos]))

        def exec(self):
            return 0

    monkeypatch.setattr(library_view, "PlayerDialog", FakePlayer)
    v = library_view.LibraryView()
    v.refresh()
    v._rows["aaaaaaaaaaaa"].watch_btn.click()
    assert seen == [("Video", [("Bản dựng", "output.mp4"), ("Bản xem nhanh", "preview.mp4"),
                               ("Video gốc", "source.mp4")])]
    opened = []
    v.openRequested.connect(opened.append)
    v._rows["aaaaaaaaaaaa"].open_btn.click()
    assert opened == ["aaaaaaaaaaaa"]
    v.shutdown()


def test_player_dialog_unloads_on_close(qapp, storage):
    from desktop.widgets.player_dialog import PlayerDialog

    d = make_job(storage, "aaaaaaaaaaaa")
    dlg = PlayerDialog(None, "Video", [("Video gốc", d / "source.mp4")])
    unloaded = []
    dlg.panel.unload = lambda: unloaded.append(1)
    dlg.reject()
    assert unloaded == [1]


# --- main window ------------------------------------------------------------------------

def test_open_project_is_protected(qapp, storage, trashed):
    from desktop import main as main_mod
    from desktop import store

    make_job(storage, "aaaaaaaaaaaa")
    group = make_group(storage)
    win = main_mod.MainWindow()
    win.job = store.load_job("aaaaaaaaaaaa")[0]
    win._queue = [{"_job_id": group["parts"][1]["id"]}]
    win._open_library()
    assert win.stack.currentIndex() == main_mod.LIBRARY
    lib = win.library
    assert not lib._rows["aaaaaaaaaaaa"].check.isEnabled()
    assert not lib._rows[group["id"]].check.isEnabled()
    told = []
    lib._inform = lambda title, text: told.append(text)
    lib._confirm = lambda plan, heavy_only: True
    assert not lib.delete_items([store.library_item("aaaaaaaaaaaa")])
    assert told and "đang mở" in told[0] and trashed == []
    win._delete_job("aaaaaaaaaaaa")                         # the start page's ··· → Xóa too
    assert len(told) == 2 and trashed == []
    win.job = None
    win._queue = []
    lib.refresh()
    assert lib._rows["aaaaaaaaaaaa"].check.isEnabled()
    win._delete_job("aaaaaaaaaaaa")
    assert trashed == [(storage / "aaaaaaaaaaaa").resolve()]
    win.library.shutdown()


def test_library_open_resumes_like_a_recent_card(qapp, storage, monkeypatch):
    from desktop import main as main_mod

    make_job(storage, "aaaaaaaaaaaa")
    win = main_mod.MainWindow()
    gates = []
    monkeypatch.setattr(win, "_on_gate", lambda: gates.append(win.job.id))
    win.input.recent.all_btn.click()                         # "Xem tất cả (1)"
    assert win.stack.currentIndex() == main_mod.LIBRARY
    assert win.input.recent.all_btn.text() == "Xem tất cả (1)"
    win.library._rows["aaaaaaaaaaaa"].open_btn.click()
    assert gates == ["aaaaaaaaaaaa"]
    win.library.closed.emit()
    assert win.stack.currentIndex() == main_mod.INPUT
    win.nav.library_btn.click()
    assert win.stack.currentIndex() == main_mod.LIBRARY and win.nav.library_btn.isChecked()
    win.library.shutdown()


def test_cleaned_project_fetches_its_source_again_on_open(qapp, storage, monkeypatch):
    from desktop import main as main_mod

    d = make_job(storage, "aaaaaaaaaaaa")
    (d / "source.mp4").unlink()
    win = main_mod.MainWindow()
    runs, gates = [], []
    monkeypatch.setattr(win, "_run_gate", lambda job, opts, line: runs.append((job.id, opts)))
    monkeypatch.setattr(win, "_on_gate", lambda: gates.append(1))
    win._resume("aaaaaaaaaaaa")
    assert runs and runs[0][0] == "aaaaaaaaaaaa" and runs[0][1]["extract_terms"] is False
    assert gates == []
    e = make_job(storage, "eeeeeeeeeeee", url=None)           # no link: open as before
    (e / "source.mp4").unlink()
    win._resume("eeeeeeeeeeee")
    assert gates == [1] and len(runs) == 1
    win.library.shutdown()
