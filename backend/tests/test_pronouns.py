"""Soát xưng hô: the model says who speaks each line and which self-pronouns the cast allows;
only an unambiguous first-person word (tôi / tao / tớ) outside that set is swapped
(pipeline/pronouns.py)."""
from __future__ import annotations

import json

from app.models import Job
from app.pipeline import pronouns
from app.pipeline.srt import parse_srt


def test_allowed_selves_keeps_one_word_pronouns_in_order():
    assert pronouns.allowed_selves("tôi/tao") == ["tôi", "tao"]
    assert pronouns.allowed_selves(" Em ") == ["em"]
    assert pronouns.allowed_selves("bà đây / tao, hoặc tớ") == ["tao", "tớ"]
    assert pronouns.allowed_selves("?") == []


def test_fix_line_swaps_only_first_person_words_the_cast_does_not_allow():
    assert pronouns.fix_line("Tôi vừa xem", ["em"]) == "Em vừa xem"
    assert pronouns.fix_line("Bọn tôi có tranh đâu", ["em"]) == "Bọn em có tranh đâu"
    assert pronouns.fix_line("Tôi đói rồi", ["tôi", "tao"]) is None          # allowed
    assert pronouns.fix_line("Cũng đến lượt bọn tao chứ", ["tớ", "tao"]) is None
    assert pronouns.fix_line("Anh Cố, anh xem đây là gì?", ["em"]) is None    # "anh" = you
    assert pronouns.fix_line("Em biết lỗi rồi", ["tôi"]) is None             # em is not touched
    assert pronouns.fix_line("Tôitôi", ["em"]) is None                       # whole words only
    assert pronouns.fix_line("Tôi về xe ngay", []) is None                   # unknown speaker
    assert pronouns.fix_line("Giờ tôi bảo ông Phùng nhà cô", ["tao"]) is None  # → tao … cô
    assert pronouns.fix_line("Cô dám đánh tôi!", ["tao"]) is None


def test_parse_reads_the_who_section():
    reply = ("CAST:\n1: not a line (cast section)\nCố → nữ sinh: tôi – cô\n"
             "**WHO:**\n1: Cố → Tiểu Phi | self: tôi/tao\n2: Tiểu Phi → Cố | self: em\n3: ?\n"
             "4: — \n5: ? → Cố | self: em\n6: nữ sinh → ? | self: em\n9: Cố → x | self: tôi\n")
    who = pronouns.parse(reply, 6)
    assert who == [("Cố → Tiểu Phi", ["tôi", "tao"]), ("Tiểu Phi → Cố", ["em"]), ("?", []),
                   ("—", []), ("? → Cố", []), ("nữ sinh → ?", [])]


def test_window_body_numbers_the_window_and_shows_earlier_lines():
    rows = [(i, i + 1, f"zh{i}", f"vi{i}") for i in range(pronouns.WINDOW + 5)]
    body = pronouns.window_body(rows, pronouns.WINDOW, "HEAD")
    assert body.startswith("HEAD") and "EARLIER LINES" in body
    assert f"1. ZH: zh{pronouns.WINDOW}" in body and "6. ZH" not in body


class _Prov:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def available(self):
        return True

    def rewrite(self, text, instruction, **kw):
        self.calls.append((text, kw.get("role")))
        return self.reply


def _job(tmp_path, pairs):
    def srt(texts):
        return "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n{t}\n\n" for i, t in enumerate(texts, 1))

    (tmp_path / "cn.srt").write_text(srt([z for z, _v in pairs]), encoding="utf-8")
    (tmp_path / "vi.srt").write_text(srt([v for _z, v in pairs]), encoding="utf-8")
    return Job(id="j", work_dir=tmp_path, url="", metadata={})


def test_check_fixes_the_self_pronoun_and_records_who(tmp_path, monkeypatch):
    job = _job(tmp_path, [("我立刻回车上", "Tôi về xe ngay"), ("帮我", "Phụ tôi"), ("走吧", "Đi thôi")])
    prov = _Prov("CAST:\nx\nWHO:\n1: Tiểu Phi → Cố | self: em\n2: Cố → Tiểu Phi | self: tôi/tao\n3: ?\n")
    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: prov)
    logs: list[str] = []
    assert pronouns.check(job, "fake", log=logs.append) == {"fixed": 1, "lines": 3}
    assert [c.text for c in parse_srt(job.vi_srt)] == ["Em về xe ngay", "Phụ tôi", "Đi thôi"]
    assert (tmp_path / "vi.mt.srt").read_text(encoding="utf-8") == job.vi_srt.read_text(encoding="utf-8")
    assert prov.calls[0][1] == "refine" and "1. ZH: 我立刻回车上" in prov.calls[0][0]
    report = json.loads((tmp_path / pronouns.REPORT).read_text(encoding="utf-8"))
    assert report["fixed"][0]["who"] == "Tiểu Phi → Cố" and report["who"][1]["self"] == ["tôi", "tao"]
    assert any("sửa 1 câu" in m for m in logs)


def test_a_failing_model_changes_nothing(tmp_path, monkeypatch):
    job = _job(tmp_path, [("我立刻回车上", "Tôi về xe ngay")])
    before = job.vi_srt.read_text(encoding="utf-8")

    class Boom(_Prov):
        def rewrite(self, *a, **k):
            raise RuntimeError("down")

    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: Boom(""))
    assert pronouns.check(job, "fake") == {"fixed": 0, "lines": 1}
    assert job.vi_srt.read_text(encoding="utf-8") == before
    monkeypatch.setattr("app.providers.registry.get_translator",
                        lambda name: (_ for _ in ()).throw(KeyError("nope")))
    logs: list[str] = []
    assert pronouns.check(job, "fake", log=logs.append) is None
    assert any("Bỏ qua bước soát xưng hô" in m for m in logs)
