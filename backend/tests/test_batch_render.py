"""Dựng hàng loạt: render settings are saved and rebuilt as a template, only approved
projects are offered (edited-after-approval ones are skipped), the dialog / render page
queue them, and the main window renders the queue one by one in the second slot while
other projects stay openable. Real ffmpeg on tiny clips with the no-dub mode (no TTS,
nothing uploaded); storage is a tmp dir."""
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
from app.pipeline import approval, batch  # noqa: E402

SRT = "1\n00:00:00,300 --> 00:00:01,200\nxin chào\n"


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


def make_job(storage: Path, jid: str, title: str, *, approve=True, output=False,
             real_source=False, style: dict | None = None) -> Job:
    from desktop import store

    j = Job(id=jid, work_dir=storage / jid, url=f"https://v.douyin.com/{jid}/")
    j.metadata = {"title": title, "duration": 2.0, "platform": "douyin"}
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                              encoding="utf-8")
    j.vi_srt.write_text(SRT, encoding="utf-8")
    j.cn_srt.write_text(SRT.replace("xin chào", "你好"), encoding="utf-8")
    if style is not None:
        (j.work_dir / "style.json").write_text(json.dumps(style), encoding="utf-8")
    if real_source:
        subprocess.run([_ffmpeg(), "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(j.source_video)], check=True)
    else:
        j.source_video.write_bytes(b"s")
    if approve:
        approval.approve(j)
    if output:
        j.output_video.write_bytes(b"o")
        stamp = (j.work_dir / "approved.json").stat().st_mtime if approve else 0
        os.utime(j.output_video, (stamp + 5, stamp + 5))
    store.save_job(j, {"url": j.url, "tts": "edge"}, "await_edit")
    return j


# --- app/pipeline/batch.py ------------------------------------------------------------

def test_saved_settings_drop_per_run_fields(tmp_path):
    batch.save_opts(tmp_path, {"voice": "vi-VN-HoaiMyNeural", "burn_cues": [1], "_x": 1,
                               "res": "1080"})
    assert batch.template_from(tmp_path) == {"voice": "vi-VN-HoaiMyNeural", "res": "1080"}
    batch.save_opts(tmp_path, {"voice": "other", "preview": True})     # the quick proof
    assert batch.template_from(tmp_path)["voice"] == "vi-VN-HoaiMyNeural"


def test_template_rebuilt_for_projects_rendered_before(tmp_path):
    (tmp_path / "render.json").write_text(json.dumps(
        {"cover_mode": "glass", "frame": "tiktok", "audio_mode": "duck", "res": "720",
         "preset": "balanced"}), encoding="utf-8")
    (tmp_path / "voice.json").write_text(json.dumps(
        {"voice": "vi-VN-NamMinhNeural", "rate": 1.1, "audio_mode": "keep_music"}),
        encoding="utf-8")
    (tmp_path / "style.json").write_text(json.dumps({"font": "Noto Sans", "size": 22,
                                                     "style": {"primary": "#fff"}}),
                                         encoding="utf-8")
    t = batch.template_from(tmp_path)
    assert t["voice"] == "vi-VN-NamMinhNeural" and t["rate"] == "+10%"
    assert t["audio_mode"] == "keep_music" and t["frame"] == "tiktok" and t["res"] == "720"
    assert t["font"] == "Noto Sans" and t["style"] == {"primary": "#fff"}
    assert batch.describe(t) == "NamMinh · +10% · Giữ nhạc nền & hiệu ứng · 720p · Cân bằng · " \
                                "khung 9:16"
    assert batch.template_from(tmp_path / "nothing") is None


def test_each_project_keeps_its_own_subtitle_look(tmp_path):
    t = {"font": "A", "size": 20, "style": {"x": 1}, "audio_mode": "mix", "burn_cues": []}
    assert batch.opts_for(tmp_path, t) == {"font": "A", "size": 20, "style": {"x": 1},
                                           "audio_mode": "duck"}
    (tmp_path / "style.json").write_text(json.dumps({"font": "B", "size": 30, "style": {"y": 2}}),
                                         encoding="utf-8")
    o = batch.opts_for(tmp_path, t)
    assert (o["font"], o["size"], o["style"]) == ("B", 30, {"y": 2})


def test_only_approved_unrendered_projects_need_a_render(storage):
    a = make_job(storage, "aaaaaaaaaaa1", "第1集")
    b = make_job(storage, "aaaaaaaaaaa2", "第2集", output=True)
    c = make_job(storage, "aaaaaaaaaaa3", "第3集", approve=False)
    d = make_job(storage, "aaaaaaaaaaa4", "第4集")
    d.vi_srt.write_text(SRT.replace("xin chào", "đã sửa"), encoding="utf-8")   # after approval
    assert batch.render_state(a.work_dir) == "new"
    assert batch.render_state(b.work_dir) is None
    os.utime(b.output_video, (1, 1))                          # rendered before the approval
    assert batch.render_state(b.work_dir) == "stale"
    assert batch.render_state(c.work_dir) is None and batch.render_state(d.work_dir) is None
    assert batch.render_state(storage / "nope") is None and not (storage / "nope").exists()

    from desktop import store

    ready, waiting = store.batch_candidates()
    assert {r["id"]: r["state"] for r in ready} == {"aaaaaaaaaaa1": "new", "aaaaaaaaaaa2": "stale"}
    assert waiting == 2
    batch.save_opts(b.work_dir, {"voice": "v", "res": "720"})
    assert [t["id"] for t in store.batch_templates()] == ["aaaaaaaaaaa2"]


# --- dialog / render page -------------------------------------------------------------

def test_dialog_lists_in_episode_order_and_returns_the_pick(qapp):
    from desktop.widgets.batch_dialog import ROLE, BatchDialog

    rows = [{"id": "b", "title": "第2集", "state": "new", "duration": 30},
            {"id": "a", "title": "第1集", "state": "stale", "duration": 0},
            {"id": "q", "title": "第3集", "state": "new"}]
    tpl = [{"id": "t", "title": "Tập mẫu", "opts": {"voice": "vi-VN-HoaiMyNeural", "res": "1080",
                                                     "preset": "sharp", "audio_mode": "duck"}}]
    dlg = BatchDialog(None, rows, tpl, preselect={"b"}, waiting=4, queued={"q"})
    assert [dlg.list.item(i).data(ROLE) for i in range(3)] == ["a", "b", "q"]
    assert dlg.picked() == ["b"] and dlg.ok_btn.text() == "Dựng 1 video"
    assert "HoaiMy" in dlg.desc.text() and "1080p · Nét" in dlg.desc.text()
    from PySide6.QtCore import Qt

    dlg._set_all(Qt.Checked)
    assert dlg.spec() == {"ids": ["a", "b"], "template": "t"}      # "q" is already queued
    dlg.close()
    empty = BatchDialog(None, rows, [])
    assert not empty.ok_btn.isEnabled() and "dựng một video" in empty.desc.text()
    empty.close()


def test_render_page_queues_instead_of_rendering_during_a_batch(qapp):
    from desktop.views.render_view import RenderView

    v = RenderView()
    v.set_approved(True)
    later, now = [], []
    v.renderLaterRequested.connect(later.append)
    v.renderRequested.connect(now.append)
    assert v.later_btn.isEnabled()
    v.later_btn.click()
    assert len(later) == 1 and not now and "preview" not in later[0]
    v.set_batch_active(True)
    assert v.render_btn.text() == "Thêm vào hàng dựng" and not v.quick_btn.isEnabled()
    v.render_btn.click()
    assert len(later) == 2 and not now
    v.set_batch_active(False)
    assert v.render_btn.text() in ("Dựng video", "Dựng lại") and v.quick_btn.isEnabled()
    v.shutdown()


# --- the main window -----------------------------------------------------------------------

def _wait(cond, n: int = 3000) -> None:
    import time

    from PySide6.QtWidgets import QApplication

    for _ in range(n):
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)


@needs_ffmpeg
def test_main_window_renders_the_queue_and_keeps_other_projects_open(qapp, storage):
    from desktop import main as main_mod
    from desktop import store

    tpl = make_job(storage, "aaaaaaaaaaa0", "第0集", output=True)
    batch.save_opts(tpl.work_dir, {"audio_mode": "original", "frame": "source", "res": "source",
                                   "preset": "light", "cover_mode": None, "font": "Be Vietnam Pro",
                                   "size": 20, "credit_overlay": False, "ai_label": False})
    for k in (1, 2):
        make_job(storage, f"aaaaaaaaaaa{k}", f"第{k}集", real_source=True)
    make_job(storage, "aaaaaaaaaaa3", "第3集", approve=False, real_source=True)  # in review
    win = main_mod.MainWindow()
    win._notify = lambda *a, **k: None
    win._start_batch({"ids": ["aaaaaaaaaaa1", "aaaaaaaaaaa2"], "template": "aaaaaaaaaaa0"})
    assert win._batch_active() and win.render._batch
    assert win._library_guard(store.library_item("aaaaaaaaaaa2")) in ("đang chờ dựng", "đang dựng")
    win._resume("aaaaaaaaaaa3")                                  # review the next one meanwhile
    assert win.job is not None and win.job.id == "aaaaaaaaaaa3"
    later = {"audio_mode": "original", "preset": "light", "frame": "source", "res": "source"}
    win._render_later(later)                                     # not approved yet: refused
    assert "aaaaaaaaaaa3" not in win._batch_ids()
    win.editor.save_now()
    approval.approve(win.job)                                    # "Duyệt" → then "Dựng sau"
    win._render_later(later)
    assert "aaaaaaaaaaa3" in win._batch_ids()
    _wait(lambda: not win.render._batch and not win._any_busy())
    for k in (1, 2, 3):
        out = storage / f"aaaaaaaaaaa{k}" / "output.mp4"
        assert out.exists() and out.stat().st_size > 1000, k
        assert batch.render_state(out.parent) is None
    assert len(win._batch_ok) == 3 and not win._batch_failed
    assert win.editor.isEnabled()                                # unlocked after its render
    assert batch.template_from(storage / "aaaaaaaaaaa3")["preset"] == "light"
    assert not win.render._batch and "Đã dựng 3/3" in win.library.cb_msg.text()
    win.library.shutdown()


@needs_ffmpeg
def test_a_project_edited_after_approval_is_skipped(qapp, storage):
    from desktop import main as main_mod

    tpl = make_job(storage, "aaaaaaaaaaa0", "第0集", output=True)
    batch.save_opts(tpl.work_dir, {"audio_mode": "original", "preset": "light"})
    j = make_job(storage, "aaaaaaaaaaa1", "第1集", real_source=True)
    win = main_mod.MainWindow()
    win._notify = lambda *a, **k: None
    win._start_batch({"ids": ["aaaaaaaaaaa1"], "template": "aaaaaaaaaaa0"})
    j.vi_srt.write_text(SRT.replace("xin chào", "sửa sau khi duyệt"), encoding="utf-8")
    _wait(lambda: not win.render._batch and not win._any_busy())
    assert not j.output_video.exists()
    assert win._batch_failed and "chưa được duyệt" in win._batch_failed[0]
    win.library.shutdown()


def test_describe_mentions_the_watermark():
    opts = {"audio_mode": "original", "res": "720", "preset": "light",
            "watermark": {"on": True, "text": "Vietsub: Kênh Của Tôi"}}
    assert batch.describe(opts).endswith("watermark “Vietsub: Kênh Của Tôi”")
    assert "watermark" not in batch.describe({**opts, "watermark": {"on": False, "text": "x"}})
