"""Per-video translation brief (pipeline/brief.py) and the improved prompts.

The brief is a context sheet the translator reads with every batch of one video:
genre, who speaks to whom, xưng hô, names. These pin: how the transcript is
sampled, that a user note is marked authoritative, that the brief is re-used
instead of re-bought, that failure degrades instead of breaking the run, and that
it reaches the SYSTEM prompt of every pass — but never the cacheable user turn.
"""
from __future__ import annotations

import json

import pytest

from app.models import Cue, Job
from app.pipeline import brief, translate
from app.pipeline.srt import write_srt
from app.providers.translation._common import (
    REFINE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_refine_prompt,
    build_user_prompt,
    system_prompt_for,
)

CN = ["大家好，欢迎来到我的频道", "今天给大家做一道红烧肉", "老李说他最爱吃这个"]


@pytest.fixture()
def job(tmp_path):
    j = Job(id="b", work_dir=tmp_path)
    j.metadata = {"title": "家常红烧肉", "platform": "douyin", "uploader": "川妹子"}
    write_srt([Cue(i, i, i + 1, t) for i, t in enumerate(CN, 1)], j.cn_srt)
    return j


class FakeProvider:
    """Stands in for the draft translator: records rewrite() calls."""

    def __init__(self, reply="Thể loại & nội dung: vlog nấu ăn", fail=False, available=True):
        self.calls: list[tuple[str, str, int]] = []
        self._reply, self._fail, self._available = reply, fail, available

    def available(self):
        return self._available

    def rewrite(self, text, instruction, *, max_tokens=300, role=None):
        self.calls.append((text, instruction, max_tokens))
        if self._fail:
            raise RuntimeError("boom")
        return self._reply


@pytest.fixture()
def fake(monkeypatch):
    prov = FakeProvider()
    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: prov)
    return prov


# --- transcript sampling ------------------------------------------------------

def test_a_short_transcript_is_sent_whole():
    text, used = brief.sample_transcript(CN)
    assert text == "\n".join(CN) and used == 3


def test_a_long_transcript_is_sampled_from_start_middle_and_end():
    lines = [f"第{i}行内容比较长一点点" for i in range(2000)]
    text, used = brief.sample_transcript(lines, budget=600)
    assert len(text) <= 600 + 10                 # + the two "…" separators
    assert "第0行" in text                       # start
    assert "第666行" in text or "第667行" in text  # middle
    assert "第1333行" in text or "第1334行" in text  # final third
    assert text.count("…") == 2
    assert 0 < used < 2000


def test_blank_lines_are_ignored():
    text, used = brief.sample_transcript(["", "  ", "你好"])
    assert text == "你好" and used == 1


# --- the request --------------------------------------------------------------

def test_request_carries_metadata_transcript_and_an_authoritative_note(job):
    req = brief.build_request(job, CN, "Cô ấy gọi 老李 là anh Lý")
    assert "家常红烧肉" in req and "川妹子" in req
    assert "USER NOTE (authoritative" in req and "anh Lý" in req
    assert "老李说他最爱吃这个" in req
    assert "full" in req                          # whole transcript, not a sample


def test_request_without_a_note_has_no_note_section(job):
    assert "USER NOTE" not in brief.build_request(job, CN, None)


# --- build / persist / reuse --------------------------------------------------

def test_auto_brief_is_built_and_persisted(job, fake):
    out = brief.build_brief(job, "openrouter")
    assert out == "Thể loại & nội dung: vlog nấu ăn"
    assert len(fake.calls) == 1
    assert fake.calls[0][1] == brief.BRIEF_INSTRUCTION
    saved = json.loads((job.work_dir / brief.BRIEF_FILE).read_text(encoding="utf-8"))
    assert saved["brief"] == out and saved["source"] == "openrouter"
    assert brief.load_brief(job) == out


def test_unchanged_inputs_reuse_the_brief_without_a_new_call(job, fake):
    brief.build_brief(job, "openrouter", note="ghi chú")
    brief.build_brief(job, "openrouter", note="ghi chú")
    assert len(fake.calls) == 1


def test_a_changed_note_rebuilds(job, fake):
    brief.build_brief(job, "openrouter", note="cũ")
    brief.build_brief(job, "openrouter", note="mới")
    assert len(fake.calls) == 2


def test_a_changed_transcript_rebuilds(job, fake):
    brief.build_brief(job, "openrouter")
    write_srt([Cue(1, 0, 1, "完全不同的内容")], job.cn_srt)
    brief.build_brief(job, "openrouter")
    assert len(fake.calls) == 2


def test_force_rebuilds(job, fake):
    brief.build_brief(job, "openrouter")
    brief.build_brief(job, "openrouter", force=True)
    assert len(fake.calls) == 2


# --- degradation: optional pre-pass must never break the run ------------------

def test_llm_failure_with_a_note_falls_back_to_the_note_verbatim(job, monkeypatch):
    prov = FakeProvider(fail=True)
    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: prov)
    logs: list[str] = []
    out = brief.build_brief(job, "openrouter", note="Vlog của chị Hoa", log=logs.append)
    assert out == "Vlog của chị Hoa"
    assert brief.load_brief(job) == "Vlog của chị Hoa"
    assert any("Không tạo được" in m for m in logs)


