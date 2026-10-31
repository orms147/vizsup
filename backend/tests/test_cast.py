"""Bộ phim: names + xưng hô learnt from the newest approved episode (pipeline/cast.py), and
the series sheet handed to the next episode's brief as fixed input."""
from __future__ import annotations

import json

import pytest

from app.config import settings
from app.models import Cue, Job
from app.pipeline import approval, brief, cast, series
from app.pipeline.srt import write_srt

REPLY = json.dumps({
    "characters": [
        {"zh": "顾哥", "vi": "anh Cố", "gender": "nam", "xung_ho": "xưng \"tôi\"; gọi các nữ sinh \"các cô\""},
        {"zh": "殷小飞", "vi": "Ân Tiểu Phi", "gender": "nữ", "xung_ho": "xưng \"tôi\", gọi 顾哥 \"anh Cố\""},
    ],
    "chung": "giọng hệ thống xưng \"tôi\", gọi người chơi \"bạn\"",
}, ensure_ascii=False)


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    st = tmp_path / "storage"
    st.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", st)
    return st


class FakeLLM:
    def __init__(self, reply=REPLY):
        self.reply = reply
        self.calls: list[tuple[str, str, str]] = []

    def available(self) -> bool:
        return True

    def rewrite(self, text, instruction, *, max_tokens=300, role="shorten"):
        self.calls.append((text, instruction, role))
        return self.reply


@pytest.fixture()
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr("app.providers.registry.get_translator", lambda _name: fake)
    return fake


def episode(storage, name, num, *, approved=True, cn=("顾哥，走吧", "好的"),
            vi=("Anh Cố, đi thôi.", "Được.")) -> Job:
    j = Job(id=name, work_dir=storage / name, metadata={"title": f"第{num}集 | 《末日校车》{num:02d}"})
    write_srt([Cue(i, i, i + 1, t) for i, t in enumerate(cn, 1)], j.cn_srt)
    write_srt([Cue(i, i, i + 1, t) for i, t in enumerate(vi, 1)], j.vi_srt)
    series.resolve(j)
    if approved:
        approval.approve(j, cues=len(vi))
    return j


def fresh(storage, name, num) -> Job:
    """The next episode, about to be translated (Chinese only)."""
    j = Job(id=name, work_dir=storage / name, metadata={"title": f"第{num}集 | 《末日校车》{num:02d}"})
    write_srt([Cue(1, 0, 1, "上车")], j.cn_srt)
    series.resolve(j)
    return j


# --- pure ------------------------------------------------------------------------------------

def test_pairs_follow_time_not_index():
    cn = [Cue(1, 0, 1, "你好"), Cue(2, 1, 2, "顾哥"), Cue(3, 2, 3, "走吧")]
    vi = [Cue(1, 0, 2, "Chào anh Cố."), Cue(2, 2, 3, "Đi thôi."), Cue(3, 5, 6, "Ừ.")]
    assert cast.pairs(cn, vi) == ["你好 顾哥 → Chào anh Cố.", "走吧 → Đi thôi.", "— → Ừ."]


def test_parse_cast_tolerates_fences_and_drops_bad_rows():
    reply = ("```json\n" + json.dumps({"characters": [
        {"zh": "顾哥", "vi": "anh Cố", "gender": "Nam", "xung_ho": " xưng  tôi "},
        {"zh": "", "vi": "x"}, {"zh": "某人", "vi": ""}, "junk",
        {"zh": "黄嘉怡", "vi": "Hoàng Gia Di", "gender": "?"}],
        "chung": "  "}, ensure_ascii=False) + "\n```")
    out = cast.parse_cast(reply)
    assert out["characters"] == [
        {"zh": "顾哥", "vi": "anh Cố", "gender": "nam", "xung_ho": "xưng tôi"},
        {"zh": "黄嘉怡", "vi": "Hoàng Gia Di", "gender": "", "xung_ho": ""}]
    assert out["chung"] == ""
    assert cast.parse_cast("không có gì") == {"characters": [], "chung": ""}
    assert cast.parse_cast("{bad json}") == {"characters": [], "chung": ""}


