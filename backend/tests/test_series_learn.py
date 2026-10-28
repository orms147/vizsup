"""Bộ phim (c, d): what an approved episode teaches the series, and the previous episode's
last lines as continuity context for the next one."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.models import Cue, Job
from app.pipeline import approval, batch, series
from app.pipeline.srt import write_srt


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    st = tmp_path / "storage"
    st.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", st)
    return st


def episode(storage, name, num, *, vi=("Chào.",), cn=("你好",), name_zh="末日校车", last=None,
            approved=True) -> Job:
    title = f"第{num}集 | 《{name_zh}》{num:02d}" if last is None else f"《{name_zh}》{num}-{last}"
    j = Job(id=name, work_dir=storage / name, metadata={"title": title})
    write_srt([Cue(i, i, i + 1, t) for i, t in enumerate(cn, 1)], j.cn_srt)
    write_srt([Cue(i, i, i + 1, t) for i, t in enumerate(vi, 1)], j.vi_srt)
    series.resolve(j)
    if approved:
        approval.approve(j, cues=len(vi))
    return j


def write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# --- (c) learn -----------------------------------------------------------------------------

def test_kept_suggested_terms_join_the_series_and_earlier_wins(storage):
    j = episode(storage, "ep1", 1, cn=("林默来了", "丧尸出现", "校车开走"),
                vi=("Lâm Mặc đến rồi.", "Thây ma xuất hiện.", "Xe buýt chạy đi."), approved=False)
    p = series.profile_of(j)
    p.glossary = {"校车": "xe trường"}                      # an earlier episode's rendering
    series.save(p)
    write(j.work_dir / "glossary.suggested.json",
          {"林默": "Lâm Mặc",          # kept in vi.srt → learned
           "丧尸": "zombie",           # the user changed it to "thây ma" → NOT learned
           "校车": "xe buýt",          # already in the series → earlier wins
           "老师": "Lão sư"})           # zh not in this episode → not learned
    approval.approve(j, cues=3)                                # the hook
    q = series.profile_of(j)
    assert q.glossary == {"校车": "xe trường", "林默": "Lâm Mặc"}
    rep = json.loads((j.work_dir / series.LEARNED).read_text(encoding="utf-8"))
    assert rep["added"] == {"林默": "Lâm Mặc"} and rep["kept"] == 1


def test_first_style_is_remembered_but_not_replaced(storage):
    j = episode(storage, "ep1", 1, approved=False)
    write(j.work_dir / "style.json", {"size": 50})
    approval.approve(j)
    assert series.profile_of(j).style == {"size": 50}
    write(j.work_dir / "style.json", {"size": 99})
    approval.approve(j)
    assert series.profile_of(j).style == {"size": 50}


def test_learning_without_a_series_or_with_broken_files_is_harmless(storage):
    solo = Job(id="solo", work_dir=storage / "solo", metadata={"title": "plain"})
    write_srt([Cue(1, 0, 1, "x")], solo.vi_srt)
    assert approval.approve(solo)                              # no series → nothing happens
    j = episode(storage, "ep1", 1, approved=False)
    (j.work_dir / "glossary.suggested.json").write_text("{bad", encoding="utf-8")
    approval.approve(j)
    assert series.profile_of(j).glossary == {}


def test_first_real_render_sets_the_series_render_settings(storage):
    j = episode(storage, "ep1", 1, approved=False)
    batch.save_opts(j.work_dir, {"voice": "vi-VN-NamMinhNeural", "preview": True})
    assert series.profile_of(j).render == {}                   # a quick proof is not a setting
    batch.save_opts(j.work_dir, {"voice": "vi-VN-NamMinhNeural", "watermark": "Tôi",
                                 "burn_cues": [1], "_x": 1})
    assert series.profile_of(j).render == {"voice": "vi-VN-NamMinhNeural", "watermark": "Tôi"}
    batch.save_opts(j.work_dir, {"voice": "other"})
    assert series.profile_of(j).render["voice"] == "vi-VN-NamMinhNeural"


# --- (d) previous episode ---------------------------------------------------------------------

def test_previous_lines_come_from_the_latest_earlier_approved_episode(storage):
    lines5 = tuple(f"Câu {i}." for i in range(1, 13))
    episode(storage, "ep4", 4, vi=("Cũ hơn.",))
    episode(storage, "ep5", 5, vi=lines5)
    cur = episode(storage, "ep6", 6, approved=False)
    assert series.previous_lines(cur) == list(lines5[-8:])           # last 8 only
    assert series.previous_lines(cur, 3) == ["Câu 10.", "Câu 11.", "Câu 12."]
    ctx = series.context_for(cur)
    assert ctx["_prev"] == list(lines5[-8:])


def test_unapproved_or_later_or_other_series_episodes_are_ignored(storage):
    episode(storage, "ep5", 5, vi=("Chưa duyệt.",), approved=False)
    episode(storage, "ep8", 8, vi=("Tập sau.",))
    episode(storage, "other", 5, vi=("Phim khác.",), name_zh="别的剧")
    cur = episode(storage, "ep6", 6, approved=False)
    assert series.previous_lines(cur) == []
    assert "_prev" not in series.context_for(cur)


def test_an_edited_episode_after_approval_is_no_longer_a_source(storage):
    ep5 = episode(storage, "ep5", 5, vi=("Duyệt rồi.",))
    cur = episode(storage, "ep6", 6, approved=False)
    assert series.previous_lines(cur) == ["Duyệt rồi."]
    write_srt([Cue(1, 0, 1, "Sửa sau khi duyệt.")], ep5.vi_srt)
    assert series.previous_lines(cur) == []


def test_a_range_episode_counts_by_its_last_number(storage):
    episode(storage, "pack", 1, vi=("Gói 1-5.",), last=5)
    episode(storage, "ep6", 6, vi=("Tập 6.",))
    cur = episode(storage, "ep7", 7, approved=False)
    assert series.previous_lines(cur) == ["Tập 6."]
    cur6 = Job(id="ep6", work_dir=storage / "ep6", metadata={"title": "x"})
    assert series.previous_lines(cur6) == ["Gói 1-5."]


def test_no_episode_number_means_no_previous(storage):
    p = series.create("末日校车")
    j = Job(id="x", work_dir=storage / "x", metadata={"title": "《末日校车》特辑"})
    series.resolve(j, p.slug)
    assert series.link_of(j.work_dir)["episode"] is None
    assert series.previous_lines(j) == []
