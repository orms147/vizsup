"""Render page (wave 4, item 1a): the posting / brand options live in ONE collapsible section
"Đăng tải & thương hiệu" with a one-line summary while folded (a missing right to use stays visible
in amber), every control keeps its attribute name, and the primary buttons stay on screen at
1366×768 and on a DPI-scaled ~1130 px window."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    saved = (app.font(), app.styleSheet(),
             {k: app.property(k) for k in ("vz_sans", "vz_mono", "vz_cjk")})
    load_fonts(app)
    yield app
    app.setFont(saved[0])
    app.setStyleSheet(saved[1])
    for k, val in saved[2].items():
        app.setProperty(k, val)


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _view(qapp, width=1100):
    from desktop.theme import qss
    from desktop.views.render_view import RenderView

    qapp.setStyleSheet(qss())
    v = RenderView()
    v.resize(width, 900)
    v.show()
    for _ in range(4):
        qapp.processEvents()
    return v


def test_posting_options_are_in_one_folded_section_with_a_summary(qapp, storage):
    v = _view(qapp)
    sec = v.post_section
    assert not sec.is_expanded()
    for w in (v.credit_cb, v.ai_label_cb, v.wm_cb, v.card_cb, v.rights, v.proof_btn, v.rights_note):
        assert sec.isAncestorOf(w), w
        assert not w.isVisible()                               # folded away
    assert not sec.summary.isHidden()
    text = sec.summary.text()
    assert "ghi nguồn" in text and "nhãn AI" in text and text.endswith("quyền: chưa ghi")
    from desktop.theme import V2

    assert V2["amber"] in sec.summary.styleSheet()              # no right to use recorded: amber
    sec.header.click()                                          # open
    for _ in range(3):
        qapp.processEvents()
    assert sec.is_expanded() and v.credit_cb.isVisible() and v.rights.isVisible()
    assert sec.summary.isHidden()
    v.close()


def test_summary_follows_the_controls(qapp, storage):
    v = _view(qapp)
    s = v.post_section.summary
    v.credit_cb.setChecked(False)
    assert "ghi nguồn" not in s.text() and "nhãn AI" in s.text()
    v.ai_label_cb.setChecked(False)
    assert s.text() == "không bật mục nào · quyền: chưa ghi"
    v.wm_cb.setChecked(True)
    v.wm_text.setText("Vietsub: Kênh Mèo")
    assert "watermark “Vietsub: Kênh Mèo”" in s.text()
    v.card_cb.setChecked(True)
    assert "thẻ tập" in s.text()
    v.rights.setCurrentIndex(v.rights.findData("own"))          # no job: still reflected
    assert s.text().endswith("quyền: video của tôi")
    from desktop.theme import V2

    assert V2["amber"] not in s.styleSheet()                    # recorded: no warning colour
    v.rights.setCurrentIndex(v.rights.findData("unknown"))
    assert s.text().endswith("quyền: chưa ghi")
    v.close()


def test_the_section_state_is_remembered(qapp, storage):
    from desktop import store

    v = _view(qapp)
    v.post_section.header.click()
    assert store.load_ui_state()["render_post_open"] is True
    again = _view(qapp)
    assert again.post_section.is_expanded()
    again.post_section.header.click()
    assert store.load_ui_state()["render_post_open"] is False
    v.close()
    again.close()


def test_footer_summary_says_what_dung_video_will_do(qapp, storage):
    v = _view(qapp)
    v.set_voices(["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"])
    v.select_voice("vi-VN-HoaiMyNeural")
    line = v.summary_lbl.text()
    assert line.startswith("Hoài My 1.00× · ")
    mode = v._audio_mode()
    from desktop.views.render_view import MODES

    assert next(t for k, _i, t, _d in MODES if k == mode) in line
    v.speed.setValue(120)
    assert "Hoài My 1.20×" in v.summary_lbl.text()
    v.mode_cards["original"].click()                             # no dub: no voice in the line
    assert "Hoài My" not in v.summary_lbl.text() and "chỉ phụ đề" in v.summary_lbl.text()
    v.preset.setCurrentIndex(v.preset.findData("light"))
    assert v.preset.currentText().split(" · ")[0] in v.summary_lbl.text()
    v._src_size, v._src_dur = (540, 960), 100.0
    v._sync_quality()
    assert "540×960" in v.summary_lbl.text() and "ước tính" in v.summary_lbl.text()
    v.close()


@pytest.mark.parametrize("width,height", [(1366, 768), (1130, 680)])
def test_the_primary_buttons_are_always_on_screen(qapp, tmp_path, monkeypatch, width, height):
    from app.pipeline import approval
    from desktop import main as m
    from desktop.theme import qss

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)

    class _Tts:
        def voices(self):
            return ["vi-VN-HoaiMyNeural"]

    monkeypatch.setattr(m, "get_tts", lambda _n: _Tts())
    qapp.setStyleSheet(qss())
    win = m.MainWindow()
    win.resize(width, height)
    win.show()
    from app.models import Job
    from desktop.cuemodel import EditorCue

    win.job = Job(id="r1", work_dir=tmp_path / "r1")
    win.opts = {}
    ed = win.editor
    ed.job = win.job
    ed.cues = [EditorCue(start=i * 4.0, end=i * 4.0 + 3, vi=f"câu {i}", zh="一") for i in range(5)]
    ed._reload(dirty=False)
    ed.save_now()                                               # _approve saves too: do it first
    approval.approve(win.job, cues=5)
    win._approve()
    for _ in range(10):
        qapp.processEvents()
    rv = win.render
    rv.again_lbl.setVisible(True)                                # the tallest footer: every line on
    rv.group_box.setVisible(True)
    rv.fit_hint.setText("Người nói nhanh: ở 1.00× khoảng 58% câu sẽ trễ hoặc bị cắt.")
    for _ in range(6):
        qapp.processEvents()
    page = win.stack.currentWidget()
    assert page.verticalScrollBar().maximum() == 0, "the render page scrolls vertically"
    for b in (rv.render_btn, rv.quick_btn):
        bottom = b.mapTo(win, b.rect().bottomRight()).y()
        assert bottom <= win.height(), (b.text(), bottom, win.height())
    win.close()
