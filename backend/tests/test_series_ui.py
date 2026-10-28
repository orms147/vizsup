"""Bộ phim on the start screen (offscreen Qt): the combo lists the saved series, its pick
travels in the options, and the note shows the term count."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
input_view = pytest.importorskip("desktop.views.input_view")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.pipeline import series  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from app.providers.registry import list_providers

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = input_view.InputView(list_providers())
    v.url.setText("https://www.douyin.com/video/1")
    return v


def items(v):
    return [(v.series_combo.itemText(i), v.series_combo.itemData(i))
            for i in range(v.series_combo.count())]


def test_default_is_auto_and_no_series_yet(view):
    assert [d for _t, d in items(view)] == ["auto", "none"]
    assert view._collect_opts()["series"] == "auto"
    assert "Có tên trong" in view.series_note.text()


def test_saved_series_are_listed_and_the_pick_goes_into_the_options(view):
    p = series.create("末日校车")
    p.glossary = {"丧尸": "zombie", "校车": "xe buýt"}
    series.save(p)
    view.refresh_recent()
    assert ("《末日校车》 · 2 thuật ngữ", p.slug) in items(view)
    view.series_combo.setCurrentIndex(view.series_combo.findData(p.slug))
    assert view._collect_opts()["series"] == p.slug
    assert view.series_note.text() == "Bộ phim: 《末日校车》 · 2 thuật ngữ"
    view.series_combo.setCurrentIndex(view.series_combo.findData("none"))
    assert view._collect_opts()["series"] == "none"
    assert "riêng" in view.series_note.text()


def test_refresh_keeps_the_pick(view):
    p = series.create("甲")
    view.refresh_series()
    view.series_combo.setCurrentIndex(view.series_combo.findData(p.slug))
    view.refresh_series()
    assert view.series_combo.currentData() == p.slug


def test_the_pick_is_not_remembered_between_launches():
    from desktop import store

    assert "series" not in store._UI_KEYS
