"""Each line's voice (man / woman, from its pitch) rides in the translate and refine prompts so
the models know who speaks — the xưng hô errors a Sonnet 5.5 review of 《末日校车》 found were
mostly a girl's line given the man's pronouns and back (2026-11-01)."""
from __future__ import annotations

import json

from app.models import Cue, Job
from app.pipeline import translate, voices
from app.providers.translation._common import _ranges, build_refine_prompt, build_user_prompt, voice_hint


def test_ranges_compress_runs():
    assert _ranges([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8"
    assert _ranges([4]) == "4"


def test_hint_lists_both_voices_and_skips_unsure_lines():
    hint = voice_hint({"_voices": ["m", "m", None, "f", "f", "m"]}, 6)
    listed = hint.split("unsure): ")[1].split(". Use")[0]
    assert listed == "a man's voice: 1-2, 6; a woman's voice: 4-5"     # line 3 is unsure


def test_no_hint_when_absent_misaligned_or_all_unsure():
    assert voice_hint(None, 2) == ""
    assert voice_hint({"_voices": ["m", "f"]}, 3) == ""           # never a shifted list
    assert voice_hint({"_voices": [None, None]}, 2) == ""


def test_both_prompts_carry_the_hint_and_never_the_key():
    ctx = {"_voices": ["m", "f"]}
    draft = build_user_prompt(["a", "b"], context=ctx)
    refine = build_refine_prompt(["a", "b"], ["va", "vb"], context=ctx)
    for p in (draft, refine):
        assert "a man's voice: 1" in p and "a woman's voice: 2" in p
        assert "_voices" not in p
    assert build_user_prompt(["a"]) == build_user_prompt(["a"], context={"title": None})


def test_each_batch_gets_its_own_slice():
    tags = ["m"] * translate.BATCH + ["f", None]
    ctx = translate._batch_context({"title": "T", "_voices": tags}, 1)
    assert ctx["_voices"] == ["f", None] and ctx["title"] == "T"
    assert translate._batch_context({"title": "T"}, 1) == {"title": "T"}


class _Recorder:
    def __init__(self):
        self.seen: list[tuple[str, list]] = []

    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        self.seen.append(("draft", list((kw.get("context") or {}).get("_voices") or [])))
        return [f"v{t}" for t in texts]

    def refine_texts(self, cn, vi, **kw):
        self.seen.append(("refine", list((kw.get("context") or {}).get("_voices") or [])))
        return list(vi)


def _job(tmp_path, n=3):
    (tmp_path / "cn.srt").write_text("".join(
        f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n句{i}\n\n" for i in range(1, n + 1)),
        encoding="utf-8")
    (tmp_path / "metadata.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
    return Job(id="j", work_dir=tmp_path, url="", metadata={"title": "t"})


def test_translate_passes_the_tags_to_both_passes(tmp_path, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(translate, "get_translator", lambda name: rec)
    monkeypatch.setattr(voices, "line_hints", lambda job, cues, log=None: ["m", "f", None])
    logs: list[str] = []
    translate.translate(_job(tmp_path), draft="fake", refine="fake", glossary={},
                        log=logs.append, voice_hints=True)
    assert rec.seen == [("draft", ["m", "f", None]), ("refine", ["m", "f", None])]
    assert any("1 câu giọng nam, 1 câu giọng nữ" in m for m in logs)


def test_off_or_no_answer_leaves_the_prompt_alone(tmp_path, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(translate, "get_translator", lambda name: rec)
    monkeypatch.setattr(voices, "line_hints", lambda job, cues, log=None: None)
    translate.translate(_job(tmp_path), draft="fake", refine="fake", glossary={}, voice_hints=True)
    (tmp_path / "vi.srt").unlink()
    monkeypatch.setattr(voices, "line_hints", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    translate.translate(_job(tmp_path), draft="fake", refine="fake", glossary={})
    assert all(tags == [] for _stage, tags in rec.seen)


def test_one_kind_of_voice_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(voices, "_audio_source", lambda job, log=None: tmp_path / "a.wav")
    monkeypatch.setattr(voices, "_decode", lambda src: None)
    monkeypatch.setattr(voices, "guess_cues", lambda x, cues: ["f", None, "f"])
    job = _job(tmp_path)
    assert voices.line_hints(job, [Cue(index=1, start=0, end=1, text="a")] * 3) is None
    monkeypatch.setattr(voices, "guess_cues", lambda x, cues: ["f", "m", None])
    assert voices.line_hints(job, [Cue(index=1, start=0, end=1, text="a")] * 3) == ["f", "m", None]


def test_a_failure_is_logged_never_raised(tmp_path, monkeypatch):
    def boom(job, log=None):
        raise OSError("no ffmpeg")

    monkeypatch.setattr(voices, "_audio_source", boom)
    logs: list[str] = []
    assert voices.line_hints(_job(tmp_path), [], log=logs.append) is None
    assert any("Không nghe được giọng" in m for m in logs)
