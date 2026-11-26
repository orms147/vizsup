"""«Lọc từ» kept for new projects: the series' profile or app-wide; copied at the start of a
run into a project without its own list, never into an approved one."""
from __future__ import annotations

from app.models import Job
from app.pipeline import approval, series, word_filter

RULES = [("giết", "dít")]


def _job(tmp_path, name="a"):
    d = tmp_path / name
    d.mkdir()
    return Job(id=name, work_dir=d)


def test_app_wide_list_reaches_a_new_project(tmp_path):
    a, b = _job(tmp_path, "a"), _job(tmp_path, "b")
    assert word_filter.kept_for_new(a) == ("", None)
    word_filter.keep_for_new(a, True, RULES)
    logs = []
    assert word_filter.inherit(b, log=logs.append)
    assert word_filter.load(b) == (True, RULES)
    assert "1 từ" in logs[0]
    assert not word_filter.inherit(b)                 # its own list now: left alone
    word_filter.keep_for_new(a, False, None)
    assert word_filter.kept_for_new(a) == ("", None)


def test_series_list_wins_and_stays_in_the_series(tmp_path):
    p = series.create("Phim A")
    a, other = _job(tmp_path, "a"), _job(tmp_path, "o")
    series._write_link(a, p)
    word_filter.keep_for_new(a, True, RULES)
    assert series.load(p.slug).word_filter["rules"] == [["giết", "dít"]]
    assert word_filter.kept_for_new(other) == ("", None)   # not app-wide
    b = _job(tmp_path, "b")
    series._write_link(b, p)
    assert word_filter.inherit(b) and word_filter.load(b) == (True, RULES)


def test_an_approved_project_is_never_changed(tmp_path, monkeypatch):
    a, b = _job(tmp_path, "a"), _job(tmp_path, "b")
    word_filter.keep_for_new(a, True, RULES)
    monkeypatch.setattr(approval, "is_approved", lambda job: True)
    assert not word_filter.inherit(b)
    assert not (b.work_dir / word_filter.FILE).exists()
