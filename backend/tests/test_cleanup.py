"""«Dọn dung lượng» (app/pipeline/cleanup.py): only finished projects, the three kinds, a posted
video removed still counts as rendered (never re-rendered), and the library wiring. Storage is a
tmp dir; the Recycle Bin call is mocked."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from app.models import Job

SRT = "1\n00:00:00,000 --> 00:00:01,000\nxin chào\n"
OLD = time.time() - 10 * 86400


def make(storage: Path, jid: str, *, url: str | None = "https://v.douyin.com/x/",
         rendered: bool = True, approved: bool = True, age: float = OLD) -> Job:
    from app.pipeline import approval

    d = storage / jid
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({"title": jid, "url": url}), encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": jid}), encoding="utf-8")
    (d / "vi.srt").write_text(SRT, encoding="utf-8")
    (d / "source.mp4").write_bytes(b"s" * 100)
    for sub in ("tts", "dub"):
        (d / sub).mkdir()
        (d / sub / "a.bin").write_bytes(b"t" * 10)
    (d / "preview.mp4").write_bytes(b"p" * 7)
    job = Job(id=jid, work_dir=d)
    if approved:
        rev = approval.approve(job)
        if rendered:
            (d / "output.mp4").write_bytes(b"o" * 1000)
            (d / "output.vertical.mp4").write_bytes(b"v" * 500)
            (d / "output.srt").write_text(SRT, encoding="utf-8")
            (d / "render.json").write_text(json.dumps({"revision": rev}), encoding="utf-8")
            for f in ("output.mp4", "render.json"):
                os.utime(d / f, (age, age))
    return job


def posts_for(jid: str, *statuses: str) -> list[dict]:
    return [{"id": f"{jid}:{i}", "job": jid, "kind": "long", "platform": f"p{i}", "status": s}
            for i, s in enumerate(statuses)]


def test_posted_needs_every_post_on_a_platform():
    from app.pipeline.cleanup import posted

    assert not posted("a", [])
    assert posted("a", posts_for("a", "done", "scheduled", "private"))
    assert not posted("a", posts_for("a", "done", "pending"))
    assert not posted("a", posts_for("a", "failed"))
    assert not posted("a", posts_for("b", "done"))


def test_only_finished_old_projects_are_planned(tmp_path):
    from app.pipeline import cleanup

    make(tmp_path, "aaaaaaaaaaa1")                               # finished, old
    make(tmp_path, "aaaaaaaaaaa2", age=time.time())              # rendered today
    make(tmp_path, "aaaaaaaaaaa3", rendered=False)               # approved, not rendered
    make(tmp_path, "aaaaaaaaaaa4", approved=False)               # at the gate
    make(tmp_path, "aaaaaaaaaaa5", url=None)                     # local file gone: source stays
    rows = {r["id"]: r for r in cleanup.scan(tmp_path, posts=[])}
    assert set(rows) == {"aaaaaaaaaaa1", "aaaaaaaaaaa5"}
    r = rows["aaaaaaaaaaa1"]
    assert {p.name for p in r["cache"]} == {"tts", "dub", "preview.mp4"}
    assert [p.name for p in r["source"]] == ["source.mp4"]
    assert r["output"] == []                                     # nothing posted yet
    assert r["bytes"] == {"cache": 27, "source": 100, "output": 0}
    assert rows["aaaaaaaaaaa5"]["source"] == []


def test_edited_after_render_keeps_everything(tmp_path):
    from app.pipeline import approval, cleanup

    job = make(tmp_path, "bbbbbbbbbbb1")
    job.vi_srt.write_text(SRT.replace("xin chào", "chào bạn"), encoding="utf-8")
    approval.approve(job)                                        # a render is owed now
    assert cleanup.scan(tmp_path, posts=[]) == []


def test_posted_video_removed_still_counts_as_rendered(tmp_path):
    from app.pipeline import approval, batch, cleanup, batch_import

    job = make(tmp_path, "ccccccccccc1")
    d = job.work_dir
    [row] = cleanup.scan(tmp_path, posts=posts_for(job.id, "done"))
    assert {p.name for p in row["output"]} == {"output.mp4", "output.vertical.mp4"}  # .srt kept
    for p in row["output"]:
        p.unlink()
    assert batch.render_state(d) == "new"                        # without the mark: re-render!
    cleanup.mark_removed(d, row["output"])
    assert cleanup.archived(d) and cleanup.rendered(d)
    assert batch.render_state(d) is None                         # never offered again
    from app.config import settings
    old = settings.vizsup_storage_dir
    settings.vizsup_storage_dir = tmp_path
    try:
        assert batch_import.project_state(job.id) == "rendered"
    finally:
        settings.vizsup_storage_dir = old
    # still offered for the cache it has left, not for the video again
    [again] = cleanup.scan(tmp_path, posts=posts_for(job.id, "done"))
    assert again["output"] == []
    # edited + approved again: a normal "new" render
    job.vi_srt.write_text(SRT.replace("xin chào", "chào bạn"), encoding="utf-8")
    approval.approve(job)
    assert not cleanup.archived(d)
    assert batch.render_state(d) == "new"


def test_mark_needs_the_output_itself(tmp_path):
    from app.pipeline import cleanup

    job = make(tmp_path, "ddddddddddd1")
    cleanup.mark_removed(job.work_dir, [job.work_dir / "tts"])
    assert not (job.work_dir / cleanup.MARK).exists()


def test_paths_for_and_totals():
    from app.pipeline import cleanup

    rows = [{"cache": [Path("a")], "source": [Path("b")], "output": [Path("c")],
             "bytes": {"cache": 1, "source": 2, "output": 4}}]
    assert cleanup.totals(rows) == {"cache": 1, "source": 2, "output": 4}
    assert cleanup.paths_for(rows, {"cache", "output"}) == [Path("a"), Path("c")]


# --- the library wiring (PySide6) --------------------------------------------------------

@pytest.fixture()
def lib(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from PySide6.QtWidgets import QApplication

    from app.config import settings
    from desktop import trash

    QApplication.instance() or QApplication([])
    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)

    def fake(paths):
        for p in paths:
            p = Path(p)
            assert "storage" in p.parts
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    monkeypatch.setattr(trash, "move_to_trash", fake)
    from desktop.views.library_view import LibraryView

    v = LibraryView()
    v._inform = lambda *_a: None
    yield base, v
    v.close()


def test_library_clean_moves_chosen_kinds_and_marks(lib):
    from app.pipeline import batch, cleanup
    from desktop import store

    base, v = lib
    job = make(base, "eeeeeeeeeee1")
    busy = make(base, "eeeeeeeeeee2")
    v.set_guard(lambda it: "đang mở" if it["id"] == busy.id else None)
    rows = cleanup.scan(base, posts=posts_for(job.id, "done") + posts_for(busy.id, "done"))
    seen = {}

    def confirm(rs):
        seen["ids"] = [r["id"] for r in rs]
        return cleanup.paths_for(rs, {"cache", "output"})

    v._clean_confirm = confirm
    assert v._clean_dialog(rows)
    assert seen["ids"] == [job.id]                       # the open project keeps its files
    d = job.work_dir
    assert not (d / "tts").exists() and not (d / "output.mp4").exists()
    assert (d / "source.mp4").exists() and (d / "vi.srt").exists() and (d / "output.srt").exists()
    assert (busy.work_dir / "output.mp4").exists()
    assert batch.render_state(d) is None
    row = next(it for it in store.library_items() if it["id"] == job.id)
    assert row["status"] == "rendered" and row["archived"] and not row["has_output"]


def test_cleanup_dialog_ticks(lib):
    from app.pipeline import cleanup
    from desktop.widgets.cleanup_dialog import CleanupDialog

    base, _v = lib
    job = make(base, "fffffffffff1")
    rows = cleanup.scan(base, posts=[])
    dlg = CleanupDialog(None, rows, base)
    assert dlg.kinds() == {"cache", "source"}            # nothing posted: no video tick
    assert not dlg.checks["output"].isEnabled()
    assert {p.name for p in dlg.paths()} == {"tts", "dub", "preview.mp4", "source.mp4"}
    dlg.checks["source"].setChecked(False)
    assert job.work_dir / "source.mp4" not in dlg.paths()
    dlg.close()
