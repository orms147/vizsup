"""Series editor dialog ("Bộ phim" ⚙): the table ↔ profile conversion (pure) and the
dialog / input-view round trip, offscreen. No JSON editing for the owner."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.pipeline import series

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
sd = pytest.importorskip("desktop.series_dialog")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


# --- pure ------------------------------------------------------------------------------------

def test_rows_to_map_strips_drops_empty_keys_and_last_row_wins():
    rows = [(" 丧尸 ", " zombie "), ("", "x"), ("  ", "y"), ("丧尸", "thây ma"), ("a", "")]
    assert sd.rows_to_map(rows) == {"丧尸": "thây ma", "a": ""}


def test_rows_to_characters_needs_a_chinese_name_first_row_wins():
    rows = [("林默", " Lâm Mặc ", "nam", " gọi em "), ("", "x", "", ""), ("林默", "khác", "nữ", ""),
            ("小雨", "", "", "")]
    assert sd.rows_to_characters(rows) == [
        {"zh": "林默", "vi": "Lâm Mặc", "gender": "nam", "note": "gọi em"},
        {"zh": "小雨", "vi": "", "gender": "", "note": ""}]


def test_apply_rows_replaces_the_four_parts_and_keeps_style_and_render():
    p = series.Profile(slug="s", name="s", style={"size": 50}, render={"voice": "v"})
    sd.apply_rows(p, brief="  Phim.  ", characters=[("甲", "Giáp", "nam", "")],
                  glossary=[("校车", "xe buýt"), ("空", "")], pronounce=[("AI", "ây ai"), ("ừm", "")])
    assert p.brief == "Phim." and p.characters[0]["vi"] == "Giáp"
    assert p.glossary == {"校车": "xe buýt"}                  # a glossary entry needs a translation
    assert p.pronounce == {"AI": "ây ai", "ừm": ""}           # empty spoken = stay silent
    assert p.style == {"size": 50} and p.render == {"voice": "v"}


# --- the dialog ------------------------------------------------------------------------------------

def test_dialog_shows_the_profile_and_saves_edits(qapp, storage):
    p = series.create("末日校车")
    p.brief = "Cũ."
    p.characters = [{"zh": "林默", "vi": "Lâm Mặc", "gender": "nam", "note": "xưng anh"}]
    p.glossary = {"校车": "xe buýt"}
    p.pronounce = {"zombie": "dôm bi"}
    series.save(p)
    dlg = sd.SeriesDialog(series.load(p.slug))
    assert dlg.brief.toPlainText() == "Cũ."
    assert dlg.chars.cells() == [("林默", "Lâm Mặc", "nam", "xưng anh")]
    assert dlg.gloss.cells() == [("校车", "xe buýt")] and dlg.pron.cells() == [("zombie", "dôm bi")]
    dlg.brief.setPlainText("Mới.")
    dlg.chars.add_row("小雨", "Tiểu Vũ", "nữ", "gọi anh Mặc là anh")
    dlg.gloss.add_row("丧尸", "zombie")
    dlg.pron.add_row("AI", "ây ai")
    dlg.gloss.table.selectRow(0)                               # remove the first glossary row
    dlg.gloss.remove_row()
    dlg._save()
    q = series.load(p.slug)
    assert q.brief == "Mới."
    assert [c["zh"] for c in q.characters] == ["林默", "小雨"] and q.characters[1]["gender"] == "nữ"
    assert q.glossary == {"丧尸": "zombie"} and q.pronounce == {"zombie": "dôm bi", "AI": "ây ai"}


def test_forget_saved_style_and_render(qapp, storage):
    p = series.create("甲")
    p.style, p.render = {"size": 40}, {"voice": "v"}
    series.save(p)
    dlg = sd.SeriesDialog(series.load(p.slug))
    assert dlg.forget_style.isEnabled() and "đã lưu" in dlg.style_lbl.text()
    dlg.forget_style.click()
    dlg.forget_render.click()
    assert not dlg.forget_style.isEnabled() and "chưa có" in dlg.render_lbl.text()
    dlg._save()
    q = series.load(p.slug)
    assert q.style == {} and q.render == {}


def test_cancel_changes_nothing(qapp, storage):
    p = series.create("甲")
    dlg = sd.SeriesDialog(series.load(p.slug))
    dlg.brief.setPlainText("không lưu")
    dlg.reject()
    assert series.load(p.slug).brief == ""


# --- from the start screen ------------------------------------------------------------------------------

@pytest.fixture()
def view(qapp, storage):
    from desktop.views import input_view
    from app.providers.registry import list_providers

    return input_view.InputView(list_providers())


def test_gear_edits_the_picked_series(view, monkeypatch):
    p = series.create("乙")
    view.refresh_series()
    view.series_combo.setCurrentIndex(view.series_combo.findData(p.slug))
    seen = {}

    def fake_exec(self):
        seen["name"] = self.profile.name
        self.brief.setPlainText("đã sửa")
        self._save()
        return 1

    monkeypatch.setattr(sd.SeriesDialog, "exec", fake_exec)
    view.series_edit.click()
    assert seen["name"] == "乙" and series.load(p.slug).brief == "đã sửa"
    assert view.series_combo.currentData() == p.slug


def test_gear_with_no_pick_creates_a_series_first(view, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("末日校车", True)))
    monkeypatch.setattr(sd.SeriesDialog, "exec", lambda self: 1)
    assert view.series_combo.currentData() == "auto"
    view.series_edit.click()
    created = series.load(series.slugify("末日校车"))
    assert created is not None and created.name == "末日校车"
    assert view.series_combo.currentData() == created.slug       # and it is picked


def test_gear_cancelled_name_creates_nothing(view, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("  ", True)))
    view.series_edit.click()
    assert series.list_profiles() == []
