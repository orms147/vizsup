"""Responsive layout + pointer feedback (offscreen Qt): the start screen switches to
two columns on wide windows and back, the editor never cuts its inspector off and
does not let it sprawl, the render panel fits its scroll area, clickable widgets get
the hand cursor (arrow when disabled), and the hover glow paints only while hovered."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
pointer = pytest.importorskip("desktop.widgets.pointer")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QHoverEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QFrame,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

from desktop.widgets.chrome import breakpoint_for  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """With the app's bundled fonts (widths depend on them); the app font and the
    font properties are restored for the other test modules afterwards."""
    from desktop.theme import load_fonts

    app = QApplication.instance() or QApplication([])
    saved = (app.font(), {k: app.property(k) for k in ("vz_sans", "vz_mono", "vz_cjk")})
    load_fonts(app)
    yield app
    app.setFont(saved[0])
    for k, val in saved[1].items():
        app.setProperty(k, val)


def _styled(w: QWidget) -> QWidget:
    """The app's stylesheet on this view only (sizes depend on it; other test modules
    share the QApplication and must not inherit it)."""
    from desktop.theme import qss

    w.setStyleSheet(qss())
    return w


def _settle(app, w: QWidget, width: int, height: int = 900) -> None:
    w.resize(width, height)
    for _ in range(3):
        app.processEvents()


def _clipped(root: QWidget) -> list[str]:
    """Scroll areas (no sideways scrolling) whose content is wider than the viewport."""
    bad = []
    for sa in root.findChildren(QScrollArea):
        inner = sa.widget()
        if sa.isVisible() and inner is not None and sa.widgetResizable():
            need, have = inner.minimumSizeHint().width(), sa.viewport().width()
            if need > have:
                bad.append(f"{type(inner).__name__}: {need} > {have}")
    return bad


# --- breakpoints / split sizes ---------------------------------------------------------

def test_breakpoint_picks_the_widest_reached():
    pts = (("narrow", 0), ("wide", 1500), ("xwide", 2200))
    assert breakpoint_for(1366, pts) == "narrow"
    assert breakpoint_for(1500, pts) == "wide"
    assert breakpoint_for(2560, pts) == "xwide"
    assert breakpoint_for(-1, pts) == "narrow"


def test_editor_split_caps_inspector_and_list_on_wide_windows():
    from desktop.views.editor_view import INSPECTOR_MAX, LIST_MAX, split_sizes

    for total in (1300, 1850, 2490):
        v, lst, ins = split_sizes(total, "balanced", inspector_min=390)
        assert v + lst + ins == total
        assert 390 <= ins <= max(390, INSPECTOR_MAX) and lst <= LIST_MAX
        v, lst, ins = split_sizes(total, "video", inspector_min=390)   # the panel steps aside
        assert ins == 0 and v + lst == total and lst <= LIST_MAX and v > lst
    v, lst, ins = split_sizes(2490, "balanced", 390)
    assert v > lst and ins == INSPECTOR_MAX             # the extra width went to the video


# --- start screen ------------------------------------------------------------------------

@pytest.fixture()
def input_view(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from app.providers.registry import list_providers
    from desktop.views import input_view

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = _styled(input_view.InputView(list_providers()))
    v._est_timer.timeout.disconnect()                  # no duration probe in tests
    v.show()
    yield v
    v.close()


def test_start_screen_goes_two_columns_when_wide_and_back(qapp, input_view):
    v = input_view
    v.opt_btn.setChecked(False)
    _settle(qapp, v, 1366)
    assert not v._wide and v.options.isHidden() and v.opt_btn.isVisible()
    assert "\n" in v.headline.text()

    _settle(qapp, v, 1920)
    assert v._wide and v.options.parentWidget() is v._right
    assert v.options.isVisible() and not v.opt_btn.isVisible()
    assert "\n" not in v.headline.text()
    assert not _clipped(v)

    v.set_busy(True)                                   # the run already took its options
    assert v.options.isVisible() and not v.options.isEnabled()
    v._finish()
    assert v.options.isEnabled()

    _settle(qapp, v, 1366)
    assert not v._wide and v.options.parentWidget() is v._left
    assert v.options.isHidden()                       # the toggle is off again
    v.opt_btn.setChecked(True)
    assert v.options.isVisible() and not _clipped(v)


# --- editor / render: nothing cut off at the right -------------------------------------

def test_editor_inspector_fits_1366_and_stays_bounded_at_2560(qapp):
    from desktop.cuemodel import EditorCue
    from desktop.views.editor_view import INSPECTOR_MAX, EditorView

    v = _styled(EditorView())
    v.cues = [EditorCue(start=0.0, end=2.0, vi="một câu khá dài để thử", zh="一")]
    v._reload(dirty=False)
    v.cue_list.selectRow(0)
    v.show()
    try:
        _settle(qapp, v, 1366 - 36, 640)
        for tab in range(v.inspector.tabbar.count()):
            v.inspector.set_tab(tab)
            qapp.processEvents()
            assert not _clipped(v.inspector), f"tab {tab}"
        _settle(qapp, v, 2560 - 36, 1300)
        assert v.inspector.width() <= max(INSPECTOR_MAX, v.inspector.content_min_width())
        assert v.video.width() > v.inspector.width()
    finally:
        v.close()


def test_render_options_panel_fits_its_scroll_area(qapp):
    from desktop.views.render_view import RenderView

    v = _styled(RenderView())
    v.show()
    try:
        _settle(qapp, v, 1366, 680)
        assert not _clipped(v)
    finally:
        v.close()


# --- cursor policy -----------------------------------------------------------------------

@pytest.fixture()
def policy(qapp):
    p = pointer.CursorPolicy()
    qapp.installEventFilter(p)
    yield p
    qapp.removeEventFilter(p)


def test_clickables_get_the_hand_text_fields_and_explicit_cursors_do_not(qapp, policy):
    box = QWidget()
    btn, cb, edit, kept = QPushButton("a", box), QCheckBox("b", box), QLineEdit(box), \
        QPushButton("c", box)
    kept.setCursor(Qt.ArrowCursor)                     # set in code → the policy keeps it
    box.show()
    qapp.processEvents()
    assert btn.cursor().shape() == Qt.PointingHandCursor
    assert cb.cursor().shape() == Qt.PointingHandCursor
    assert edit.cursor().shape() == Qt.IBeamCursor
    assert kept.cursor().shape() == Qt.ArrowCursor
    btn.setEnabled(False)
    assert btn.cursor().shape() == Qt.ArrowCursor
    btn.setEnabled(True)
    assert btn.cursor().shape() == Qt.PointingHandCursor
    box.close()


# --- hover glow --------------------------------------------------------------------------

def _hover(w: QWidget, kind, x: float, y: float) -> None:
    p = QPointF(x, y)
    QApplication.sendEvent(w, QHoverEvent(kind, p, w.mapToGlobal(p), p))


def _brightness(w: QWidget, x: int, y: int) -> int:
    c = w.grab().toImage().pixelColor(x, y)
    return c.red() + c.green() + c.blue()


def test_glow_paints_under_the_mouse_only_while_hovered(qapp):
    card = QFrame()
    card.setStyleSheet("QFrame{background:#17181f;}")
    card.resize(400, 200)
    g = pointer.glow(card)
    assert pointer.glow(card) is g                     # idempotent
    card.show()
    qapp.processEvents()
    _hover(card, QEvent.HoverLeave, 0, 0)              # wherever the platform cursor was
    base = _brightness(card, 100, 100)
    _hover(card, QEvent.HoverEnter, 100, 100)
    assert g.pos is not None
    assert _brightness(card, 100, 100) > base          # lit under the mouse …
    assert _brightness(card, 395, 195) <= base + 3     # … not far away
    _hover(card, QEvent.HoverLeave, 0, 0)
    assert g.pos is None and _brightness(card, 100, 100) == base
    card.close()


def test_glow_can_be_switched_off(qapp):
    card = QFrame()
    card.resize(200, 100)
    g = pointer.glow(card)
    card.show()
    pointer.set_glow_enabled(False)
    try:
        _hover(card, QEvent.HoverMove, 50, 50)
        assert g.pos is None
    finally:
        pointer.set_glow_enabled(True)
        card.close()
