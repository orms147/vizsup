"""Losing the network, the app or the power mid-run must not lose work:
- state files are replaced atomically (a failed write leaves the old content),
- a render cut off mid-encode leaves no output.mp4 that looks finished,
- a run that fails because the internet dropped waits for it and resumes,
- the runs in flight are kept on disk and offered again after a crash."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import fsutil, net  # noqa: E402
from app.models import Cue, Job  # noqa: E402


# --- atomic writes ------------------------------------------------------------------------

def test_atomic_write_keeps_the_old_file_when_the_write_fails(tmp_path, monkeypatch):
    p = tmp_path / "vi.srt"
    fsutil.atomic_write_text(p, "bản cũ")
    assert p.read_text(encoding="utf-8") == "bản cũ"

    def boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "fsync", boom)                   # dies mid-write
    with pytest.raises(OSError):
        fsutil.atomic_write_text(p, "bản mới")
    assert p.read_text(encoding="utf-8") == "bản cũ"
    assert [f.name for f in tmp_path.iterdir()] == ["vi.srt"]    # no temp file left behind


def test_replace_retries_a_briefly_locked_target(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("x")
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(fsutil.time, "sleep", lambda _s: None)
    fsutil.replace(src, dst)
    assert dst.read_text() == "x" and len(calls) == 3


def test_subtitles_and_project_files_are_written_atomically(tmp_path, monkeypatch):
    from app.pipeline import approval
    from app.pipeline.srt import write_srt

    seen = []
    real = fsutil.atomic_write_bytes
    monkeypatch.setattr(fsutil, "atomic_write_bytes",
                        lambda path, data: seen.append(Path(path).name) or real(path, data))
    job = Job(id="j", work_dir=tmp_path / "j")
    write_srt([Cue(1, 0.0, 1.0, "xin chào")], job.vi_srt)
    approval.approve(job)
    assert {"vi.srt", "approved.json"} <= set(seen)


# --- a render cut off mid-encode ------------------------------------------------------------

def test_an_interrupted_encode_leaves_no_output(tmp_path, monkeypatch):
    from app.pipeline import assemble

    job = Job(id="r", work_dir=tmp_path / "r")
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    job.source_video.write_bytes(b"v")
    monkeypatch.setattr(assemble, "probe_duration", lambda _p: 1.0)
    monkeypatch.setattr(assemble, "_video_encoder", lambda: "libx264")
    names = []

    def killed(cmd, cwd, **kw):
        names.append(cmd[-1])
        (cwd / cmd[-1]).write_bytes(b"half")               # ffmpeg wrote part of it …
        raise InterruptedError("Đã hủy.")                    # … then the app was stopped

    monkeypatch.setattr(assemble, "_run_ffmpeg", killed)
    with pytest.raises(InterruptedError):
        assemble.assemble(job, [])
    assert names == ["output" + assemble.RENDERING_SUFFIX]
    assert not job.output_video.exists() and not (job.work_dir / names[0]).exists()

    def finished(cmd, cwd, **kw):
        (cwd / cmd[-1]).write_bytes(b"whole video")

    monkeypatch.setattr(assemble, "_run_ffmpeg", finished)
    assemble.assemble(job, [])
    assert job.output_video.read_bytes() == b"whole video"


# --- network ----------------------------------------------------------------------------------

class ConnectError(Exception):
    pass


def test_network_errors_are_recognised_through_the_chain():
    try:
        try:
            raise ConnectError("All connection attempts failed")
        except ConnectError as inner:
            raise RuntimeError("Bộ dịch lỗi") from inner
    except RuntimeError as outer:
        assert net.looks_like_network_error(outer)
    assert net.looks_like_network_error(OSError("[WinError 11001] getaddrinfo failed"))
    assert not net.looks_like_network_error(RuntimeError("Phụ đề chưa được duyệt"))
    assert not net.looks_like_network_error(ValueError("bad"))


def test_a_run_waits_for_the_network_and_resumes():
    state = {"online": False, "calls": 0, "waits": [], "back": 0}

    def body():
        state["calls"] += 1
        if state["calls"] == 1:
            raise ConnectError("connection refused")         # the internet dropped
        return "done"

    def online():
        return state["online"]

    def sleep(_s):                                          # the network returns later
        if len(state["waits"]) >= 2:
            state["online"] = True

    out = net.run_resilient(body, online=online, sleep=sleep,
                            on_wait=lambda s: state["waits"].append(s),
                            on_back=lambda: state.__setitem__("back", state["back"] + 1))
    assert out == "done" and state["calls"] == 2 and state["back"] == 1
    assert len(state["waits"]) >= 2


def test_other_errors_fail_at_once_and_cancel_stops_the_wait():
    calls = []

    def bad():
        calls.append(1)
        raise RuntimeError("Phụ đề chưa được duyệt")

    with pytest.raises(RuntimeError):
        net.run_resilient(bad, online=lambda: False, sleep=lambda _s: None)
    assert calls == [1]                                      # no waiting for a real error

    def offline():
        raise ConnectError("network is unreachable")

    with pytest.raises(InterruptedError):
        net.run_resilient(offline, online=lambda: False, sleep=lambda _s: None,
                          cancel=lambda: True)


def test_a_blip_while_online_is_retried_a_few_times():
    calls = []

    def flaky():
        calls.append(1)
        raise ConnectError("connection reset")

    with pytest.raises(ConnectError):
        net.run_resilient(flaky, online=lambda: True, sleep=lambda _s: None, blip_retries=2)
    assert len(calls) == 3


def test_worker_reports_the_wait_and_runs_again(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtCore")
    from desktop.worker import PipelineWorker

    w = PipelineWorker.__new__(PipelineWorker)
    PipelineWorker.__init__(w, Job(id="w", work_dir=tmp_path / "w"),
                            {})
    msgs, logs, runs = [], [], []
    w.progress.connect(lambda f, m: msgs.append((f, m)))
    w.log.connect(logs.append)
    flips = iter([False, False, True, True])
    monkeypatch.setattr(net, "is_online", lambda timeout=2.0: next(flips, True))
    monkeypatch.setattr(net.time, "sleep", lambda _s: None)

    def body():
        runs.append(1)
        if len(runs) == 1:
            raise ConnectError("getaddrinfo failed")

    w._resilient(body)
    assert len(runs) == 2
    assert any(f < 0 and "Mất kết nối mạng" in m for f, m in msgs)
    assert any("Có mạng lại" in ln for ln in logs)


# --- crash recovery -------------------------------------------------------------------------

@pytest.fixture()
def storage(tmp_path, monkeypatch):
    from app.config import settings

    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


def test_session_file_round_trip(storage, monkeypatch):
    from desktop import session

    session.save({"gate": None, "queue": [], "render": None, "batch": []})
    assert not (storage / session.FILE).exists()             # nothing in flight → no file
    state = {"gate": {"job_id": "a", "opts": {}}, "queue": [{"url": "u"}], "render": None,
             "batch": [{"id": "b"}, {"id": "c"}]}
    session.save(state)
    assert session.load() is None                            # this process wrote it
    data = json.loads((storage / session.FILE).read_text(encoding="utf-8"))
    data["pid"] = -1                                         # … a previous run did
    (storage / session.FILE).write_text(json.dumps(data), encoding="utf-8")
    loaded = session.load()
    assert loaded["batch"] == [{"id": "b"}, {"id": "c"}]
    lines = session.describe(loaded, lambda j: f"T-{j}")
    assert lines == ["Đang dịch: T-a", "1 video trong hàng đợi dịch", "2 video chờ dựng hàng loạt"]


def test_main_window_keeps_the_plan_and_resumes_it(storage, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from app.pipeline import approval
    from desktop import main as main_mod
    from desktop import session, store

    def job(jid, *, vi=True, approve=False):
        j = Job(id=jid, work_dir=storage / jid, url=f"https://v.douyin.com/{jid}/")
        (j.work_dir / "metadata.json").write_text(json.dumps({"title": jid}), encoding="utf-8")
        if vi:
            j.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
        if approve:
            approval.approve(j)
        store.save_job(j, {"url": j.url}, "queued")
        return j

    job("aaaaaaaaaaa1", vi=False)                            # was being translated
    job("aaaaaaaaaaa2", vi=False)                            # waiting in the queue
    job("aaaaaaaaaaa3", vi=True)                             # queued, but got there anyway
    job("aaaaaaaaaaa4", approve=True)                        # render cut off
    job("aaaaaaaaaaa5", approve=False)                       # batch item no longer approved
    win = main_mod.MainWindow()
    calls = {"gate": [], "queue": [], "batch": []}
    win._run_gate = lambda j, o, line: calls["gate"].append(j.id)
    win._start_queue = lambda items, **k: calls["queue"].extend(items)
    win._batch_add = lambda items: calls["batch"].extend(items) or len(items)
    win.resume_session({
        "gate": {"job_id": "aaaaaaaaaaa1", "opts": {"url": "x"}},
        "queue": [{"_job_id": "aaaaaaaaaaa2"}, {"_job_id": "aaaaaaaaaaa3"}, {"url": "new"}],
        "render": {"id": "aaaaaaaaaaa4", "opts": {"preset": "light"}, "title": "Bốn"},
        "batch": [{"id": "aaaaaaaaaaa5", "opts": {}}]})
    assert calls["gate"] == ["aaaaaaaaaaa1"]
    assert calls["queue"] == [{"_job_id": "aaaaaaaaaaa2"}, {"url": "new"}]
    assert [b["id"] for b in calls["batch"]] == ["aaaaaaaaaaa4"]
    assert calls["batch"][0]["opts"] == {"preset": "light"}

    # the live state is what gets written: a render in flight + a batch waiting
    win2 = main_mod.MainWindow()
    win2._render_item = {"id": "aaaaaaaaaaa4", "opts": {}, "title": "Bốn"}
    win2._batch = [{"id": "aaaaaaaaaaa5", "opts": {}, "title": "Năm"}]
    win2._persist()
    data = json.loads((storage / session.FILE).read_text(encoding="utf-8"))
    assert data["render"]["id"] == "aaaaaaaaaaa4" and data["batch"][0]["id"] == "aaaaaaaaaaa5"
    win2._closing = True
    win2._render_item, win2._batch = None, []
    win2._persist()                                          # closing: the plan stays
    assert (storage / session.FILE).exists()
    win2._closing = False
    win2._persist()                                          # nothing left → file removed
    assert not (storage / session.FILE).exists()
