"""Hands-on UX audit, 2026-11-01 (offscreen walk of every screen): the widget-level bugs it found,
one test each — a hint that named the wrong key, letters cut off, boxes overlapping, tick
boxes / step arrows / disabled buttons that could not be told apart from their background."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    saved = (app.font(), {k: app.property(k) for k in ("vz_sans", "vz_mono", "vz_cjk")})
    load_fonts(app)
    yield app
    app.setFont(saved[0])
    for k, val in saved[1].items():
        app.setProperty(k, val)


def _settle(qapp, n: int = 4) -> None:
    for _ in range(n):
        qapp.processEvents()


def _lum(c) -> float:
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def test_status_bar_hints_name_what_the_keys_do():
    """"[ ] Canh lời" sat in the editor's status bar, but [ / ] set the start / end to the
    playhead — snapping to the voice is A (desktop/widgets/shortcuts.py)."""
    from desktop.main import EDITOR_HINTS
    from desktop.widgets.shortcuts import SHORTCUTS

    hints = dict(EDITOR_HINTS)
    listed = dict(SHORTCUTS)
    assert hints["[ ]"].startswith("Đặt đầu") and listed["[ / ]"].startswith("Đặt đầu")
    assert "canh" in hints["A"].lower() and "canh" in listed["A / Shift+A"].lower()
    assert "canh" not in hints["[ ]"].lower()


_MNEMONIC = re.compile(r"(?<!&)&(?!&)")         # a lone "&" is eaten as a shortcut marker


def test_no_ampersand_is_eaten_by_a_button_or_menu(qapp):
    """Kiểu chữ "Dòng & vị trí" showed as "Dòng _vị trí", the list's ··· menu as "Gộp câu quá
    ngắn  gỡ chồng lấn": Qt reads a lone & in a button / menu text as a shortcut marker."""
    from PySide6.QtWidgets import QAbstractButton

    from desktop.widgets.cue_list import CueList
    from desktop.widgets.style_panel import StylePanel

    texts = [b.text() for b in StylePanel().findChildren(QAbstractButton)]
    cl = CueList()
    texts += [a.text() for a in cl.more_btn.menu().actions()]
    eaten = [t for t in texts if _MNEMONIC.search(t)]
    assert not eaten, eaten
    assert any("&&" in t for t in texts)             # the two that had one, shown as "&"


def test_the_autosave_mark_stays_on_the_editor(qapp, tmp_path, monkeypatch):
    """Ctrl+L right after a fix: the autosave landed on Thư viện and put "Đã lưu" + ⌨ in its
    bar (also after a cancelled «Dựng video» step from there)."""
    from app.config import settings
    from desktop import main as m

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    w = m.MainWindow()
    w.show()
    _settle(qapp)
    for page in (m.LIBRARY, m.SETTINGS, m.INPUT):
        w.stack.setCurrentIndex(page)
        w.editor.dirtyChanged.emit(True)
        w.editor.dirtyChanged.emit(False)
        _settle(qapp, 2)
        assert not w.nav.saved.isVisible() and not w.nav.keys_btn.isVisible(), page
    w.stack.setCurrentIndex(m.EDITOR)
    _settle(qapp, 2)
    assert w.nav.saved.isVisible()
    w.editor.dirtyChanged.emit(True)
    assert w.nav.saved.text() == "Đang lưu…"
    w.close()


def test_bold_italic_caps_letters_fit_their_buttons(qapp):
    """B / I / AA in Kiểu chữ: the app's 13-px button padding left 6 px of a 32-px button, the
    letters showed as cut-off marks."""
    from desktop.theme import qss
    from desktop.widgets.style_panel import StylePanel

    p = StylePanel()
    p.setStyleSheet(qss())
    p.resize(380, 900)
    p.show()
    _settle(qapp)
    for b in (p.bold, p.italic, p.caps):
        b.ensurePolished()
        assert b.sizeHint().width() <= b.width(), (b.text(), b.sizeHint().width(), b.width())
    p.close()


def test_series_gender_boxes_do_not_overlap_the_next_row(qapp):
    """Bộ phim → Nhân vật: the "Nam / Nữ" box of each row (34 px) overlapped the next row (30)."""
    from desktop.series_dialog import _Characters
    from desktop.theme import qss

    t = _Characters()
    t.setStyleSheet(qss())
    for zh, g in (("顾哥", "m"), ("江老师", "f"), ("林小小", "f")):
        t.add_row(zh, "x", g, "")
    t.add_row()                                   # "Thêm dòng" too
    t.resize(700, 400)
    t.show()
    _settle(qapp)
    combos = [t.table.cellWidget(r, 2) for r in range(t.table.rowCount())]
    for r, c in enumerate(combos):
        assert t.table.rowHeight(r) >= c.sizeHint().height(), r
    for a, b in zip(combos, combos[1:]):
        assert a.geometry().bottom() < b.geometry().top()
    t.close()


