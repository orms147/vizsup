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


def test_mode_cards_do_not_overlap_their_text(rv):
    for key, card in rv.mode_cards.items():
        t, d = _in(card, card._title), _in(card, card._desc)
        assert not t.intersects(d), key
        assert card.rect().contains(t) and card.rect().contains(d), key


def test_cards_fit_at_the_narrowest_options_width(rv):
    rv._split.setSizes([360, 740])
    QApplication.processEvents()
    for key, card in rv.mode_cards.items():
        t, d = _in(card, card._title), _in(card, card._desc)
        assert not t.intersects(d) and card.rect().contains(d), key


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
