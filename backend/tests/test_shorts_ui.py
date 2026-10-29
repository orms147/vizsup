"""«Cắt Short» (S3), desktop: the dialog, the library row (approved episodes only, a Short listed right
under its episode), the whole flow into a new project that opens in the editor UNAPPROVED, and the
render page starting such a project with the Short preset. Tmp storage only; AI is faked."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402
from app.models import Cue, Job  # noqa: E402
from app.pipeline import approval, clipshort  # noqa: E402
from app.pipeline.srt import write_srt  # noqa: E402
from test_library import _wait  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


def talk(n=60):
    out = []
    for i in range(n):
        txt = f"Tại sao cô ấy làm vậy {i}?" if i % 6 == 0 else f"Câu thoại số {i} về chuyện nhà"
        out.append(Cue(i + 1, i * 2.5, i * 2.5 + 2.0, txt))
    return out


def episode(base: Path, jid="ep0000000008", *, approved=True, source=True, title="第8集 | 《末日校车》08 开头"):
    j = Job(id=jid, work_dir=base / jid, url="https://v.douyin.com/x/")
    j.metadata = {"title": title, "platform": "douyin", "duration": 150.0}
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False), encoding="utf-8")
    (j.work_dir / "job.json").write_text(json.dumps({"id": jid, "title": title, "opts": {}}), encoding="utf-8")
    cs = talk()
    write_srt(cs, j.vi_srt)
    write_srt([Cue(c.index, c.start, c.end, f"中文{c.index}") for c in cs], j.cn_srt)
    (j.work_dir / "output.mp4").write_bytes(b"o" * 5)
    if source:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=blue:s=320x180:r=5:d=150", "-f", "lavfi", "-i",
                        "sine=f=220:r=48000:d=150", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(j.source_video)], check=True)
    if approved:
        approval.approve(j)
    return j


# --- the dialog --------------------------------------------------------------------------------

def test_dialog_picks_edits_and_gates(qapp):
    from desktop.widgets.short_dialog import ShortDialog, fmt, parse

    assert parse("1:23.5") == 83.5 and parse("83,5") == 83.5 and parse("0:05") == 5.0
    assert parse("") is None and parse("x") is None and parse("-3") is None
    assert fmt(83.5) == "1:23.5" and fmt(5) == "0:05.0"
    cs = talk()
    cands = clipshort.heuristic(cs)
    d = ShortDialog(None, "Tập 8", cs, 150.0, cands)
    assert d.list.count() == len(cands) and not d.empty.isVisibleTo(d)
    assert d.hook.text() == cands[0].hook and d.ok_btn.isEnabled()
    spec = d.spec()
    assert abs(spec["start"] - cands[0].start) < 0.06 and spec["hook"] == cands[0].hook
    d.start.setText("0:10")
    d.end.setText("0:12")                                         # one line: under the 5 s minimum
    assert not d.ok_btn.isEnabled() and "5" in d.length.text()
    d.end.setText("0:50")
    assert d.ok_btn.isEnabled() and "giây" in d.length.text()
    assert "câu" in d.span.text()                                 # first / last line shown
    d.end.setText("zz")
    assert d.spec() is None and not d.ok_btn.isEnabled()
    d.start.setText("500")
    d.end.setText("540")                                          # no line there
    assert not d.ok_btn.isEnabled() and "không có câu" in d.length.text()
    d.set_candidates([])
    assert d.empty.isVisibleTo(d)
    got = []
    d.aiRequested.connect(lambda: got.append(1))
    d.ai_btn.click()
    assert got == [1]


# --- the library -------------------------------------------------------------------------------

def test_short_rows_sit_under_their_episode_whatever_the_order():
    from desktop.views.library_view import shorts_under_parents

    items = [{"id": "s1", "short": {"parent": "ep"}}, {"id": "other"}, {"id": "ep"},
             {"id": "s2", "short": {"parent": "ep"}}, {"id": "orphan", "short": {"parent": "gone"}}]
    assert [i["id"] for i in shorts_under_parents(items)] == ["other", "ep", "s1", "s2", "orphan"]
    assert shorts_under_parents([]) == []


@needs_ffmpeg
def test_row_menu_rules_and_the_whole_flow(qapp, storage, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from desktop.views.library_view import LibraryView

    ok = episode(storage, "ep0000000008")
    unapproved = episode(storage, "ep0000000009", approved=False, title="第9集 | 《末日校车》09 x")
    nosrc = episode(storage, "ep0000000007", source=False, title="第7集 | 《末日校车》07 x")
    v = LibraryView()
    v.resize(1100, 800)
    v.show()
    v.refresh()
    _wait(lambda: len(v._details) == 3)
    acts = {i: v._rows[i].short_act for i in (ok.id, unapproved.id, nosrc.id)}
    assert acts[ok.id].isEnabled() and "30–60" in acts[ok.id].toolTip()
    assert not acts[unapproved.id].isEnabled() and "Duyệt" in acts[unapproved.id].toolTip()
    assert not acts[nosrc.id].isEnabled() and "video gốc" in acts[nosrc.id].toolTip()

    # the flow: dialog (faked: pick a range, accept) → a new project → opens in the editor
    def fake_dialog(self, dlg):
        assert dlg.list.count() >= 1                              # proposals are there at once
        dlg.start.setText("0:12")
        dlg.end.setText("0:52")
        dlg.hook.setText("Tại sao cô ấy làm vậy?")
        return True

    monkeypatch.setattr(LibraryView, "_run_dialog", fake_dialog)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(str(a)))
    opened, changed, msgs = [], [], []
    v.openRequested.connect(opened.append)
    v.changed.connect(lambda: changed.append(1))
    v.message.connect(lambda t, k: msgs.append(t))
    def stamp():
        return {p.name: p.stat().st_mtime for p in ok.work_dir.iterdir() if p.name != "thumb.jpg"}

    before = stamp()                                              # (thumb.jpg: the library's own cache)
    v._cut_short(ok.id)
    _wait(lambda: bool(opened), n=1500)
    assert len(opened) == 1 and changed
    child = storage / opened[0]
    assert child.is_dir() and (child / "source.mp4").exists() and (child / "vi.srt").exists()
    md = json.loads((child / "metadata.json").read_text(encoding="utf-8"))
    assert md["short"]["parent"] == ok.id and md["short"]["hook"].startswith("Tại sao")
    assert not approval.is_approved(Job(id=child.name, work_dir=child))      # the human gate stays
    assert before == stamp()                                      # the parent is untouched
    assert any("Short" in m for m in msgs)
    # the library lists it right under its episode, with a Short chip, and cannot cut it again
    _wait(lambda: child.name in v._rows)
    order = v.order()
    assert order.index(child.name) == order.index(ok.id) + 1
    from PySide6.QtWidgets import QLabel

    chips = [lb.text() for lb in v._rows[child.name].findChildren(QLabel)]
    assert any(t.startswith("Short · tập 8") for t in chips), chips
    _wait(lambda: child.name in v._details)
    assert not v._rows[child.name].short_act.isEnabled()
    v.shutdown()
    v.close()


def test_the_ai_button_swaps_in_the_ai_candidates_and_reports_failure(qapp, storage, monkeypatch):
    from desktop.views.library_view import LibraryView
    from desktop.widgets.short_dialog import ShortDialog

    cs = talk()
    v = LibraryView()
    dlg = ShortDialog(None, "Tập 8", cs, 150.0, clipshort.heuristic(cs))
    dlg.show()
    ai = [clipshort.Candidate(5.0, 45.0, "Câu AI chọn", "vì thế", 1.0)]
    monkeypatch.setattr(clipshort, "ask_ai", lambda cues, title="", **kw: ai)
    v._short_ai(dlg, cs, "Tập 8")
    _wait(lambda: dlg.list.count() == 1)
    assert dlg.hook.text() == "Câu AI chọn" and dlg.ai_btn.isEnabled()
    assert "1 đoạn" in dlg.note.text()

    def boom(cues, title="", **kw):
        raise RuntimeError("Cần OpenRouter để gợi ý bằng AI.")

    monkeypatch.setattr(clipshort, "ask_ai", boom)
    v._short_ai(dlg, cs, "Tập 8")
    _wait(lambda: "OpenRouter" in dlg.note.text())
    assert dlg.ai_btn.isEnabled() and dlg.list.count() == 1       # the list is kept
    v.shutdown()
    dlg.close()


# --- the render page ----------------------------------------------------------------------------------

@needs_ffmpeg
def test_a_cut_short_opens_the_render_page_with_the_short_preset(qapp, storage):
    from desktop.theme import qss
    from desktop.views.render_view import RenderView

    parent = episode(storage)
    child = clipshort.make(parent, 12, 52, "Hook", storage)
    qapp.setStyleSheet(qss())
    rv = RenderView()
    rv.show()
    rv.set_job(child, cues=10, tts="edge")
    o = rv.collect_opts()
    assert o["short"] is True and o["frame"] == "tiktok" and o["safe_caption"] == "tiktok"
    plain = RenderView()
    plain.set_job(parent, cues=10, tts="edge")
    assert plain.collect_opts()["short"] is False
    rv.close()
    plain.close()
    time.sleep(0)
