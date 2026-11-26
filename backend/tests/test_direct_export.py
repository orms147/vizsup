from __future__ import annotations

import json
import subprocess

import pytest

from app.ffmpegutil import ffmpeg_bin
from app.models import Cue, Job
from app.pipeline import approval, direct_export
from app.pipeline.srt import parse_srt, write_srt


def test_sealed_revision_is_approved_once_and_edits_cannot_be_auto_approved(tmp_path):
    job = Job("one", tmp_path)
    write_srt([Cue(1, 0, 1, "Bản dịch")], job.vi_srt)
    (tmp_path / direct_export.READY).write_text(approval.revision(job), encoding="utf-8")
    direct_export.authorize(job)
    approved = (tmp_path / "approved.json").read_bytes()
    direct_export.authorize(job)
    assert approval.is_auto_approved(job)
    assert (tmp_path / "approved.json").read_bytes() == approved

    write_srt([Cue(1, 0, 1, "Bản sửa")], job.vi_srt)
    assert not approval.is_auto_approved(job)
    with pytest.raises(RuntimeError, match="đã thay đổi"):
        direct_export.authorize(job)
    approval.approve(job)
    assert approval.is_approved(job)
    assert not approval.is_auto_approved(job)


def test_delivery_has_audio_description_and_survives_cleanup_and_duplicate_title(tmp_path):
    opts = {"folder": str(tmp_path / "dest"), "frame": "source", "frames": False}
    job = Job("one", tmp_path / "one", url="https://example.invalid/episode",
              metadata={"title": "Tên phim - Tập 1"})
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=s=96x64:d=1",
                    "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:v", "libx264", "-shortest", str(job.output_video)],
                   check=True, capture_output=True)
    (job.work_dir / "context_brief.json").write_text(json.dumps(
        {"brief": "Thể loại & nội dung:\nHai người đi tìm kho báu.\nNgười nói & xưng hô:\nTôi"},
        ensure_ascii=False), encoding="utf-8")
    kwargs = {"log": lambda _: None, "cancel": lambda: False}
    dest = direct_export.deliver(job, job.output_video, opts, **kwargs)
    found = dest / f"{dest.name}.mp4"
    assert found.samefile(job.output_video)
    assert {p.name for p in dest.iterdir()} == {found.name, "Mô tả.txt"}
    note = (dest / "Mô tả.txt").read_text(encoding="utf-8")
    assert "Hai người đi tìm kho báu." in note
    assert "Người nói & xưng hô" not in note
    assert job.url in note
    job.output_video.unlink()
    assert direct_export.deliver(job, job.output_video, opts, **kwargs) == dest
    other = Job("two", tmp_path / "two", metadata=job.metadata)
    assert direct_export.deliver(other, found, opts, **kwargs) != dest


def test_repair_only_selected_rows_and_preserves_timing(tmp_path, monkeypatch):
    job = Job("repair", tmp_path)
    source = [Cue(1, 0, 2, "一"), Cue(2, 2, 6, "二")]
    write_srt(source, job.cn_srt)
    write_srt([Cue(1, 0, 2, "还好"), Cue(2, 2, 6, "Sẵn sàng.")], job.vi_srt)

    def rewrite(items, **kwargs):
        assert [item["zh"] for item in items] == ["一"]
        return ["May quá!"]

    monkeypatch.setattr(direct_export.translate, "retranslate_lines", rewrite)
    direct_export.repair(job, {}, {}, log=lambda _: None, cancel=lambda: False)
    assert [(c.start, c.end, c.text) for c in parse_srt(job.vi_srt)] == [
        (0, 2, "May quá!"), (2, 6, "Sẵn sàng.")]


def test_repair_pairs_lines_by_time_when_counts_differ(tmp_path, monkeypatch):
    job = Job("repair-merged", tmp_path)
    write_srt([Cue(1, 0, 1, "一"), Cue(2, 1, 2, "二"), Cue(3, 2, 6, "三")], job.cn_srt)
    write_srt([Cue(1, 0, 2, "Chữ 一二 còn sót"), Cue(2, 2, 6, "Sẵn sàng.")], job.vi_srt)
    seen = []

    def rewrite(items, **kwargs):
        seen.extend(item["zh"] for item in items)
        return ["Một hai."]

    monkeypatch.setattr(direct_export.translate, "retranslate_lines", rewrite)
    direct_export.repair(job, {}, {}, log=lambda _: None, cancel=lambda: False)
    assert seen == ["一 二"]
    assert [c.text for c in parse_srt(job.vi_srt)] == ["Một hai.", "Sẵn sàng."]


