"""F-1 / F-2 / F-3: credit the source, label the AI voice, record the right to use."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.models import Job
from app.pipeline import disclosure


@pytest.fixture()
def job(tmp_path):
    j = Job(id="d", work_dir=tmp_path / "d", url="https://v.douyin.com/abc/")
    (j.work_dir / "metadata.json").write_text(json.dumps({
        "title": "远方太寒冷了 #翻唱", "uploader": "菜菜颖", "uploader_id": "caicaiying",
        "platform": "douyin", "webpage_url": "https://www.douyin.com/video/7688721076042876378",
        "upload_date": "20261001"}, ensure_ascii=False), encoding="utf-8")
    return j


def test_credit_line_and_overlays(job):
    assert disclosure.credit_line(job) == "Nguồn: 菜菜颖 · Douyin"
    tags = disclosure.overlays(job, credit=True, ai_voice=True)
    assert [(t["align"], t["text"]) for t in tags] == [(7, "Nguồn: 菜菜颖 · Douyin"),
                                                        (9, "Lồng tiếng AI")]
    assert disclosure.overlays(job, credit=False, ai_voice=False) == []
    local = Job(id="l", work_dir=job.work_dir.parent / "l")
    assert disclosure.credit_line(local) == ""                  # a file from disk: nothing known
    job.metadata = {"uploader": "一个名字特别特别特别长的美食博主小王同学的频道官方账号", "platform": "douyin"}
    line = disclosure.credit_line(job)
    assert line.endswith("… · Douyin") and len(line) <= len("Nguồn: ") + 22 + len(" · Douyin")


def test_post_description_names_the_source_and_the_ai_voice(job):
    text = disclosure.post_description(job, ai_voice=True)
    assert "菜菜颖 (Douyin), đăng 01/10/2026 — https://www.douyin.com/video/7688721076042876378" in text
    assert "giọng đọc AI" in text and "cho phép" not in text
    disclosure.save_rights(job, "permission", note="email 05/10")
    text = disclosure.post_description(job, ai_voice=False)
    assert "giọng đọc AI" not in text and "sự cho phép của tác giả. (email 05/10)" in text


def test_rights_are_saved_with_the_proof_copied_in(job, tmp_path):
    assert disclosure.load_rights(job)["basis"] == "unknown"
    proof = tmp_path / "permission.png"
    proof.write_bytes(b"png")
    data = disclosure.save_rights(job, "permission", proof=proof)
    assert data["proof"] == "rights/permission.png"
    assert (job.work_dir / "rights" / "permission.png").read_bytes() == b"png"
    assert disclosure.load_rights(job)["basis"] == "permission"
    items = disclosure.checklist(ai_voice=True, credit=True, rights="unknown")
    assert items[0][0] is False and items[1][0] is True and "Có" in items[2][1]


def test_render_burns_the_corner_notes(job, monkeypatch):
    from app.pipeline import assemble

    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    monkeypatch.setattr(assemble, "_build_render", lambda *a, **k: ["ffmpeg"])
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda *a, **k: None)
    assemble.assemble(job, [], overlays=disclosure.overlays(job, credit=True, ai_voice=True))
    ass = job.subs_ass.read_text(encoding="utf-8")
    assert "Style: Tag," in ass and "{\\an9}Lồng tiếng AI" in ass and "{\\an7}Nguồn: 菜菜颖" in ass


def test_render_page_sends_the_choices_and_records_rights(job):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from desktop.views.render_view import RenderView
    from desktop.widgets.dialogs import PostDialog

    rv = RenderView()
    rv.set_job(job, cues=1)
    assert rv.credit_cb.isEnabled() and "菜菜颖" in rv.credit_cb.toolTip()
    assert "Chưa ghi quyền" in rv.rights_note.text()
    got = []
    rv.renderRequested.connect(got.append)
    rv._emit_render()
    assert got[-1]["credit_overlay"] is True and got[-1]["ai_label"] is True
    rv.mode_cards["original"].setChecked(True)                   # subtitles only: no AI voice
    rv._emit_render()
    assert got[-1]["ai_label"] is False and not rv.ai_label_cb.isEnabled()
    rv.rights.setCurrentIndex(rv.rights.findData("own"))
    assert disclosure.load_rights(job)["basis"] == "own" and "Đã ghi" in rv.rights_note.text()
    dlg = PostDialog(rv, text=disclosure.post_description(job, ai_voice=False),
                     checklist=disclosure.checklist(ai_voice=False, credit=True, rights="own"))
    assert "菜菜颖" in dlg.text.toPlainText()
