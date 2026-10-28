"""Thư viện status chips + "Theo tập" sort (UX audit L1): Chưa duyệt / Đã duyệt / Đã dựng /
Cần dựng lại (approved again after the last render) as filters with counts, combined with the
search; episodes of one series sort by episode number. Tmp storage only."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("PySide6.QtWidgets")

from test_library import _wait, make_job  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    from app.config import settings

    base = tmp_path / "storage"
    base.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", base)
    return base


def _approve(base: Path, jid: str) -> None:
    from app.models import Job
    from app.pipeline import approval

    approval.approve(Job(id=jid, work_dir=base / jid))


def _fill(base: Path):
    """Five projects: one per chip (+ one unfinished)."""
    make_job(base, "aaaaaaaaaaaa", title="Chờ duyệt")                          # review
    make_job(base, "bbbbbbbbbbbb", title="Đã duyệt")                           # approved
    _approve(base, "bbbbbbbbbbbb")
    make_job(base, "cccccccccccc", title="Đã dựng", output=True)               # rendered, current
    make_job(base, "dddddddddddd", title="Cần dựng lại", output=True)          # rendered
    out = base / "dddddddddddd" / "output.mp4"
    old = time.time() - 3600
    os.utime(out, (old, old))
    _approve(base, "dddddddddddd")                                             # …approved after
    make_job(base, "eeeeeeeeeeee", title="Dở dang", vi=False)                  # unfinished


@pytest.fixture()
def view(qapp, storage):
    from desktop.views.library_view import LibraryView

    _fill(storage)
    v = LibraryView()
    v.resize(1100, 800)
    v.show()
    v.refresh()
    _wait(lambda: len(v._details) == 5)
    qapp.processEvents()
    yield v
    v.shutdown()
    v.close()


def test_categories_and_counts(view):
    assert view.chip_counts() == {"all": 5, "unapproved": 1, "approved": 1, "rendered": 1,
                                  "stale": 1, "unfinished": 1}
    assert view.chip_btns["stale"].text() == "Cần dựng lại 1"
    assert view._rows["dddddddddddd"].status.text() == "Cần dựng lại"
    assert view._rows["cccccccccccc"].status.text() == "Đã dựng"


def test_each_chip_filters_the_list(view):
    want = {"unapproved": "aaaaaaaaaaaa", "approved": "bbbbbbbbbbbb", "rendered": "cccccccccccc",
            "stale": "dddddddddddd", "unfinished": "eeeeeeeeeeee"}
    for chip, iid in want.items():
        view.set_chip(chip)
        assert view.visible_ids() == [iid], chip
    view.set_chip("all")
    assert len(view.visible_ids()) == 5


def test_chip_and_search_combine_and_the_empty_message_says_why(view):
    view.set_chip("rendered")
    view.search.setText("duyet")                       # matches "Đã duyệt" / "Chờ duyệt", not Đã dựng
    assert view.visible_ids() == []
    assert "khớp" in view.empty.text()
    view.search.setText("")
    view.set_chip("approved")
    view.search.setText("zzz")
    assert view.visible_ids() == []
    view.search.setText("")
    assert view.visible_ids() == ["bbbbbbbbbbbb"]


def test_a_chip_without_projects_is_disabled_and_chips_follow_the_details(qapp, storage):
    from desktop.views.library_view import LibraryView

    make_job(storage, "aaaaaaaaaaaa", title="Chỉ chờ duyệt")
    v = LibraryView()
    v.refresh()
    _wait(lambda: len(v._details) == 1)
    assert not v.chip_btns["rendered"].isEnabled() and not v.chip_btns["stale"].isEnabled()
    assert v.chip_btns["unapproved"].isEnabled()
    assert v.chip_btns["unfinished"].isHidden()
    v.shutdown()


def test_episode_sort_numbers_within_a_series(qapp, storage):
    from desktop.views.library_view import LibraryView, episode_sort_key

    make_job(storage, "aaaaaaaaaaaa", title="第10集 | 《末日校车》10")
    make_job(storage, "bbbbbbbbbbbb", title="第2集 | 《末日校车》2")
    make_job(storage, "cccccccccccc", title="第1集 | 《别的剧》1")
    make_job(storage, "dddddddddddd", title="第1集 | 《末日校车》1")
    make_job(storage, "eeeeeeeeeeee", title="Video không số")
    v = LibraryView()
    v.refresh()
    v.set_sort("ep")
    order = v.order()
    bus = [i for i in order if i in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "dddddddddddd")]
    assert bus == ["dddddddddddd", "bbbbbbbbbbbb", "aaaaaaaaaaaa"]        # 1, 2, 10 — not 1, 10, 2
    assert abs(order.index("dddddddddddd") - order.index("aaaaaaaaaaaa")) == 2   # one series together
    numbered = episode_sort_key({"title": "Phim A EP5", "mtime": 1})
    bare = episode_sort_key({"title": "Phim A", "mtime": 9})
    assert numbered < bare                                       # numbered first within a series
    v.shutdown()
