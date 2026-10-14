"""Long videos in the app: the worker splits, the queue runs the parts, the project list
shows one row per video, parts share context, the render page offers the join."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.models import Job  # noqa: E402
from app.pipeline import split  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "storage")
    (tmp_path / "storage").mkdir()
    return tmp_path / "storage"


def _fake_group(storage: Path, n: int = 3, *, url: str = "https://v.douyin.com/long/") -> dict:
    """A split video on disk without ffmpeg: parent folder + n part folders."""
    from desktop import store

    gid = "parent00001"
    (storage / gid).mkdir()
    parts = []
    for k in range(1, n + 1):
        pid = f"part0000{k}"
        j = Job(id=pid, work_dir=storage / pid)
        j.source_video.write_bytes(b"v")
        j.metadata = {"title": f"Video dài · Phần {k}/{n}",
                      "group": {"id": gid, "index": k, "total": n, "start": 1800.0 * (k - 1)}}
        (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                                  encoding="utf-8")
        store.save_job(j, {"_job_id": pid, "translator": "passthrough"}, "queued")
        parts.append({"id": pid, "index": k, "start": 1800.0 * (k - 1)})
    group = {"id": gid, "title": "Video dài", "url": url, "duration": 1800.0 * n, "parts": parts}
    (storage / gid / "source.mp4").write_bytes(b"parent")
    (storage / gid / "group.json").write_text(json.dumps(group, ensure_ascii=False), encoding="utf-8")
    return group


def test_project_list_has_one_row_per_split_video(storage):
    from desktop import store

    _fake_group(storage)
    solo = Job(id="solo", work_dir=storage / "solo")
    solo.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    store.save_job(solo, {}, "await_edit")
    rows = store.list_projects()
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["group", "job"]                     # parent folder itself is not a project
    g = next(r for r in rows if r["kind"] == "group")
    assert g["title"] == "Video dài" and [p["index"] for p in g["parts"]] == [1, 2, 3]
    assert "https://v.douyin.com/long/" in store.known_urls()


def test_group_summary_points_at_the_part_waiting_for_review(storage):
    from desktop import store

    group = _fake_group(storage)
    p2 = storage / group["parts"][1]["id"]
    (p2 / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhai\n", encoding="utf-8")
    s = store.group_summary(group["id"])
    assert s["next"] == group["parts"][1]["id"] and len(s["parts"]) == 3


def test_parts_share_brief_terms_and_style(storage):
    group = _fake_group(storage)
    p1, p2 = (Job(id=p["id"], work_dir=storage / p["id"]) for p in group["parts"][:2])
    p1.metadata = json.loads((p1.work_dir / "metadata.json").read_text(encoding="utf-8"))
    p2.metadata = json.loads((p2.work_dir / "metadata.json").read_text(encoding="utf-8"))
    from app.pipeline import brief

    brief._save(p1, brief="Người nói: nữ, xưng em", note=None, key="k", source="llm")
    assert split.share_brief(p1, storage) and not split.share_brief(p1, storage)
    assert split.inherit_brief(p2, storage) == "Người nói: nữ, xưng em"
    assert brief.load_brief(p2) == "Người nói: nữ, xưng em"

    (p1.work_dir / "glossary.suggested.json").write_text('{"小王": "Tiểu Vương"}', encoding="utf-8")
    split.share_terms(p1, storage)
    (p2.work_dir / "glossary.suggested.json").write_text(
        '{"小王": "Vương nhỏ", "茶汤": "canh trà"}', encoding="utf-8")
    split.share_terms(p2, storage)
    merged = json.loads((p2.work_dir / "glossary.suggested.json").read_text(encoding="utf-8"))
    assert merged == {"小王": "Tiểu Vương", "茶汤": "canh trà"}       # earlier part wins

    (p1.work_dir / "style.json").write_text('{"font": "Be Vietnam Pro", "size": 22}', encoding="utf-8")
    assert split.inherit_style(p2, storage)
    assert json.loads((p2.work_dir / "style.json").read_text(encoding="utf-8"))["size"] == 22


def _ffmpeg():
    try:
        from app.ffmpegutil import ffmpeg_bin

        return ffmpeg_bin()
    except Exception:  # noqa: BLE001
        return None


@pytest.mark.skipif(_ffmpeg() is None, reason="ffmpeg not available")
def test_worker_splits_a_long_video_and_hands_back_the_parts(qapp, storage, monkeypatch):
    from app.pipeline import vad
    from desktop import store
    from desktop.worker import PipelineWorker

    monkeypatch.setattr(split, "SPLIT_MIN", 60.0)
    monkeypatch.setattr(vad, "spans_for", lambda *a, **k: None)
    job = Job(id="longjob0001", work_dir=storage / "longjob0001", url="https://b23.tv/x")
    subprocess.run([_ffmpeg(), "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=360",
                    "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000:duration=360",
                    "-af", "volume='if(lt(mod(t,7),6),1,0)':eval=frame",
                    "-c:v", "libx264", "-g", "15", "-sc_threshold", "0", "-c:a", "aac",
                    "-shortest", str(job.source_video)], check=True)
    job.metadata = {"title": "Buổi live dài"}
    opts = {"url": "https://b23.tv/x", "translator": "passthrough", "subtitle_source": "asr"}
    store.save_job(job, opts, "running")
    got, gates, fails = [], [], []
    w = PipelineWorker(job, opts)
    w.split_done.connect(got.append)
    w.gate_reached.connect(lambda: gates.append(1))
    w.failed.connect(fails.append)
    w.run_to_gate()
    assert fails == [] and gates == [] and len(got) == 1
    items = got[0]
    assert len(items) == 2 and all(i["_job_id"] and "url" not in i for i in items)
    assert not (job.work_dir / "job.json").exists()          # the parent is no longer a project
    assert (job.work_dir / "group.json").exists()
    loaded = store.load_job(items[1]["_job_id"])
    assert loaded[0].metadata["title"] == "Buổi live dài · Phần 2/2"
    saved = json.loads((loaded[0].work_dir / "job.json").read_text(encoding="utf-8"))
    assert saved["stage"] == "queued"


def test_split_parts_jump_the_queue_and_reuse_their_folders(qapp, storage, monkeypatch):
    from desktop import main as main_mod

    group = _fake_group(storage, 2)
    win = main_mod.MainWindow()
    runs = []
    monkeypatch.setattr(win, "_busy", lambda: False)
    monkeypatch.setattr(win, "_run", lambda method, opts, conns, job=None:
                        runs.append((method, opts, job)))
    win._queue = [{"url": "https://v.douyin.com/other/"}]
    win._queue_total = 1
    items = [{"_job_id": p["id"], "translator": "passthrough"} for p in group["parts"]]
    win._on_split_done(items)
    assert runs and runs[0][2].id == group["parts"][0]["id"]       # part 1 runs next…
    assert [o.get("_job_id") or o.get("url") for o in win._queue] == [
        group["parts"][1]["id"], "https://v.douyin.com/other/"]     # …then part 2, then the rest


def test_resume_group_queues_only_parts_without_subtitles(qapp, storage, monkeypatch):
    from desktop import main as main_mod

    group = _fake_group(storage, 3)
    (storage / group["parts"][0]["id"] / "vi.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nmột\n", encoding="utf-8")
    win = main_mod.MainWindow()
    queued = []
    monkeypatch.setattr(win, "_start_queue", lambda items, front=False: queued.extend(items))
    win._resume_group(group["id"])
    assert [o["_job_id"] for o in queued] == [p["id"] for p in group["parts"][1:]]


def test_render_page_offers_the_join_when_every_part_is_rendered(qapp):
    from desktop.views.render_view import RenderView

    rv = RenderView()
    joins = []
    rv.joinRequested.connect(joins.append)
    rv.set_group({"id": "g1", "index": 2, "total": 3, "rendered": 2, "approved": 3})
    assert rv.group_box.isVisibleTo(rv) and not rv.join_btn.isEnabled()
    assert "Phần 2/3" in rv.group_lbl.text()
    rv.set_group({"id": "g1", "index": 2, "total": 3, "rendered": 3, "approved": 3})
    rv.join_btn.click()
    assert joins == ["g1"]
    rv.set_group(None)
    assert not rv.group_box.isVisibleTo(rv)


def test_recent_projects_group_row(qapp, storage):
    from desktop.widgets.recent_projects import RecentProjects

    group = _fake_group(storage, 2)
    rp = RecentProjects()
    assert list(rp._groups) == [group["id"]] and not rp._rows
    row = rp._groups[group["id"]]
    opened, joined = [], []
    rp.openRequested.connect(opened.append)
    rp.groupJoinRequested.connect(joined.append)
    row.apply_group({"id": group["id"], "parts": [
        {"id": p["id"], "index": p["index"], "approved": True, "rendered": True, "has_vi": True}
        for p in group["parts"]], "next": group["parts"][0]["id"], "duration": 3600.0})
    assert "2/2 đã dựng" in row.badge.text() and row._joinable
    join = next(a for a in row._menu.actions() if a.text() == "Ghép video đầy đủ")
    assert join.isEnabled()
    join.trigger()
    assert joined == [group["id"]]
    rp.shutdown()


def test_long_durations_read_as_hours():
    from desktop.widgets.recent_projects import duration_label

    assert duration_label(10800) == "3:00:00" and duration_label(328.7) == "05:28"


def test_queue_summary_counts_outcomes_not_items(qapp, tmp_path, monkeypatch):
    """Review: after Hủy or a failure the queue still announced 'Xong hàng đợi: N video'."""
    from app.config import settings
    from desktop import main as main_mod

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    win = main_mod.MainWindow()
    seen = []
    monkeypatch.setattr(win, "_busy", lambda: False)
    monkeypatch.setattr(win, "_notify", lambda *a: seen.append(a))
    monkeypatch.setattr(win, "_run", lambda method, opts, conns, job=None: None)
    win._queue_total = 3
    win._queue_ok, win._queue_failed = 1, 1
    win._run_next_queued()
    assert "1 mục tới bước duyệt" in win.status.text.text() and "1 mục lỗi" in win.status.text.text()
    win._queue_total, win._queue_ok, win._queue_cancelled = 2, 1, True
    seen.clear()
    win._run_next_queued()
    assert "Đã dừng hàng đợi" in win.status.text.text() and seen == []


def test_a_part_renders_in_a_second_slot_while_the_queue_runs(qapp, tmp_path, monkeypatch):
    """Review part 1 *and render it* while part 2 is still being transcribed: the queue
    holds the main worker slot, so renders/joins get their own."""
    import time

    from PySide6.QtWidgets import QApplication

    from app.config import settings
    from app.models import Job
    from desktop import main as main_mod

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    win = main_mod.MainWindow()
    assert win._render_slot() == "main"                    # idle → the usual slot
    monkeypatch.setattr(win, "_busy", lambda: True)
    assert win._render_slot() is None                      # a gate run owns self.job
    win._queue_job = Job(id="q", work_dir=tmp_path / "q")
    assert win._render_slot() == "render"                  # the queue → second slot

    fails = []
    win._set_render_locked(True)
    win._run("run_join", {"_group": "missing"}, {"failed": fails.append},
             job=Job(id="g", work_dir=tmp_path / "g"), slot="render")
    for _ in range(200):
        QApplication.processEvents()
        if fails and not win._render_busy():
            break
        time.sleep(0.02)
    for _ in range(10):
        QApplication.processEvents()
    assert fails and "group.json" in fails[0]
    assert win.render.render_btn.isEnabled()               # the second slot unlocked itself
    assert win._thread is None                             # the main slot was never touched


def test_group_row_never_points_at_a_deleted_part(tmp_path, monkeypatch):
    import json

    from app.config import settings
    from app.models import Job
    from desktop import store

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "group.json").write_text(json.dumps(
        {"id": "g", "parts": [{"id": "gone", "index": 1}, {"id": "here", "index": 2}]}),
        encoding="utf-8")
    j = Job(id="here", work_dir=tmp_path / "here")
    j.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    store.save_job(j, {}, "await_edit")
    assert store.group_summary("g")["next"] == "here"
