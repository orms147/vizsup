"""E-7: approved lines are reused; the user's recurring corrections become style rules."""
from __future__ import annotations

from app.models import Cue, Job
from app.pipeline import memory
from app.pipeline.srt import write_srt


def test_approved_lines_are_reused_for_the_same_chinese():
    memory.remember([("我说再喝一碗我熬的茶汤。", "Em nói anh uống thêm chén trà em nấu"),
                     ("嗯", "Ừ")])                          # too short to be a reliable key
    out, n = memory.apply_tm(["我说再喝一碗我熬的茶汤，", "别的", "嗯"],
                             ["Tôi nói uống thêm bát trà", "khác", "Vâng"])
    assert n == 1 and out == ["Em nói anh uống thêm chén trà em nấu", "khác", "Vâng"]


def test_edits_are_logged_and_counted_until_a_style_update(tmp_path):
    job = Job(id="m", work_dir=tmp_path / "m")
    write_srt([Cue(1, 0, 1, "你好"), Cue(2, 1, 2, "再见")], job.cn_srt)
    write_srt([Cue(1, 0, 1, "Xin chào bạn"), Cue(2, 1, 2, "Tạm biệt")], job.vi_srt)
    memory.save_machine_translation(job)
    assert memory.record_edits(job, [("你好", "Chào cậu"), ("再见", "Tạm biệt")]) == 1
    rows = (memory._storage() / memory.EDITS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and "Chào cậu" in rows[0]          # only the changed line
    assert memory.record_edits(job, [("你好", "Chào cậu")]) == 2


def test_style_update_keeps_only_rule_lines_and_resets_the_counter(tmp_path, monkeypatch):
    import json

    (memory._storage()).mkdir(parents=True, exist_ok=True)
    rows = [{"zh": "你", "mt": "bạn", "final": "cậu"}] * 4
    (memory._storage() / memory.EDITS_FILE).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    class Fake:
        def available(self):
            return True

        def rewrite(self, body, instruction, *, max_tokens=300, role=None):
            assert role == "brief" and "MT: bạn" in body
            return "Đây là các quy tắc:\n- Gọi người nghe là \"cậu\"\n- Câu ngắn"

    monkeypatch.setattr("app.providers.registry.get_translator", lambda name: Fake())
    rules = memory.update_style("openrouter")
    assert rules == '- Gọi người nghe là "cậu"\n- Câu ngắn'
    assert memory.style_rules() == rules and memory._meta()["since"] == 0


def test_style_rules_ride_in_the_translator_prompt():
    from app.providers.translation._common import system_prompt_for

    out = system_prompt_for("BASE", {"_style": "- Gọi người nghe là cậu"})
    assert "THE USER'S OWN STYLE" in out and "Gọi người nghe là cậu" in out