def test_merge_adds_new_people_and_never_overrides_the_owner():
    p = series.Profile(slug="s", name="s", brief="Phim zombie.", characters=[
        {"zh": "顾哥", "vi": "chú Cố", "gender": "", "note": ""}])
    added = cast.merge(p, cast.parse_cast(REPLY))
    assert added == ["Ân Tiểu Phi"]
    gu, yin = p.characters
    assert gu["vi"] == "chú Cố"                              # the owner's rendering stays
    assert gu["note"].startswith("xưng") and gu["gender"] == "nam"   # empty fields filled
    assert yin["auto"] is True and yin["gender"] == "nữ"
    assert p.brief == "Phim zombie."                           # a brief of the owner's stays
    q = series.Profile(slug="t", name="t")
    cast.merge(q, cast.parse_cast(REPLY))
    assert "người chơi" in q.brief                             # an empty brief is filled


# --- learning ------------------------------------------------------------------------------

def test_next_episode_learns_once_from_the_newest_approved_one(storage, llm):
    episode(storage, "ep8", 8)
    episode(storage, "ep9", 9)
    episode(storage, "ep11", 11)                    # a LATER episode is not read for ep 10
    episode(storage, "ep7", 7, approved=False)      # not approved → never read
    j = fresh(storage, "ep10", 10)
    logs: list[str] = []
    added = cast.learn(j, "openrouter", log=logs.append)
    assert added == ["anh Cố", "Ân Tiểu Phi"]
    assert len(llm.calls) == 1
    text, instruction, role = llm.calls[0]
    assert role == "brief" and "APPROVED" in instruction
    assert "顾哥，走吧 → Anh Cố, đi thôi." in text
    assert "học xưng hô từ tập 9" in logs[0]
    p = series.profile_of(j)
    assert [c["zh"] for c in p.characters] == ["顾哥", "殷小飞"]
    assert json.loads((series.root() / p.slug / cast.LEARNED).read_text(encoding="utf-8"))["from"] == ["ep9"]
    # the sheet now carries the fixed xưng hô for the translator
    assert "anh Cố" in series.sheet(p) and "các cô" in series.sheet(p)

    cast.learn(j, "openrouter")                    # next run: ep 8 is the newest unread
    assert len(llm.calls) == 2 and "KNOWN SHEET" in llm.calls[1][0]
    assert [c["zh"] for c in series.profile_of(j).characters] == ["顾哥", "殷小飞"]  # no dupes
    cast.learn(j, "openrouter")                    # nothing left → no call
    assert len(llm.calls) == 2


def test_no_series_no_approved_episode_or_a_failing_model_is_harmless(storage, llm, monkeypatch):
    solo = Job(id="solo", work_dir=storage / "solo", metadata={"title": "plain"})
    write_srt([Cue(1, 0, 1, "x")], solo.cn_srt)
    assert cast.learn(solo, "openrouter") == []
    j = fresh(storage, "ep2", 2)
    assert cast.learn(j, "openrouter") == [] and llm.calls == []

    episode(storage, "ep1", 1)

    def boom(*_a, **_k):
        raise RuntimeError("offline")
    monkeypatch.setattr(llm, "rewrite", boom)
    logs: list[str] = []
    assert cast.learn(j, "openrouter", log=logs.append) == []
    assert "Không học được" in logs[0]
    assert not (series.root() / series.profile_of(j).slug / cast.LEARNED).exists()  # retried later


def test_the_series_sheet_reaches_the_brief_and_rebuilds_it(storage, monkeypatch):
    fake = FakeLLM("Thể loại & nội dung: phim zombie.")
    monkeypatch.setattr("app.providers.registry.get_translator", lambda _name: fake)
    j = fresh(storage, "ep10", 10)
    brief.build_brief(j, "openrouter", observed="nam + nữ", fixed="")
    assert "SERIES SHEET" not in fake.calls[0][0]
    brief.build_brief(j, "openrouter", observed="nam + nữ", fixed="")
    assert len(fake.calls) == 1                                     # unchanged → re-used
    brief.build_brief(j, "openrouter", observed="nam + nữ",
                      fixed="Nhân vật (giữ nguyên ở mọi tập):\n- 顾哥 → anh Cố")
    assert len(fake.calls) == 2                                     # a new sheet → rebuilt
    assert "SERIES SHEET (fixed for every episode of this series):\n" in fake.calls[1][0]
    assert "- 顾哥 → anh Cố" in fake.calls[1][0]
    assert "SERIES SHEET, when present" in fake.calls[1][1]
