"""Kiểu chữ v2: text / background opacity end to end (panel → style dict → .ass), the
colour wheel popup, fonts (every installed Vietnamese-capable face, fonts added from
files, the files the render needs) and the compact panel layout."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from app import fontfiles  # noqa: E402
from app.models import Cue  # noqa: E402

BUNDLED = Path(__file__).resolve().parents[2] / "desktop" / "assets" / "fonts"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    load_fonts(app)
    return app


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    from app.config import settings

    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


# --- opacity --------------------------------------------------------------------------

def test_opacity_percent_maps_to_ass_alpha():
    from desktop.widgets.style_panel import alpha_to_pct, pct_to_alpha

    assert (pct_to_alpha(0), pct_to_alpha(50), pct_to_alpha(100)) == (0, 128, 255)
    assert alpha_to_pct(110) == 43 and alpha_to_pct(pct_to_alpha(37)) == 37


def test_text_and_box_opacity_reach_the_ass_style(tmp_path):
    from app.pipeline.srt import write_ass

    style = {"primary": "#FFFFFF", "outline": "#000000", "box": "#102030", "box_alpha": 128,
             "text_alpha": 64, "border_style": 4, "outline_w": 2, "shadow": 0, "bold": False,
             "italic": False, "alignment": 2, "margin_l": 40, "margin_r": 40, "margin_v": 60}
    out = tmp_path / "s.ass"
    write_ass([Cue(1, 0.0, 1.0, "xin chào")], out, font="Arial", size=60, style=style)
    line = next(ln for ln in out.read_text(encoding="utf-8").splitlines()
                if ln.startswith("Style:"))
    fields = line.split(",")
    assert fields[3] == "&H40FFFFFF"        # text: alpha 0x40
    assert fields[5] == "&H40000000"        # outline fades with the text
    assert fields[6] == "&H80302010"        # box: its own alpha, BGR


def test_panel_round_trips_opacities_and_box_kinds(qapp):
    from desktop.widgets.style_panel import StylePanel

    sp = StylePanel()
    sp.box_style.setCurrentText("Hộp mờ")
    assert sp.box_fade.value() == 43                        # the kind's own level
    sp.box_fade.setValue(70)
    sp.text_fade.setValue(30)
    s = sp.get_style()
    assert s["border_style"] == 4 and s["box_alpha"] == 178 and s["text_alpha"] == 76
    sp.box_style.setCurrentText("Hộp đặc (che chữ Trung)")
    assert sp.box_fade.value() == 0 and sp.get_style()["box_alpha"] == 0

    other = StylePanel()
    other.set_from({"font": "Be Vietnam Pro", "size": 22, "style": s})
    t = other.get_style()
    assert other.box_style.currentText() == "Hộp mờ"
    assert (t["box_alpha"], t["text_alpha"]) == (178, 76)
    assert other.box_fade.isVisibleTo(other) or not other.isVisible()

    # no background: the background controls step aside
    other.box_style.setCurrentText("Viền chữ (không nen)".replace("nen", "nền"))
    assert other.get_style()["border_style"] == 1
    assert other._box_color_row.isHidden()


def test_scope_extras_show_only_for_selected_lines(qapp):
    from desktop.widgets.style_panel import StylePanel

    sp = StylePanel()
    assert sp.btn_reset_cue.isHidden() and sp.scope_hint.isHidden()
    sp.scope.setCurrentIndex(1)
    assert not sp.btn_reset_cue.isHidden() and sp.get_scope() == "selected"


def test_position_group_starts_folded(qapp):
    from desktop.widgets.style_panel import StylePanel

    sp = StylePanel()
    assert sp.pos_section.body.isHidden() and not sp.text_section.body.isHidden()
    sp.pos_section.head.setChecked(True)
    assert not sp.pos_section.body.isHidden()


# --- colour wheel -----------------------------------------------------------------------

def test_wheel_hex_and_escape(qapp, storage):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent

    from desktop.widgets import color_picker as cp

    pop = cp.ColorPopup("#FFD966")
    seen = []
    pop.colorChanged.connect(seen.append)
    assert pop.color() == "#FFD966"
    pop.hex.setText("#4DD0E1")
    pop._from_hex()
    assert seen[-1] == "#4DD0E1" and pop.color() == "#4DD0E1"
    pop.resize(240, 400)
    pop.show()
    qapp.processEvents()
    w = pop.wheel
    c, r = w._geom()
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(c.x() + r * 0.99, c.y()),
                     QPointF(c.x() + r * 0.99, c.y()), Qt.LeftButton, Qt.LeftButton,
                     Qt.NoModifier)
    w.mousePressEvent(ev)                                      # far right = pure red hue
    hue, sat, _v = w.hsv()
    assert hue < 0.02 or hue > 0.98
    assert sat > 0.95
    QKeyEventEsc = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    pop.keyPressEvent(QKeyEventEsc)
    assert seen[-1] == "#FFD966"                               # Esc: the start colour back
    assert cp.recent_colors() == []                            # nothing kept on Esc

    pop2 = cp.ColorPopup("#000000")
    pop2.set_color("#FF6FB5", emit=True)
    pop2.close()
    assert cp.recent_colors()[0] == "#FF6FB5"


def test_colour_button_applies_live(qapp, storage):
    from desktop.widgets.style_panel import _ColorButton

    b = _ColorButton("#ffffff")
    n = []
    b.changed.connect(lambda: n.append(1))
    b._live("#123456")
    assert b.hex == "#123456" and b.text() == "#123456" and n == [1]
    b._live("#123456")
    assert n == [1]                                            # same colour: no change


# --- fonts -----------------------------------------------------------------------------

def test_registry_family_matching():
    assert fontfiles._matches("Arial Bold Italic (TrueType)", "Arial")
    assert fontfiles._matches("Cambria & Cambria Math (TrueType)", "Cambria Math")
    assert not fontfiles._matches("Arial Narrow (TrueType)", "Arial")   # another family
    assert not fontfiles._matches("Arialic (TrueType)", "Arial")
    reg = [("Tahoma (TrueType)", Path("tahoma.ttf")), ("Tahoma Bold (TrueType)", Path("tahomabd.ttf")),
           ("Verdana (TrueType)", Path("verdana.ttf"))]
    assert [p.name for p in fontfiles.system_files("Tahoma", reg)] == ["tahoma.ttf", "tahomabd.ttf"]


def test_added_fonts_are_kept_registered_and_rendered(qapp, storage, tmp_path, monkeypatch):
    from desktop import fonts

    src = tmp_path / "MyFont.ttf"
    shutil.copyfile(BUNDLED / "BeVietnamPro-Regular.ttf", src)
    junk = tmp_path / "notes.txt"
    junk.write_text("x")
    res = fonts.add_font_files([src, junk])
    assert res[0][0] == "Be Vietnam Pro" and res[0][1] is True and res[0][2] == ""
    assert "không phải tệp font" in res[1][2]
    d = fontfiles.user_dir(storage)
    assert (d / "MyFont.ttf").exists()
    assert json.loads((d / fontfiles.INDEX).read_text(encoding="utf-8")) == {"MyFont.ttf": "Be Vietnam Pro"}
    monkeypatch.setattr(fontfiles, "_registry_fonts", lambda: [])
    names = [p.name for p in fontfiles.files_for("Be Vietnam Pro", storage, BUNDLED)]
    assert "MyFont.ttf" in names and "BeVietnamPro-Regular.ttf" in names


def test_font_list_offers_vietnamese_faces_only(qapp, storage):
    from desktop import fonts

    bundled, _user, system = fonts.families()
    assert bundled[0] == "Be Vietnam Pro"
    assert not any(f.startswith("JetBrains Mono") for f in system)
    assert all(fonts.supports_vietnamese(f) for f in system)


def test_export_never_attaches_a_windows_font(tmp_path, storage):
    from app.models import Job
    from app.pipeline import export

    j = Job(id="e", work_dir=tmp_path / "e")
    (j.work_dir / "fonts").mkdir()
    for n in ("BeVietnamPro-Regular.ttf", "arial.ttf", "JetBrainsMono.ttf"):
        (j.work_dir / "fonts" / n).write_bytes(b"f")
    assert [p.name for p in export._fonts(j)] == ["BeVietnamPro-Regular.ttf"]
