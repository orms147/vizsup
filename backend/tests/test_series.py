"""Series profile ("Bộ phim", pipeline/series.py): detection from the 《…》 title, storage,
linking a project, and what it hands to the pipeline. No network."""
from __future__ import annotations

import json

import pytest

from app.config import settings
from app.models import Job
from app.pipeline import series

TITLE = "第4集 | 《末日校车》06 全员求生"


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    st = tmp_path / "storage"
    st.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", st)
    return st


def make_job(storage, name="ep1", title=TITLE):
    return Job(id=name, work_dir=storage / name, metadata={"title": title})


# --- (a) model and storage ------------------------------------------------------------

@pytest.mark.parametrize("title,name", [
    (TITLE, "末日校车"), ("《 末日  校车 》1-5 合集", "末日 校车"), ("EP12 no brackets", None),
    (None, None), ("《》x", None),
])
def test_detect_name(title, name):
    assert series.detect_name(title) == name


def test_slugify_is_filesystem_safe():
    s = series.slugify('a/b\\c:d*e?f"g<h>i|j k')
    assert not any(ch in s for ch in '\\/:*?"<>| ')
    assert series.slugify("   ") == "series"


def test_save_load_roundtrip(storage):
    p = series.create("末日校车")
    p.glossary = {"校车": "xe buýt trường"}
    p.characters = [{"zh": "林默", "vi": "Lâm Mặc", "gender": "nam", "note": "gọi Tiểu Vũ là em"}]
    p.brief = "Phim sinh tồn."
    p.style = {"size": 52}
    p.render = {"voice": "vi-VN-NamMinhNeural", "watermark": "Tên tôi"}
    p.pronounce = {"AI": "ây ai"}
    series.save(p)
    q = series.load(p.slug)
    assert q.to_dict() == p.to_dict()
    assert (storage / "_series" / p.slug / "series.json").exists()
    assert [x.slug for x in series.list_profiles()] == [p.slug]


def test_broken_profile_files_are_ignored(storage):
    d = storage / "_series" / "x"
    d.mkdir(parents=True)
    (d / "series.json").write_text("{nope", encoding="utf-8")
    (storage / "_series" / "y").mkdir()
    assert series.load("x") is None and series.list_profiles() == []


def test_series_folder_is_not_a_project(storage):
    from desktop import store

    series.create("末日校车")
    assert not store.is_project_dir(storage / "_series")


# --- linking a project ------------------------------------------------------------------

def test_auto_resolve_creates_and_links(storage):
    job = make_job(storage)
    p = series.resolve(job)
    assert p.name == "末日校车" and p.match == "末日校车"
    lk = series.link_of(job.work_dir)
    assert lk["slug"] == p.slug and lk["episode"] == 6 and lk["last"] == 6
    again = series.resolve(make_job(storage, "ep2", "第5集 | 《末日校车》07 x"))
    assert again.slug == p.slug and len(series.list_profiles()) == 1


def test_title_without_brackets_matches_an_existing_profile_by_text(storage):
    p = series.create("末日校车")
    job = make_job(storage, "ep3", "末日校车 第八集")
    assert series.resolve(job).slug == p.slug
    other = make_job(storage, "vlog", "我的一天 vlog")
    assert series.resolve(other) is None and series.link_of(other.work_dir) is None


def test_pick_and_none(storage):
    a = series.create("甲")
    series.create("乙")
    job = make_job(storage, "x", "无括号标题")
    assert series.resolve(job, a.slug).slug == a.slug
    assert series.link_of(job.work_dir)["slug"] == a.slug
    assert series.resolve(job, "auto").slug == a.slug               # the link is kept
    assert series.resolve(job, "none") is None
    assert series.link_of(job.work_dir) is None
    assert series.resolve(job, "missing-slug") is None                  # unknown pick → no series


def test_what_a_profile_contributes(storage):
    job = make_job(storage)
    p = series.resolve(job)
    p.characters = [{"zh": "林默", "vi": "Lâm Mặc", "gender": "nam", "note": "xưng anh"},
                    {"zh": "无译", "vi": ""}]
    p.glossary = {"林默": "Lâm Mặc (đội trưởng)", "丧尸": "zombie"}
    p.pronounce = {"zombie": "dôm bi"}
    p.brief = "Phim sinh tồn trên xe buýt."
    series.save(p)
    g = series.glossary_for(job)
    assert g == {"林默": "Lâm Mặc (đội trưởng)", "丧尸": "zombie"}   # explicit entry beats the character
    assert series.pronounce_for(job) == {"zombie": "dôm bi"}
    s = series.sheet(p)
    assert "Phim sinh tồn" in s and "林默 → Lâm Mặc (nam; xưng anh)" in s and "无译" in s
    assert series.summary(p) == "Bộ phim: 《末日校车》 · 2 thuật ngữ"
    assert series.summary(None) == ""
    assert len(series.sheet(series.Profile(slug="s", name="s", brief="x" * 5000))) <= series.SHEET_CHARS


def test_unlinked_job_gets_nothing(storage):
    job = make_job(storage, "solo", "plain")
    assert series.glossary_for(job) == {} and series.pronounce_for(job) == {}
    assert series.context_for(job) == {}
    assert series.learn(job) == {}


def test_link_file_is_json(storage):
    job = make_job(storage)
    series.resolve(job)
    assert json.loads((job.work_dir / series.LINK).read_text(encoding="utf-8"))["name"] == "末日校车"