def test_direct_worker_renders_an_approved_video_with_ffmpeg(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from desktop.worker import PipelineWorker
    from app.pipeline import tts

    job = Job("episode", tmp_path / "episode", metadata={"title": "Tập 1"})
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=s=96x64:d=2",
                    "-f", "lavfi", "-i", "sine=duration=2",
                    "-c:v", "libx264", "-shortest", str(job.source_video)],
                   check=True, capture_output=True)
    segment = job.tts_dir / "seg_0001.mp3"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "sine=frequency=700:duration=0.6", str(segment)],
                   check=True, capture_output=True)
    worker = PipelineWorker(job, {direct_export.REQUEST: {"folder": str(tmp_path / "delivered")}})
    failures, results = [], []
    worker.failed.connect(failures.append)
    worker.render_done.connect(results.append)

    def gate():
        write_srt([Cue(1, 0, 2, "是。")], job.cn_srt)
        write_srt([Cue(1, 0, 2, "Đúng vậy.")], job.vi_srt)

    def synthesize(current, **_kwargs):
        assert approval.is_auto_approved(current)
        return [(parse_srt(current.vi_srt)[0], segment)]

    monkeypatch.setattr(worker, "_gate_body", gate)
    monkeypatch.setattr(tts, "synthesize", synthesize)
    worker.run_direct()
    assert not failures
    assert results == [str(tmp_path / "delivered" / "Tập 1")]
    assert direct_export.read_result(job) is not None


def test_direct_worker_joins_parts_and_retries_delivery_without_redoing_render(
        tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from desktop import store
    from desktop.worker import PipelineWorker
    from app.config import settings
    from app.pipeline import batch, render, split

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    parent = Job("group", tmp_path / "group")
    parts = [Job(f"part{i}", tmp_path / f"part{i}") for i in (1, 2)]
    group = {"parts": [{"id": p.id, "index": i} for i, p in enumerate(parts, 1)]}
    (parent.work_dir / split.GROUP_FILE).write_text(json.dumps(group), encoding="utf-8")
    for p in parts:
        p.source_video.touch()
        store.save_job(p, {})
    request = {"folder": str(tmp_path / "delivery"), "frame": "tiktok"}
    worker = PipelineWorker(parent, {direct_export.REQUEST: request})
    done, failed, actions = [], [], []
    worker.render_done.connect(done.append)
    worker.failed.connect(failed.append)

    def gate():
        job = worker.job
        actions.append(("translate", job.id))
        write_srt([Cue(1, 0, 2, "是")], job.cn_srt)
        write_srt([Cue(1, 0, 2, "Đúng vậy.")], job.vi_srt)

    def render_one(job, opts, **kwargs):
        assert approval.is_auto_approved(job)
        assert opts["frame"] == "tiktok"
        assert opts["thumbnail"] is False
        actions.append(("render", job.id))
        job.output_video.touch()

    def join(*args, **kwargs):
        actions.append(("join", parent.id))
        parent.output_video.touch()

    def deliver(*args, **kwargs):
        actions.append(("deliver", parent.id))
        if not failed:
            raise OSError("Thư mục chưa ghi được")
        return tmp_path / "delivery"

    monkeypatch.setattr(worker, "_gate_body", gate)
    monkeypatch.setattr(worker, "_resilient", lambda fn: fn())
    monkeypatch.setattr(render, "render_job", render_one)
    monkeypatch.setattr(batch, "render_state", lambda d: None)
    monkeypatch.setattr("desktop.worker._playable", lambda p: p.exists())
    monkeypatch.setattr(split, "joined_is_current", lambda *a: parent.output_video.exists())
    monkeypatch.setattr(split, "join", join)
    monkeypatch.setattr(direct_export, "deliver", deliver)
    worker.run_direct()
    worker.run_direct()

    assert len(failed) == 1
    assert done == [str(tmp_path / "delivery")]
    assert actions == [("translate", "part1"), ("render", "part1"), ("translate", "part2"),
                       ("render", "part2"), ("join", "group"), ("deliver", "group"),
                       ("deliver", "group")]
