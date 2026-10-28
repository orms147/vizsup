"""Fixes from the 2026-10-29 fresh-eyes review of the overnight merges. Each test fails on the
code it replaces (the failing scenario is in its docstring)."""
from __future__ import annotations

import json
import os

import pytest

from app import catalog
from app.config import settings
from app.pipeline import postcopy, subcsv


# --- catalog ----------------------------------------------------------------------------------

def _write_cache(folder, model: str, mtime: float) -> None:
    (folder / "_cache").mkdir(parents=True, exist_ok=True)
    p = folder / "_cache" / "model_catalog.json"
    p.write_text(json.dumps({"schema": 1, "role_defaults": {"draft": model}}), encoding="utf-8")
    os.utime(p, (mtime, mtime))


def test_the_catalog_memo_is_per_storage_not_just_per_mtime(tmp_path, monkeypatch):
    """Two storages whose cache files carry the same mtime (a copied storage, a restored
    backup, two test dirs) used to share ONE memo: the second one got the first one's models."""
    a, b = tmp_path / "a", tmp_path / "b"
    _write_cache(a, "x/one", 1_700_000_000.0)
    _write_cache(b, "x/two", 1_700_000_000.0)
    monkeypatch.setattr(catalog, "_memo", None)
    monkeypatch.setattr(settings, "vizsup_storage_dir", a)
    assert catalog.role_default("draft") == "x/one"
    monkeypatch.setattr(settings, "vizsup_storage_dir", b)
    assert catalog.role_default("draft") == "x/two"


@pytest.mark.parametrize("fetched", ["yesterday", 1e30, -5, [], None])
def test_summary_survives_a_bad_fetched_stamp(tmp_path, monkeypatch, fetched):
    """Settings → AI calls catalog.summary() while building the page; a cache file with a
    junk ``_fetched`` raised ValueError / OverflowError / OSError and the page never opened."""
    monkeypatch.setattr(catalog, "_memo", None)
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    (tmp_path / "_cache").mkdir()
    (tmp_path / "_cache" / "model_catalog.json").write_text(
        json.dumps({"schema": 1, "_fetched": fetched, "updated": "2026-10-29"}), encoding="utf-8")
    assert "Danh sách model" in catalog.summary()


@pytest.mark.parametrize("url", [
    "https://github.com/orms147/vizsup/../../attacker/repo/releases",
    "https://github.com/orms147/vizsup/@evil.example",
    "https://github.com/orms147/vizsup/releases?x=<script>",
    "http://github.com/orms147/vizsup/releases",
])
def test_catalog_app_url_must_stay_on_the_vizsup_repository(url):
    """``startswith(RELEASES)`` accepted a path that climbs out of the repository."""
    problems = catalog.validate({"schema": 1, "app": {"latest": "9.9", "url": url}}, None)
    assert problems and "app.url" in problems[0]


def test_catalog_app_url_normal_links_still_pass():
    for url in ("https://github.com/orms147/vizsup/releases",
                "https://github.com/orms147/vizsup/releases/tag/v1.2.0"):
        assert not catalog.validate({"schema": 1, "app": {"latest": "9.9", "url": url}}, None)


@pytest.mark.parametrize("field", ["label", "hint"])
def test_catalog_text_cannot_carry_html(field):
    """Package label / hint go into QLabels that render rich text: ``<a href>`` / ``<img>``
    from a catalog would draw clickable fake UI."""
    bad = catalog.validate({"schema": 1, "presets": {"economy": {field: "<a href='x'>Cập nhật</a>"}}},
                           None)
    assert bad and f"economy.{field}" in bad[0]
    assert not catalog.validate({"schema": 1, "presets": {"economy": {field: "Tiết kiệm 2"}}}, None)


# --- postcopy -----------------------------------------------------------------------------------

def test_parse_keeps_a_single_title_string_whole():
    """``"titles": "Một tiêu đề"`` (a model that forgot the array) became ["M", "ộ", "t", …]."""
    out = postcopy.parse('{"titles": "Một tiêu đề hay", "hashtags": "#vietsub"}')
    assert out["titles"] == ["Một tiêu đề hay"] and out["hashtags"] == ["#vietsub"]


@pytest.mark.parametrize("reply", ['{"titles": 5}', '{"titles": null, "hashtags": 7}',
                                   '{"titles": {"a": 1}, "hashtags": {"b": 2}}',
                                   '{"titles": [["x"], null, {"y": 1}], "hashtags": [[1]]}'])
def test_parse_never_raises_on_odd_shapes(reply):
    """``"hashtags": 7`` / ``"titles": 5`` raised TypeError inside the dialog's callback."""
    assert postcopy.parse(reply) == {"titles": [], "hashtags": []}


def test_parse_normal_answers_unchanged():
    out = postcopy.parse('```json\n{"titles": ["A", "B", "C", "D"], "hashtags": ["#Sinh Ton", "x"]}\n```')
    assert out["titles"] == ["A", "B", "C"] and out["hashtags"] == ["#sinhton", "#x"]


# --- subcsv ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell", ["inf", "-inf", "1e999", "nan"])
def test_a_non_finite_index_is_a_csv_error_not_a_traceback(cell):
    """``int(float("inf"))`` raised OverflowError past ``except ValueError`` — the import slot
    died with a traceback instead of the dialog's reason."""
    with pytest.raises(subcsv.CsvError, match="index"):
        subcsv.read_rows(f"index,start,end,zh,vi\n{cell},0,1,a,b\n")


def test_a_csv_with_spreadsheet_error_cells_is_refused_not_imported():
    """Excel / Sheets turn a cell like "- Anh đi đâu?" or "=== " into a failed formula and save
    "#NAME?" — importing that file replaced the subtitle with "#NAME?" (data loss)."""
    from types import SimpleNamespace as NS

    cues = [NS(start=0.0, end=1.0, zh="你好", vi="- Anh đi đâu?", locked=False),
            NS(start=1.0, end=2.0, zh="再见", vi="Tạm biệt", locked=False)]
    good = subcsv.export_text(cues)
    assert len(subcsv.match(cues, subcsv.read_rows(good))) == 2
    for err in ("#NAME?", "#VALUE!", "#REF!", "#ERROR!", " #N/A "):
        text = good.replace("- Anh đi đâu?", err)
        with pytest.raises(subcsv.CsvError, match="Excel"):
            subcsv.match(cues, subcsv.read_rows(text))
    legit = good.replace("Tạm biệt", "#1 Tạm biệt")           # a line that merely starts with '#'
    assert len(subcsv.match(cues, subcsv.read_rows(legit))) == 2
