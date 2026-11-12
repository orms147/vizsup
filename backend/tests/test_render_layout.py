"""Render page layout (offscreen Qt): the mode cards never draw over each other, the
options | preview seam is a splitter whose width is remembered, and the no-dub mode says
what it does (2026-10-27 user report: wrapped card text overlapped its title)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import settings  # noqa: E402
from desktop.views import render_view  # noqa: E402


@pytest.fixture()
def rv(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = render_view.RenderView()
    v.resize(1100, 800)
    v.show()
    QApplication.processEvents()
    return v


def _in(card, w):
    return QRect(w.mapTo(card, w.rect().topLeft()), w.size())


def test_three_folded_sections_say_what_the_render_will_use(rv):
    """2026-11-12: the options column is three folding sections, folded by default, each with a
    one-line summary — no scrolling to press Dựng video."""
    from PySide6.QtWidgets import QScrollArea

    for sec in rv.sections.values():
        assert not sec.is_expanded() and not sec.summary.isHidden()
    assert "Giữ" in rv.sec_voice.summary.text() or "giọng" in rv.sec_voice.summary.text().lower()
    assert "Khung gốc" in rv.sec_pic.summary.text()
    left = rv._split.widget(0)
    area = left.findChildren(QScrollArea)[0]
    assert area.verticalScrollBar().maximum() == 0           # everything fits: nothing to scroll
    rv.thumb_cb.setChecked(True)
    assert "ảnh bìa" in rv.sec_more.summary.text()
    rv.thumb_cb.setChecked(False)
    rv.sharpen_cb.setChecked(False)
    rv.karaoke_cb.setChecked(False)
    assert rv.sec_more.summary.text() == "Không bật gì thêm"
    rv.frame.setCurrentIndex(rv.frame.findData("tiktok"))
    assert "Dọc 9:16" in rv.sec_pic.summary.text()


def test_sections_remember_open_and_closed(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = render_view.RenderView()
    v.sec_voice.header.click()
    assert v.sec_voice.is_expanded()
    v2 = render_view.RenderView()
    assert v2.sec_voice.is_expanded() and not v2.sec_more.is_expanded()


def test_audio_kind_is_one_combo_that_drives_the_same_choice(rv):
    """The four tall cards are the hidden model; the combo shows and sets the choice."""
    from desktop import store

    rv.sec_voice.set_expanded(True)
    QApplication.processEvents()
    assert rv.audio_combo.isVisible() and not rv._cards_box.isVisible()
    assert rv.audio_combo.count() == 4
    assert rv.audio_combo.currentData() == rv._audio_mode()
    i = rv.audio_combo.findData("replace")
    rv.audio_combo.setCurrentIndex(i)
    rv.audio_combo.activated.emit(i)                       # what a pick by the owner sends
    assert rv._audio_mode() == "replace" and rv.mode_cards["replace"].isChecked()
    assert rv.collect_opts()["audio_mode"] == "replace"
    assert store.load_ui_state()["render_audio"] == "replace"      # the next project's default
    assert "Bỏ hết âm gốc" in rv.audio_desc.text()
    assert "Chỉ giọng Việt" in rv.sec_voice.summary.text() or rv.sec_voice.is_expanded()
    rv.mode_cards["duck"].setChecked(True)                 # code picks one: the combo follows
    assert rv.audio_combo.currentData() == "duck"


def test_no_dub_mode_says_it_keeps_the_original_audio(rv):
    title = rv.mode_cards["original"]._title.text()
    desc = rv.mode_cards["original"].desc()
    assert "Không lồng tiếng" in title and "không tạo giọng" in desc


def test_seam_is_a_splitter_and_remembers_its_width(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = render_view.RenderView()
    v.resize(1300, 800)
    v.show()                                            # the splitter lays out once shown
    QApplication.processEvents()
    assert v._split.count() == 2 and v._split.childrenCollapsible() is False
    v._split.setSizes([500, 700])
    QApplication.processEvents()
    v._split.splitterMoved.emit(500, 1)                 # what a drag of the seam emits
    saved = json.loads((tmp_path / "ui_state.json").read_text(encoding="utf-8"))
    assert saved["render_split"] == v._split.sizes()
    v2 = render_view.RenderView()                       # a new page starts where it was left
    v2.resize(1300, 800)
    v2.show()
    QApplication.processEvents()
    assert abs(v2._split.sizes()[0] - saved["render_split"][0]) <= 2


def test_nine_sixteen_background_choice(tmp_path, monkeypatch):
    """2026-10-29: blurred picture (default) or black behind a landscape video in 9:16;
    shown only for the 9:16 frame, remembered, sent with the render options."""
    from app.config import settings
    from desktop import store
    from desktop.views.render_view import RenderView

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = RenderView()
    v.frame.setCurrentIndex(v.frame.findData("source"))
    assert v.fill.isHidden()
    v.frame.setCurrentIndex(v.frame.findData("tiktok"))
    assert not v.fill.isHidden() and v.collect_opts()["tiktok_fill"] == "blur"
    v.fill.setCurrentIndex(v.fill.findData("black"))
    assert v.collect_opts()["tiktok_fill"] == "black"
    assert store.load_ui_state()["render_fill"] == "black"
    v.shutdown()