def test_a_wrapped_dialog_opens_tall_enough_for_its_rows(qapp):
    """Dịch một loạt video: the window took the layout's minimum height, which a word-wrapped
    label underestimates at the real width — "Chọn tất cả" sat 30 px over the list."""
    from PySide6.QtWidgets import QPushButton

    from desktop.theme import qss
    from desktop.widgets.batch_import_dialog import BatchImportDialog

    d = BatchImportDialog(None, url="", lister=lambda u, limit: [], load_now=False)
    d.setStyleSheet(qss())
    room = qapp.primaryScreen().availableGeometry().height() - 60
    need = d.layout().totalHeightForWidth(d.minimumWidth())
    if need > room:
        pytest.skip(f"this screen is too short ({room} < {need}) for the check")
    d.show()
    _settle(qapp)
    row = next(b for b in d.findChildren(QPushButton) if b.text() == "Chọn tất cả")
    assert row.geometry().top() > d.tree.geometry().bottom(), (row.geometry(), d.tree.geometry())
    assert d.height() >= d.layout().totalHeightForWidth(d.width())
    d.close()


def test_tick_boxes_in_lists_stand_out_from_the_list(qapp):
    """Dịch một loạt / Ghép video / Dựng hàng loạt: an unticked row's box was a near-black
    square on the near-black list."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidget, QListWidgetItem

    from desktop.theme import V2, qss

    lw = QListWidget()
    lw.setStyleSheet(qss())
    it = QListWidgetItem("《末日校车》09")
    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
    it.setCheckState(Qt.Unchecked)
    lw.addItem(it)
    lw.resize(300, 80)
    lw.show()
    _settle(qapp)
    img = lw.viewport().grab().toImage()
    r = lw.visualItemRect(it)
    from PySide6.QtGui import QColor

    bg = _lum(QColor(V2["input"]))
    brightest = max(_lum(img.pixelColor(x, y)) for x in range(r.left(), r.left() + 26)
                    for y in range(r.top(), r.bottom()))
    assert brightest - bg > 45, brightest            # the box's edge reads on the list
    lw.close()


@pytest.mark.parametrize("variant", ["danger", "ghost"])
def test_a_disabled_button_looks_disabled(qapp, variant):
    """Thư viện "Xóa dự án đã chọn" with nothing ticked looked exactly like a live button: an
    id rule (#danger / #ghost) outranked the plain :disabled one."""
    from PySide6.QtWidgets import QPushButton

    from desktop.theme import qss

    b = QPushButton("Xóa dự án đã chọn")
    b.setObjectName(variant)
    b.setStyleSheet(qss())
    b.resize(180, 34)
    b.show()
    _settle(qapp)
    live = b.grab().toImage()
    b.setEnabled(False)
    _settle(qapp)
    off = b.grab().toImage()
    assert live != off
    b.close()


def test_every_image_the_style_sheet_names_exists():
    """The spin box step arrows are files (desktop/assets/spin): a wrong path draws nothing."""
    from desktop.theme import qss

    urls = re.findall(r"url\(([^)]+)\)", qss())
    assert urls, "the spin box arrows are drawn from files"
    missing = [u for u in urls if not Path(u).exists()]
    assert not missing, missing


def test_spin_box_arrows_are_visible(qapp):
    from PySide6.QtWidgets import QSpinBox

    from desktop.theme import V2, qss

    sp = QSpinBox()
    sp.setStyleSheet(qss())
    sp.setRange(1, 168)
    sp.setValue(6)
    sp.resize(120, 36)
    sp.show()
    _settle(qapp)
    img = sp.grab().toImage()
    from PySide6.QtGui import QColor

    bg = _lum(QColor(V2["input"]))
    right = [_lum(img.pixelColor(x, y)) for x in range(sp.width() - 20, sp.width() - 4)
             for y in range(4, sp.height() - 4)]
    assert max(right) - bg > 60                         # an arrow, not a black-on-black one
    sp.close()


def test_a_recent_project_detail_line_wraps_instead_of_being_cut(qapp, tmp_path, monkeypatch):
    """Bắt đầu → Tiếp tục dự án: "16 câu cần xem lại · 01:41 · AI $0.013 · 6 phút 56" was cut
    after "AI $0.013" in the 360-px column."""
    from app.config import settings
    from desktop.theme import qss
    from desktop.widgets.recent_projects import _Row

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    r = _Row({"id": "x1", "title": "第6集 《末日校车》08", "has_output": False, "mtime": 0})
    r.apply_summary({"cues": 67, "pending": 16, "duration": 101.0,
                     "usage": "AI $0.013 · 6 phút 56"})
    r.setStyleSheet(qss())
    r.setFixedWidth(304)
    r.show()
    _settle(qapp)
    s = r.sub
    assert s.fontMetrics().horizontalAdvance(s.text()) > s.width()     # longer than the column…
    assert s.wordWrap() and s.height() >= s.heightForWidth(s.width())  # …so it takes two lines
    r.close()


def test_an_added_line_fits_the_gap_before_the_next_one():
    """«Thêm câu sau câu này» made a 2-s line even when the next line started 1 s later — it
    was flagged "Chồng lấn" the moment it appeared."""
    from desktop.cuemodel import EditorCue, added_span

    cues = [EditorCue(start=0.0, end=1.0, vi="a"), EditorCue(start=2.2, end=3.0, vi="b"),
            EditorCue(start=10.0, end=11.0, vi="c")]
    assert added_span(cues, 0) == (1.0, 2.2)          # the 1.2-s gap, not over "b"
    assert added_span(cues, 1) == (3.0, 5.0)          # room enough: 2 s
    assert added_span(cues, 2) == (11.0, 13.0)        # the last line
    assert added_span(cues, -1) == (11.0, 13.0)       # nothing selected: at the end
    assert added_span([], -1) == (0.0, 2.0)
    tight = [EditorCue(start=0.0, end=1.0, vi="a"), EditorCue(start=1.2, end=2.0, vi="b")]
    assert added_span(tight, 0) == (1.0, 3.0)         # no room: 2 s, the owner moves it


def test_the_result_says_where_it_is_saved(qapp, monkeypatch):
    """"Đã lưu" beside «Lưu video…» read as "already copied where I want it"."""
    from desktop.views import render_view

    monkeypatch.setattr(render_view, "_separation_available", lambda: False)
    v = render_view.RenderView()
    assert v.saved_lbl.text() == "Đã lưu trong dự án"
    assert "Lưu video" in v.saved_lbl.toolTip()
    v.close()


def test_the_library_player_is_titled_like_the_row(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from desktop import store
    from desktop.views import library_view

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    titles = []

    class Player:
        def __init__(self, _parent, title, _videos):
            titles.append(title)

        def exec(self):
            return 0

    monkeypatch.setattr(library_view, "PlayerDialog", Player)
    monkeypatch.setattr(store, "videos_of", lambda it: [("Bản dựng", tmp_path / "o.mp4")])
    monkeypatch.setattr(store, "friendly_title",
                        lambda iid, t: "Chuyến Xe Tận Thế · Tập 9" if "09" in t else None)
    lib = library_view.LibraryView()
    lib._items = [{"id": "a1", "title": "第7集 | 《末日校车》09 我一个普通大巴司机#ai动漫"},
                  {"id": "b2", "title": "Một video"}]
    lib._watch("a1")
    lib._watch("b2")
    assert titles == ["Chuyến Xe Tận Thế · Tập 9", "Một video"]
    lib.close()


def test_editor_listen_uses_the_dub_voice(qapp, tmp_path, monkeypatch):
    """«Nghe thử» in Sửa phụ đề read the voice from the run options, which hold none for most
    projects: the line played in Hoài My while the video was dubbed in Ngọc Huyền 1.3× +2."""
    import json
    from types import SimpleNamespace

    from app.config import settings
    from desktop.views.editor_view import EditorView

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    (tmp_path / "voice.json").write_text(json.dumps(
        {"voice": "vieneu:Ngọc Huyền", "rate": 1.3, "pitch": 2}), encoding="utf-8")
    v = EditorView()
    v.job = SimpleNamespace(work_dir=tmp_path, id="x")
    v.set_context({"tts": "edge"})
    assert v._dub_voice() == ("vieneu", "Ngọc Huyền", "+30%", 2.0)
    (tmp_path / "voice.json").unlink()                       # never rendered: the run's pick
    v.set_context({"tts": "edge", "voice": "vi-VN-NamMinhNeural", "rate": "+10%"})
    assert v._dub_voice() == ("edge", "vi-VN-NamMinhNeural", "+10%", 0.0)
    v.close()


def test_batch_settings_line_names_the_voice_like_the_render_page():
    from app.pipeline import batch

    line = batch.describe({"voice": "vieneu:Ngọc Huyền", "rate": "+30%", "audio_mode": "duck"})
    assert line.startswith("Ngọc Huyền · trên máy · +30%")
    assert "vieneu:" not in line
