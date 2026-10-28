"""Ghép tập in the desktop app: a compilation is one Thư viện row (not an editable
project), the "Ghép thành một video" dialog orders / renames / adds / drops rows and
returns its spec, the library hands the spec to the main window, which runs the join
on a worker (real ffmpeg, tiny clips) and keeps the sources from being deleted
meanwhile. Storage is a tmp dir; nothing real is touched."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.models import Job  # noqa: E402
from app.pipeline import compilation as comp  # noqa: E402
from app.pipeline.compilation import Clip, Source  # noqa: E402

SRT = "1\n00:00:00,500 --> 00:00:01,500\nxin chào\n"


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


def _ffmpeg():
    try:
        from app.ffmpegutil import ffmpeg_bin

        return ffmpeg_bin()
    except Exception:  # noqa: BLE001
        return None


needs_ffmpeg = pytest.mark.skipif(_ffmpeg() is None or shutil.which("ffprobe") is None,
                                  reason="ffmpeg not available")


def make_episode(storage: Path, jid: str, title: str, *, dur: float = 2.0,
                 real: bool = False, rendered: bool = True) -> Path:
    from app.pipeline import approval
    from desktop import store

    j = Job(id=jid, work_dir=storage / jid, url=f"https://v.douyin.com/{jid}/")
    j.metadata = {"title": title, "duration": dur, "platform": "douyin"}
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                              encoding="utf-8")
    j.vi_srt.write_text(SRT, encoding="utf-8")
    approval.approve(j)
    if rendered:
        if real:
            subprocess.run([_ffmpeg(), "-v", "error", "-y",
                            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={dur}",
                            "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            "-shortest", str(j.output_video)], check=True)
        else:
            j.output_video.write_bytes(b"o" * 5)
        stamp = (j.work_dir / "approved.json").stat().st_mtime
        os.utime(j.output_video, (stamp + 5, stamp + 5))
    store.save_job(j, {"url": j.url}, "await_edit")
    return j.work_dir


def make_compilation(storage: Path, cid: str = "c0mp11111111", n: int = 3) -> Path:
    d = storage / cid
    d.mkdir()
    (d / "output.mp4").write_bytes(b"v" * 9)
    (d / "vi.srt").write_text(SRT, encoding="utf-8")
    (d / comp.CHAPTERS_TXT).write_text("0:00 Tập 1\n0:02 Tập 2\n", encoding="utf-8")
    eps = [{"id": f"e{i}", "number": i, "title": f"Tập {i}"} for i in range(1, n + 1)]
    (d / comp.COMP_FILE).write_text(json.dumps({
        "id": cid, "title": f"Tổng hợp tập 1–{n}", "duration": 6.0, "episodes": eps,
        "chapters": [{"t": 2.0 * (i - 1), "name": f"Tập {i}"} for i in range(1, n + 1)]},
        ensure_ascii=False), encoding="utf-8")
    return d


# --- store ---------------------------------------------------------------------------

def test_a_compilation_is_one_library_row_not_a_project(storage):
    from desktop import store

    make_episode(storage, "aaaaaaaaaaa1", "第4集")
    d = make_compilation(storage)
    assert not store.is_project_dir(d)
    assert [j["id"] for j in store.list_jobs()] == ["aaaaaaaaaaa1"]   # not on the start page
    items = {it["id"]: it for it in store.library_items()}
    row = items[d.name]
    assert row["kind"] == "compilation" and row["status"] == "compiled"
    assert row["episodes"] == 3 and row["chapters"] == 3 and row["has_output"]
    assert store.videos_of(row) == [("Video ghép", d / "output.mp4")]
    srcs = store.compile_candidates()
    assert {s.id for s in srcs} == {"aaaaaaaaaaa1", d.name}


# --- dialog ----------------------------------------------------------------------------

def _source(sid, title, *, state="ok", kind="job", eps=None, chapters=None, dur=60.0):
    num = comp.episode_number(title)
    return Source(sid, kind, title, [] if state == "missing" else [Clip(Path(f"{sid}.mp4"))],
                  chapters or [(0.0, comp.chapter_name(title, num))],
                  eps if eps is not None else [{"id": sid, "number": num, "title": title}],
                  state, "chưa dựng video" if state == "missing" else "", dur)


def test_dialog_orders_renames_and_returns_its_spec(qapp):
    from desktop.widgets.compile_dialog import CompileDialog

    srcs = [_source("b", "第2集 hai"), _source("a", "第1集 một"), _source("x", "第3集", state="missing"),
            _source("c", "第4集 bốn")]
    dlg = CompileDialog(None, srcs)
    assert dlg._ids() == ["a", "b", "c"]                      # episode order, missing left out
    assert "chưa dựng video — bỏ qua" in dlg.warn_lbl.text() and "Thiếu tập 3" in dlg.warn_lbl.text()
    assert dlg.title_edit.text() == "Tổng hợp tập 1–4"
    assert dlg.ok_btn.isEnabled() and "3 tập" in dlg.summary.text()
    dlg.tree.topLevelItem(0).setText(0, "Mở đầu")             # rename a chapter
    dlg.tree.topLevelItem(2).setSelected(True)
    dlg._move(-1)
    assert dlg._ids() == ["a", "c", "b"]
    dlg.title_edit.setText("Bộ truyện")
    dlg._title_touched = True
    spec = dlg.spec()
    assert spec == {"title": "Bộ truyện", "ids": ["a", "c", "b"],
                    "names": {"a": "Mở đầu", "c": "Tập 4", "b": "Tập 2"}}
    dlg.tree.clearSelection()
    for i in (1, 2):
        dlg.tree.topLevelItem(i).setSelected(True)
    dlg._remove()
    assert dlg._ids() == ["a"] and not dlg.ok_btn.isEnabled()
    dlg._sort()
    dlg.close()


def test_dialog_adds_new_episodes_to_an_old_compilation(qapp, monkeypatch):
    from desktop.widgets import compile_dialog as cd

    old = _source("c50", "Tổng hợp tập 1–50", kind="compilation",
                  eps=[{"id": f"e{i}", "number": i, "title": ""} for i in range(1, 51)],
                  chapters=[(float(i), f"Tập {i}") for i in range(1, 51)])
    pool = [_source("e51", "第51集"), _source("e52", "第52集"), old,
            _source("e7", "第7集")]                           # already inside the 1–50 file
    dlg = cd.CompileDialog(None, [old], candidates=lambda: pool)
    assert not dlg.ok_btn.isEnabled()                          # one compilation alone
    assert "50 chương" in dlg.tree.topLevelItem(0).text(0)

    class Pick:
        def __init__(self, parent, sources):
            assert [s.id for s in sources] == ["e51", "e52"]   # already listed: not offered
        def exec(self):
            return True
        def picked(self):
            return ["e52", "e51"]

    monkeypatch.setattr(cd, "_PickDialog", Pick)
    dlg._add()
    assert dlg._ids() == ["c50", "e51", "e52"] and dlg.ok_btn.isEnabled()
    assert dlg.title_edit.text() == "Tổng hợp tập 1–52"
    assert dlg.spec()["names"] == {"e51": "Tập 51", "e52": "Tập 52"}
    dlg.close()


def test_pick_dialog_lists_and_ticks(qapp):
    from PySide6.QtCore import Qt

    from desktop.widgets.compile_dialog import ROLE, _PickDialog

    dlg = _PickDialog(None, [_source("e2", "EP2"), _source("e1", "EP1")])
    assert [dlg.list.item(i).data(ROLE) for i in range(2)] == ["e1", "e2"]
    assert dlg.picked() == []
    dlg._set_all(Qt.Checked)
    assert dlg.picked() == ["e1", "e2"]
    dlg.close()


# --- library + main window ----------------------------------------------------------------

def test_library_compose_emits_the_dialog_spec(qapp, storage):
    from desktop.views.library_view import LibraryView

    make_episode(storage, "aaaaaaaaaaa1", "第1集")
    make_episode(storage, "aaaaaaaaaaa2", "第2集")
    make_episode(storage, "aaaaaaaaaaa3", "第3集", rendered=False)
    v = LibraryView()
    v.refresh()
    for iid in ("aaaaaaaaaaa1", "aaaaaaaaaaa2"):
        v._rows[iid].check.setChecked(True)
    assert v.compile_btn.isEnabled() and "Ghép 2 video" in v.compile_btn.text()
    seen = []
    v._run_dialog = lambda dlg: seen.append(dlg._ids()) or True
    got = []
    v.compileRequested.connect(got.append)
    assert v.compose(v.selected())
    assert seen == [["aaaaaaaaaaa1", "aaaaaaaaaaa2"]]
    assert got[0]["ids"] == ["aaaaaaaaaaa1", "aaaaaaaaaaa2"]
    assert got[0]["title"] == "Tổng hợp tập 1–2"
    v.compile_started("Tổng hợp tập 1–2", 2)
    assert v._cb_running and not v.compose([])               # one join at a time
    v.set_compile_progress(0.5, "Ghép video · 50%")
    assert v.cb_bar.value() == 500
    v.compile_finished(None, "Đã hủy ghép video.")
    assert not v._cb_running and v.compile_bar.isVisibleTo(v) and not v.cb_watch.isVisibleTo(v)
    v.shutdown()


def test_rows_keep_their_buttons_on_a_narrow_window(qapp, storage):
    """The chips (Bản dựng, Douyin, Video ghép · N tập) are cut, never the actions: wide
    chips once pushed "···" (Xóa, Sao chép mục lục chương) out of the list."""
    from desktop.views.library_view import LibraryView

    make_episode(storage, "aaaaaaaaaaa1", "第1集 một tiêu đề rất rất dài " * 3)
    make_compilation(storage)
    v = LibraryView()
    v.refresh()
    v.resize(900, 700)
    v.show()
    for _ in range(5):
        qapp.processEvents()
    for row in v._rows.values():
        acts = row.open_btn.parentWidget()
        assert acts.geometry().right() <= row.width(), row.item["id"]
        assert row.width() <= row.parentWidget().width()
    v.close()
    v.shutdown()


def _wait(cond, n: int = 1500) -> None:
    import time

    from PySide6.QtWidgets import QApplication

    for _ in range(n):
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)


@needs_ffmpeg
def test_main_window_runs_the_join_and_guards_the_sources(qapp, storage):
    from desktop import main as main_mod
    from desktop import store

    make_episode(storage, "aaaaaaaaaaa1", "第1集", real=True)
    make_episode(storage, "aaaaaaaaaaa2", "第2集", real=True, dur=3.0)
    win = main_mod.MainWindow()
    win._notify = lambda *a, **k: None
    win._open_library()
    win._start_compile({"title": "Hai tập", "ids": ["aaaaaaaaaaa2", "aaaaaaaaaaa1"],
                        "names": {"aaaaaaaaaaa1": "Mở màn"}})
    assert win._library_guard(store.library_item("aaaaaaaaaaa1")) == "đang ghép video"
    _wait(lambda: not win._any_busy() and not win.library._cb_running)
    lib = win.library
    assert lib._cb_path is not None and lib._cb_path.exists()
    info = json.loads((lib._cb_path.parent / comp.COMP_FILE).read_text(encoding="utf-8"))
    assert [c["name"] for c in info["chapters"]] == ["Tập 2", "Mở màn"]   # the given order
    assert info["title"] == "Hai tập"
    assert win._library_guard(store.library_item("aaaaaaaaaaa1")) is None
    row = next(it for it in lib.items() if it["kind"] == "compilation")
    assert row["episodes"] == 2
    lib.shutdown()


@needs_ffmpeg
def test_a_failed_join_leaves_no_folder(qapp, storage):
    from desktop.worker import PipelineWorker

    make_episode(storage, "aaaaaaaaaaa1", "第1集", real=True)
    cid = storage / "cccccccccccc"
    job = Job(id=cid.name, work_dir=cid)
    w = PipelineWorker(job, {"_compile": {"title": "x", "ids": ["aaaaaaaaaaa1", "gone"]}})
    errors = []
    w.failed.connect(errors.append)
    w.run_compile()
    assert errors and "không còn trên máy" in errors[0]
    assert not cid.exists()
