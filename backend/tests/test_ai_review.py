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


def _long_job(tmp_path, n: int) -> Job:
    j = Job(id="long", work_dir=tmp_path / "long")
    write_srt([Cue(k + 1, k * 2.0, k * 2.0 + 2.0, f"中文第{k + 1}行") for k in range(n)], j.cn_srt)
    write_srt([Cue(k + 1, k * 2.0, k * 2.0 + 2.0, f"dòng việt {k + 1}") for k in range(n)], j.vi_srt)
    return j


def _numbered(body: str) -> list[str]:
    """The VI texts of the lines the model is asked to judge (numbered '1. ZH: …')."""
    review_part = body.split("Lines to review:", 1)[1].split("CONTEXT ONLY", 1)[0]
    return [ln.split("VI: ", 1)[1] for ln in review_part.splitlines() if "VI: " in ln]


def test_each_batch_carries_its_neighbours_marked_context_only(tmp_path, monkeypatch):
    """A sentence cut at a batch edge must read whole: batch 2 sees the last lines of
    batch 1, batch 1 the first lines of batch 2 — as CONTEXT ONLY, not numbered."""
    j = _long_job(tmp_path, qa.BATCH + 5)
    fake = FakeQA('{"issues":[]}')
    use(monkeypatch, fake)
    qa.review(j, "openrouter")
    first, second = sorted((b for b, _r in fake.calls),        # batches run in parallel
                           key=lambda b: "1. ZH: 中文第1行\n" not in b)

    assert _numbered(first) == [f"dòng việt {k}" for k in range(1, qa.BATCH + 1)]
    after = first.split("CONTEXT ONLY", 1)[1]
    assert "do not judge" in after and f"dòng việt {qa.BATCH + 1}" in after
    assert f"dòng việt {qa.BATCH + 3}" not in first              # only CONTEXT lines each side

    assert _numbered(second) == [f"dòng việt {k}" for k in range(qa.BATCH + 1, qa.BATCH + 6)]
    before = second.split("Lines to review:", 1)[0]
    assert before.startswith("CONTEXT ONLY") and "do not judge" in before
    assert f"中文第{qa.BATCH}行" in before and f"dòng việt {qa.BATCH - 1}" in before
    assert "CONTEXT ONLY" not in second.split("Lines to review:", 1)[1]   # nothing after the end


def test_findings_point_at_the_judged_line_not_at_a_context_line(tmp_path, monkeypatch):
    j = _long_job(tmp_path, qa.BATCH + 5)

    class PerBatch(FakeQA):
        def rewrite(self, body, instruction, *, max_tokens=300, role=None):
            self.calls.append((body, role))
            return '{"issues":[{"i":1,"kind":"meaning","note":"n","fix":"khác"}]}'

    use(monkeypatch, PerBatch(""))
    qa.review(j, "openrouter")
    assert sorted(f["vi"] for f in qa.load(j)) == ["dòng việt 1", f"dòng việt {qa.BATCH + 1}"]


def test_context_lines_are_shortened_and_the_instruction_explains_fragments():
    long_vi = "một câu tiếng Việt rất dài " * 10
    pairs = [(0.0, 1.0, "前文", long_vi)] + [(1.0, 2.0, "但大带来的却经常是更宽的", "là viền đen rộng hơn")]
    body = qa.batch_body(pairs, 1)
    ctx_line = next(ln for ln in body.splitlines() if "ZH: 前文" in ln)
    assert len(ctx_line) < len(long_vi) and ctx_line.endswith("…")
    assert "1. ZH: 但大带来的却经常是更宽的" in body                   # judged lines stay whole
    for phrase in ("CONSECUTIVE", "adjacent line", "CONTEXT ONLY", "fragment"):
        assert phrase in qa.INSTRUCTION


def test_the_flag_follows_the_line_and_disappears_once_edited(job):
    findings = [{"start": 0.0, "end": 2.0, "vi": "Bao nhiêu lời chưa kịp nói", "kind": "meaning",
                 "note": "n", "fix": "f"}]
    cues = parse_srt(job.vi_srt)
    assert "ai" in review.cue_issues(cues, qa_findings=findings)[0]
    cues[0].text = "Bao lời hứa chưa kịp thực hiện"
    assert "ai" not in review.cue_issues(cues, qa_findings=findings)[0]
    (job.work_dir / "qa_report.json").write_text(json.dumps(findings), encoding="utf-8")
    assert qa.load(job) == findings


# --- E-6: "Dịch lại" ---------------------------------------------------------------

def test_alternatives_send_context_and_parse_three_options(monkeypatch):
    from app.pipeline import translate

    fake = FakeQA("1| Bao lời hứa chưa kịp thực hiện\n2| Còn bao lời hứa dang dở\n3| Lời hứa còn dở")
    monkeypatch.setattr(translate, "get_translator", lambda name: fake)
    opts = translate.alternatives("有好多的话还来不及兑现", "Bao nhiêu lời chưa kịp nói",
                                  before=[("你越走越远", "Bạn đi càng xa")], request="trang trọng hơn",
                                  brief="Bài hát chia tay")
    assert opts == ["Bao lời hứa chưa kịp thực hiện", "Còn bao lời hứa dang dở", "Lời hứa còn dở"]
    body, role = fake.calls[0]
    assert role == "shorten" and "DÒNG CẦN DỊCH LẠI" in body and "Bạn đi càng xa" in body
    assert "trang trọng hơn" in body and "Bài hát chia tay" in body


def test_retranslate_dialog_returns_the_picked_option():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from desktop.widgets.dialogs import RetranslateDialog

    d = RetranslateDialog(None, index=3, zh="你好", vi="xin chào")
    assert not d.use_btn.isEnabled()
    d.set_options(["chào bạn", "chào cậu nha", "chào"])
    assert d.choice() == "chào cậu nha"                 # "Tự nhiên" preselected
    d.radios[2].setChecked(True)
    assert d.choice() == "chào" and d.use_btn.isEnabled()
