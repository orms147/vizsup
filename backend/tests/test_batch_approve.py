"""«Duyệt hàng loạt» (Thư viện): the projects waiting at the approval gate (vi.srt, not
approved, not running) are listed with their flagged lines; the library counts them; the
dialog approves one at a time or — one click + a confirmation — only the ones with nothing
flagged; every approval goes through the main window's one path with «Duyệt nhanh»
(MainWindow._approve_listed: running projects refused, the open editor saved first, the
after-approve hook). Temp storage, offscreen Qt; the after-approve step is patched."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline import approval, autopilot  # noqa: E402

CLEAN_VI = ("1\n00:00:00,000 --> 00:00:02,000\nXin chào.\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\nHôm nay trời đẹp.\n")
CLEAN_CN = ("1\n00:00:00,000 --> 00:00:02,000\n你好。\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\n今天天气很好。\n")
FLAGGED_VI = ("1\n00:00:00,000 --> 00:00:00,300\nNgắn quá.\n\n"   # far too short to read
              "2\n00:00:02,500 --> 00:00:05,000\nHôm nay trời đẹp.\n")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _project(storage: Path, jid: str, title: str, *, vi: str | None = CLEAN_VI,
             approved: bool = False, short: bool = False, mtime: float | None = None) -> Job:
    d = storage / jid
    d.mkdir()
    if vi is not None:
        (d / "vi.srt").write_text(vi, encoding="utf-8")
        (d / "cn.srt").write_text(CLEAN_CN, encoding="utf-8")
    meta = {"title": title, "duration": 42.0, "platform": "douyin"}
    if short:
        meta["short"] = {"parent": "someepisode", "episode": 3}
    (d / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (d / "job.json").write_text(json.dumps({"id": jid, "title": title, "opts": {}},
                                           ensure_ascii=False), encoding="utf-8")
    job = Job(id=jid, work_dir=d)
    job.metadata = meta
    if approved:
        approval.approve(job)
    if mtime is not None:
        for p in d.iterdir():
            os.utime(p, (mtime, mtime))
    return job


def _fill(storage: Path) -> dict[str, Job]:
    """Two clean episodes, one with a flagged line, one approved, one unfinished, one Short."""
    return {
        "c1": _project(storage, "aaaaaaaaaac1", "第1集 | 《末日校车》01"),
        "c2": _project(storage, "aaaaaaaaaac2", "第2集 | 《末日校车》02"),
        "f3": _project(storage, "aaaaaaaaaaf3", "第3集 | 《末日校车》03", vi=FLAGGED_VI),
        "ok": _project(storage, "aaaaaaaaaaok", "第4集 | 《末日校车》04", approved=True),
        "un": _project(storage, "aaaaaaaaaaun", "第5集 | 《末日校车》05", vi=None),
        "sh": _project(storage, "aaaaaaaaaash", "Short · tập 3", short=True),
    }


def _wait(qapp, cond, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while not cond() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


# --- store -------------------------------------------------------------------------------

def test_gate_waiting_lists_the_unapproved_with_their_flags(storage):
    from desktop import store

    p = _fill(storage)
    assert {r["id"] for r in store.gate_waiting()} == {p["c1"].id, p["c2"].id, p["f3"].id}
    rows = {r["id"]: r for r in store.gate_waiting(flags=True)}
    assert rows[p["c1"].id]["flagged"] == [] and rows[p["c1"].id]["cues"] == 2
    (line,) = rows[p["f3"].id]["flagged"]
    assert line["n"] == 1 and line["vi"] == "Ngắn quá." and line["flags"]
    assert rows[p["c1"].id]["auto"] is False and rows[p["c1"].id]["after"]
    (p["c1"].work_dir / "vi.srt").write_text(CLEAN_VI.replace("Xin chào", "Chào"),
                                            encoding="utf-8")
    approval.approve(p["c2"])
    (p["ok"].work_dir / "vi.srt").write_text(CLEAN_VI.replace("Xin chào", "Chào bạn"),
                                            encoding="utf-8")       # edited after approval
    assert {r["id"] for r in store.gate_waiting()} == {p["c1"].id, p["f3"].id, p["ok"].id}


# --- library -----------------------------------------------------------------------------

def test_the_library_counts_the_waiting_but_not_the_running(qapp, storage):
    from desktop.views.library_view import LibraryView

    p = _fill(storage)
    v = LibraryView()
    v.processing = lambda iid: "đang xử lý" if iid == p["c2"].id else None
    v.refresh()
    assert _wait(qapp, lambda: v.waiting())
    assert set(v.waiting()) == {p["c1"].id, p["f3"].id}
    assert v.approve_btn.isVisibleTo(v) and v.approve_btn.text() == "Duyệt hàng loạt (2)"
    asked = []
    v.batchApproveRequested.connect(lambda: asked.append(1))
    v.approve_btn.click()
    assert asked == [1]
    for job in (p["c1"], p["f3"]):
        approval.approve(job)
    v.refresh()
    assert _wait(qapp, lambda: not v.approve_btn.isVisibleTo(v))
    assert v.waiting() == []
    v.shutdown()


# --- the dialog --------------------------------------------------------------------------

def _rows() -> list[dict]:
    flag = {"n": 1, "start": 7.7, "zh": "你倒是会挑。", "vi": "Cô biết chọn ghê.",
            "flags": ["AI nghi sai"]}
    return [
        {"id": "e3", "title": "第3集 | 《末日校车》03", "duration": 65.0, "mtime": 3, "cues": 20,
         "flagged": [flag], "after": "Bộ phim này chưa bật Tự động đăng.", "auto": False},
        {"id": "e1", "title": "第1集 | 《末日校车》01 #drama", "duration": 61.0, "mtime": 1,
         "cues": 18, "flagged": [], "after": "", "auto": True},
        {"id": "e2", "title": "第2集 | 《末日校车》02", "duration": 0.0, "mtime": 2, "cues": 0,
         "flagged": None, "after": "", "auto": False},
    ]


def test_the_dialog_lists_shows_the_flags_and_approves_one(qapp, storage):
    from desktop.widgets.batch_approve_dialog import BatchApproveDialog

    calls = []
    refuse = {"e2": "đang xử lý"}
    d = BatchApproveDialog(None, _rows(), approve=lambda iid: calls.append(iid) or refuse.get(iid))
    assert [r["id"] for r in d.rows()] == ["e1", "e2", "e3"]        # by episode
    assert d.list.item(0, 0).text() == "第1集 | 《末日校车》01"        # the hashtags dropped
    assert [d.list.item(r, 2).text() for r in range(3)] == ["Không", "?", "1 câu"]
    assert d.summary.text() == "3 video chờ duyệt · 1 video không còn câu cần xem"
    assert d.clean_btn.text() == "Duyệt các video không còn câu cần xem (1)"
    assert not any(b.autoDefault() or b.isDefault()
                   for b in (d.approve_btn, d.clean_btn, d.edit_btn))   # Enter approves nothing
    assert calls == []                                                 # nothing without a click
    d.select("e3")
    assert d.lines.isVisibleTo(d) and d.lines.rowCount() == 1
    assert d.lines.item(0, 0).text() == "00:07" and d.lines.item(0, 2).text() == "Cô biết chọn ghê."
    assert "1 câu được đánh dấu" in d.cur_info.text()
    d.select("e1")
    assert not d.lines.isVisibleTo(d) and "không có câu nào" in d.cur_info.text()
    d.select("e2")
    d.approve_btn.click()                                              # refused: stays listed
    assert calls == ["e2"] and len(d.rows()) == 3 and "đang xử lý" in d.note.text()
    d.select("e3")
    d.approve_btn.click()
    assert calls == ["e2", "e3"] and d.approved == ["e3"]
    assert [r["id"] for r in d.rows()] == ["e1", "e2"]
    assert d.summary.text().startswith("2 video chờ duyệt")
    d.select("e1")
    d.edit_btn.click()
    assert d.edit_id == "e1" and d.result() == d.DialogCode.Accepted
    d.close()


def test_a_named_series_shows_its_vietnamese_title(qapp, storage):
    from desktop.widgets.batch_approve_dialog import BatchApproveDialog, display_title

    job = _project(storage, "aaaaaaaaaae9", "第9集 | 《末日校车》09")
    (job.work_dir / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": 9}),
                                                   encoding="utf-8")
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "series_name": "Chuyến Xe Tận Thế"})
    row = {"id": job.id, "title": "第9集 | 《末日校车》09", "duration": 1.0, "flagged": []}
    assert display_title(row) == "Chuyến Xe Tận Thế · Tập 9"
    d = BatchApproveDialog(None, [row], approve=lambda iid: None)
    assert d.list.item(0, 0).text() == "Chuyến Xe Tận Thế · Tập 9"
    d.close()


def test_the_bulk_button_asks_first_and_approves_only_the_clean(qapp, storage):
    from desktop.widgets.batch_approve_dialog import BatchApproveDialog, CleanConfirm

    calls, asked = [], []
    d = BatchApproveDialog(None, _rows(), approve=lambda iid: calls.append(iid))
    d._confirm_clean = lambda rows: asked.append([r["id"] for r in rows]) and False
    d.clean_btn.click()                                                # the owner said Hủy
    assert asked == [["e1"]] and calls == [] and len(d.rows()) == 3
    d._confirm_clean = lambda rows: True
    d.clean_btn.click()
    assert calls == ["e1"] and d.approved == ["e1"]
    assert [r["id"] for r in d.rows()] == ["e2", "e3"]                 # flagged / unread stay
    assert not d.clean_btn.isEnabled() and d.clean_btn.text().endswith("(0)")
    assert "Đã duyệt 1 video" in d.note.text()
    box = CleanConfirm(None, _rows()[1:2])
    assert box.ok_btn.text() == "Duyệt 1 video" and not box.ok_btn.isDefault()
    d.close()
    box.close()


# --- the main window ---------------------------------------------------------------------

@pytest.fixture
def win(qapp, storage):
    from desktop import main as m

    w = m.MainWindow()
    w.remote.timer.stop()
    w._notify = lambda *a, **k: None
    yield w
    w._closing = True
    w.close()


def _script_dialog(monkeypatch, script) -> list:
    """BatchApproveDialog.exec runs ``script(dlg)`` instead of waiting for the owner; the
    returned list fills once it ran."""
    from desktop.widgets import batch_approve_dialog

    ran: list = []

    def exec_(dlg):
        script(dlg)
        ran.append(dlg)
        return 0

    monkeypatch.setattr(batch_approve_dialog.BatchApproveDialog, "exec", exec_)
    return ran


def _open_batch(win, qapp, ran: list) -> None:
    """«Duyệt hàng loạt» reads the waiting list off the GUI thread, then opens the dialog."""
    win._batch_approve()
    assert _wait(qapp, lambda: bool(ran) and not win._batch_reading)


def test_approve_all_without_flags_saves_the_open_editor_first(win, storage, qapp, monkeypatch):
    p = _fill(storage)
    events: list[tuple[str, str]] = []
    win.job = p["c2"]                                    # this one is open in the editor
    win.editor.save_now = lambda: events.append(("save", str(approval.is_approved(p["c2"]))))
    win.editor.refresh_approval = lambda: events.append(("refresh", p["c2"].id))
    win._autopilot_after_approve = lambda j: events.append(("after", j.id))
    seen = {}

    def script(dlg):
        seen["rows"] = [r["id"] for r in dlg.rows()]
        seen["clean"] = [r["id"] for r in dlg.clean_rows()]

        def confirm(rows):
            seen["confirm"] = [r["id"] for r in rows]
            return True

        dlg._confirm_clean = confirm
        dlg.clean_btn.click()
        seen["left"] = [r["id"] for r in dlg.rows()]

    _open_batch(win, qapp, _script_dialog(monkeypatch, script))
    c1, c2, f3 = p["c1"].id, p["c2"].id, p["f3"].id
    assert seen["rows"] == [c1, c2, f3] and seen["clean"] == [c1, c2] == seen["confirm"]
    assert seen["left"] == [f3]
    assert approval.is_approved(p["c1"]) and approval.is_approved(p["c2"])
    assert not approval.is_approved(p["f3"])
    # the open project: saved before it was approved, its lock refreshed, then the after step
    assert events == [("after", c1), ("save", "False"), ("refresh", c2), ("after", c2)]


def test_a_running_project_is_left_out_and_refused(win, storage, qapp, monkeypatch):
    p = _fill(storage)
    after = []
    win._autopilot_after_approve = lambda j: after.append(j.id)
    win._gate_job = p["c1"]                              # still being processed
    seen = {}
    _open_batch(win, qapp, _script_dialog(
        monkeypatch, lambda dlg: seen.setdefault("rows", [r["id"] for r in dlg.rows()])))
    assert seen["rows"] == [p["c2"].id, p["f3"].id]
    assert win._approve_listed(p["c1"], "Tập 1") == "đang xử lý"
    assert not approval.is_approved(p["c1"]) and after == []
    win._gate_job = None
    win._batch_job = p["c2"]
    assert win._processing(p["c2"].id) == "đang dựng"
    win._batch_job = None
    win._queue.append({"_job_id": p["c2"].id})
    assert win._processing(p["c2"].id) == "đang chờ trong hàng đợi"
    win._queue.clear()


def test_fast_approve_takes_the_same_path(win, storage, monkeypatch):
    """«Duyệt nhanh» refuses a project being rendered, like the batch dialog."""
    from desktop.widgets import autopilot_dialogs

    p = _fill(storage)

    class Dialog:
        edit_requested = False

        def __init__(self, *a, **kw):
            pass

        def exec(self):
            return True

    monkeypatch.setattr(autopilot_dialogs, "FastApproveDialog", Dialog)
    after = []
    win._autopilot_after_approve = lambda j: after.append(j.id)
    win._batch_job = p["c1"]
    win._fast_approve(p["c1"].id)
    assert not approval.is_approved(p["c1"]) and after == []
    assert "đang dựng" in win.status.text.text()
    win._batch_job = None
    win._fast_approve(p["c1"].id)
    assert approval.is_approved(p["c1"]) and after == [p["c1"].id]


def test_edit_in_the_editor_opens_it_without_the_quick_review_popping(win, storage, qapp,
                                                                       monkeypatch):
    p = _fill(storage)
    sd = storage / "_series" / "末日校车"
    sd.mkdir(parents=True)
    (sd / "series.json").write_text(json.dumps({"name": "末日校车", "match": "末日校车"}),
                                    encoding="utf-8")
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True})
    for job in p.values():
        (job.work_dir / "series_link.json").write_text(
            json.dumps({"slug": "末日校车", "episode": 1}), encoding="utf-8")
    popped = []
    win._fast_approve = popped.append

    def script(dlg):
        dlg.select(p["f3"].id)
        dlg.edit_btn.click()

    _open_batch(win, qapp, _script_dialog(monkeypatch, script))
    assert win.job is not None and win.job.id == p["f3"].id
    end = time.time() + 1.0                    # Duyệt nhanh would pop up after 400 ms
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert popped == [] and not approval.is_approved(p["f3"])
    win.job, win.opts = p["c1"], {}
    win._on_gate()                             # an ordinary open still offers it
    assert _wait(qapp, lambda: popped == [p["c1"].id], timeout=3.0)
    win._queue_job = p["c2"]                   # running in the queue: the open is refused …
    win._open_for_edit(p["c2"].id)
    assert win.job.id == p["c1"].id and win._edit_asked is None   # … and nothing stays armed
    win._queue_job = None


def test_nothing_waiting_says_so(win, storage, qapp, monkeypatch):
    _project(storage, "aaaaaaaaaaok", "Đã duyệt", approved=True)
    ran = _script_dialog(monkeypatch, lambda dlg: None)
    win._batch_approve()
    assert win._batch_reading
    win._batch_approve()                                 # a second click while it reads: ignored
    assert _wait(qapp, lambda: not win._batch_reading)
    assert ran == [] and "Không có video nào" in win.status.text.text()
