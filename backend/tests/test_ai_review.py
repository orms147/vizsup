"""E-5 "AI soát": the QA pass flags lines with a note + fix, never edits vi.srt, and a
finding disappears once the line has been changed."""
from __future__ import annotations

import json

import pytest

from app.models import Cue, Job
from app.pipeline import qa, review
from app.pipeline.srt import parse_srt, write_srt


@pytest.fixture()
def job(tmp_path):
    j = Job(id="q", work_dir=tmp_path / "q")
    write_srt([Cue(1, 0.0, 2.0, "有好多的话还来不及兑现"), Cue(2, 2.0, 4.0, "你就不见")], j.cn_srt)
    write_srt([Cue(1, 0.0, 2.0, "Bao nhiêu lời chưa kịp nói"), Cue(2, 2.0, 4.0, "bạn đã biến mất")],
              j.vi_srt)
    return j


class FakeQA:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def available(self):
        return True

    def rewrite(self, body, instruction, *, max_tokens=300, role=None):
        self.calls.append((body, role))
        return self.reply


def use(monkeypatch, fake):
    monkeypatch.setattr(qa, "get_translator", lambda name: fake)


def test_parse_issues_keeps_only_known_kinds_and_valid_lines():
    reply = ('noise {"issues":[{"i":1,"kind":"meaning","note":"兑现 là thực hiện","fix":"x"},'
             '{"i":9,"kind":"meaning"},{"i":2,"kind":"vibes"}]} trailing')
    assert qa.parse_issues(reply, 2) == [{"i": 1, "kind": "meaning",
                                         "note": "兑现 là thực hiện", "fix": "x"}]
    assert qa.parse_issues("not json", 2) == []


def test_review_writes_findings_by_time_and_never_edits_the_subtitles(job, monkeypatch):
    fake = FakeQA('{"issues":[{"i":1,"kind":"meaning","note":"兑现 = thực hiện lời hứa",'
                  '"fix":"Bao lời hứa chưa kịp thực hiện"}]}')
    use(monkeypatch, fake)
    before = job.vi_srt.read_text(encoding="utf-8")
    assert qa.review(job, "openrouter", context={"_brief": "Bài hát chia tay"}) == 1
    assert job.vi_srt.read_text(encoding="utf-8") == before
    body, role = fake.calls[0]
    assert role == "qa" and "ZH: 有好多的话还来不及兑现" in body and "Bài hát chia tay" in body
    found = qa.load(job)
    assert found[0]["start"] == 0.0 and found[0]["vi"] == "Bao nhiêu lời chưa kịp nói"


def test_a_fix_identical_to_the_line_is_not_a_finding(job, monkeypatch):
    use(monkeypatch, FakeQA('{"issues":[{"i":2,"kind":"unnatural","note":"","fix":"bạn đã biến mất"}]}'))
    assert qa.review(job, "openrouter") == 0


def test_a_failing_model_never_breaks_the_run(job, monkeypatch):
    class Boom(FakeQA):
        def rewrite(self, *a, **k):
            raise RuntimeError("503")

    use(monkeypatch, Boom(""))
    assert qa.review(job, "openrouter") == 0


def test_the_flag_follows_the_line_and_disappears_once_edited(job):
    findings = [{"start": 0.0, "end": 2.0, "vi": "Bao nhiêu lời chưa kịp nói", "kind": "meaning",
                 "note": "n", "fix": "f"}]
    cues = parse_srt(job.vi_srt)
    assert "ai" in review.cue_issues(cues, qa_findings=findings)[0]
    cues[0].text = "Bao lời hứa chưa kịp thực hiện"
    assert "ai" not in review.cue_issues(cues, qa_findings=findings)[0]
    (job.work_dir / "qa_report.json").write_text(json.dumps(findings), encoding="utf-8")
    assert qa.load(job) == findings