def test_llm_failure_without_a_note_yields_no_brief(job, monkeypatch):
    prov = FakeProvider(fail=True)
    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: prov)
    assert brief.build_brief(job, "openrouter") is None
    assert not (job.work_dir / brief.BRIEF_FILE).exists()


def test_an_unavailable_provider_is_not_called(job, monkeypatch):
    prov = FakeProvider(available=False)
    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: prov)
    assert brief.build_brief(job, "openrouter", note="ghi chú") == "ghi chú"
    assert prov.calls == []


def test_passthrough_has_no_rewrite_so_the_note_is_used(job):
    """The real passthrough provider: no LLM, but a typed note still helps."""
    assert brief.build_brief(job, "passthrough", note="ghi chú") == "ghi chú"
    assert brief.build_brief(job, "passthrough", note=None, force=True) is None


def test_an_empty_reply_is_not_a_brief(job, monkeypatch):
    prov = FakeProvider(reply="   ")
    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: prov)
    assert brief.build_brief(job, "openrouter") is None


def test_a_corrupt_brief_file_is_ignored(job):
    (job.work_dir / brief.BRIEF_FILE).write_text("{not json", encoding="utf-8")
    assert brief.load_brief(job) is None


# --- prompt plumbing ----------------------------------------------------------

def test_system_prompt_is_unchanged_without_a_brief():
    assert system_prompt_for(SYSTEM_PROMPT, None) == SYSTEM_PROMPT
    assert system_prompt_for(SYSTEM_PROMPT, {"title": "T"}) == SYSTEM_PROMPT
    assert system_prompt_for(SYSTEM_PROMPT, {"_brief": "   "}) == SYSTEM_PROMPT


def test_brief_is_appended_to_the_system_prompt():
    s = system_prompt_for(SYSTEM_PROMPT, {"_brief": "Xưng hô: mình / các bạn"})
    assert s.startswith(SYSTEM_PROMPT)          # the cacheable prefix is untouched
    assert "VIDEO BRIEF" in s and "mình / các bạn" in s


def test_brief_never_leaks_into_the_user_turn():
    ctx = {"title": "T", "_brief": "BRIEF-TEXT"}
    assert "BRIEF-TEXT" not in build_user_prompt(["你好"], context=ctx)
    assert "BRIEF-TEXT" not in build_refine_prompt(["你好"], ["xin chào"], context=ctx)


def test_draft_and_refine_prompts_share_the_same_translation_rules():
    for rule in ("xưng hô", "Hán-Việt", "Output ONLY lines of the form `n| <vietnamese>`"):
        assert rule in SYSTEM_PROMPT and rule in REFINE_SYSTEM_PROMPT


def test_both_openai_compatible_passes_send_the_brief(monkeypatch):
    from types import SimpleNamespace

    from app.providers.registry import TRANSLATORS

    ds = TRANSLATORS["deepseek"]
    seen: list[str] = []

    class C:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kw):
            seen.append(kw["messages"][0]["content"])
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content="1| a"))])

    monkeypatch.setattr(ds, "_client", lambda: C())
    ctx = {"_brief": "BRIEF-XYZ"}
    ds.translate_texts(["你好"], context=ctx)
    ds.refine_texts(["你好"], ["xin chào"], context=ctx)
    assert all("BRIEF-XYZ" in s for s in seen) and len(seen) == 2


# --- end to end: translate() picks the brief up by itself ---------------------

def test_translate_loads_the_saved_brief_into_context(job, monkeypatch):
    (job.work_dir / brief.BRIEF_FILE).write_text(
        json.dumps({"brief": "Xưng hô: mình"}), encoding="utf-8")
    seen = {}

    def fake_cues(cues, **kw):
        seen.update(kw["context"])
        return [Cue(c.index, c.start, c.end, "vi") for c in cues]

    monkeypatch.setattr(translate, "translate_cues", fake_cues)
    logs: list[str] = []
    translate.translate(job, draft="passthrough", log=logs.append)
    assert seen["_brief"] == "Xưng hô: mình"
    assert any("brief ngữ cảnh" in m for m in logs)


def test_translate_without_a_brief_sends_none(job, monkeypatch):
    seen = {}

    def fake_cues(cues, **kw):
        seen.update(kw["context"])
        return [Cue(c.index, c.start, c.end, "vi") for c in cues]

    monkeypatch.setattr(translate, "translate_cues", fake_cues)
    translate.translate(job, draft="passthrough")
    assert "_brief" not in seen


def test_a_transient_failure_is_retried_once(job, monkeypatch):
    """The brief has no retry ladder of its own; one empty/transient reply must not
    cost the whole video its brief (seen on the stealth model, 2026-10-01)."""
    calls = {"n": 0}

    class Flaky(FakeProvider):
        def rewrite(self, text, instruction, *, max_tokens=300, role=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("OpenRouter returned an empty reply")
            return "Thể loại & nội dung: cover bài hát"

    prov = Flaky()
    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: prov)
    assert brief.build_brief(job, "openrouter") == "Thể loại & nội dung: cover bài hát"
    assert calls["n"] == 2
