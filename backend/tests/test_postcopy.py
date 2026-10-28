"""Gợi ý tiêu đề + hashtag (app/pipeline/postcopy.py): the request carries what the app
knows, the answer is parsed defensively, the dialog puts a title on top and hashtags at
the end. Fake provider — no paid call."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import Job  # noqa: E402
from app.pipeline import postcopy  # noqa: E402

REPLY = ('Sure!\n```json\n{"titles": ["Tài xế xe buýt xuyên không tận thế — Tập 6", '
         '"Tập 6: cả xe nữ sinh gặp zombie", "Hồi hộp tới phút cuối"], '
         '"hashtags": ["#vietsub", "longtieng", "#VietSub", "#hoat hinh", "#" , "#Anime"]}\n```')


@pytest.fixture
def job(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps(
        {"title": "第4集 | 《末日校车》06 我一个普通大巴司机", "platform": "douyin"}), encoding="utf-8")
    (tmp_path / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTỉnh lại đi!\n\n"
                                     "2\n00:00:01,000 --> 00:00:02,000\nCó zombie!\n",
                                     encoding="utf-8")
    return Job(id="x", work_dir=tmp_path)


def test_request_has_title_episode_and_lines(job):
    text = postcopy.request_text(job)
    assert "《末日校车》06" in text and "Tập: 6" in text and "Douyin" in text
    assert "Tỉnh lại đi!\nCó zombie!" in text


def test_parse_is_tolerant():
    got = postcopy.parse(REPLY)
    assert got["titles"][1] == "Tập 6: cả xe nữ sinh gặp zombie"
    assert got["hashtags"] == ["#vietsub", "#longtieng", "#hoathinh", "#anime"]
    assert postcopy.parse("no json here") == {"titles": [], "hashtags": []}
    assert postcopy.parse('{"titles": "oops"') == {"titles": [], "hashtags": []}


def test_compose():
    out = postcopy.compose("Video gốc: X\n", {"titles": ["T1", "T2"], "hashtags": ["#a", "#b"]})
    assert out == "T1\n\nVideo gốc: X\n\n#a #b\n"
    assert postcopy.compose("D\n", {"titles": [], "hashtags": []}) == "D\n"


class _Fake:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def available(self):
        return True

    def rewrite(self, text, instruction, *, max_tokens, role):
        self.calls.append((role, max_tokens))
        return self.reply


def test_suggest_uses_the_cheap_role(job, monkeypatch):
    from app.providers import registry

    fake = _Fake(REPLY)
    monkeypatch.setattr(registry, "get_translator", lambda name: fake)
    assert postcopy.suggest(job)["titles"][0].endswith("Tập 6")
    assert fake.calls == [("shorten", 500)]
    monkeypatch.setattr(registry, "get_translator", lambda name: _Fake("sorry"))
    with pytest.raises(RuntimeError):
        postcopy.suggest(job)


def test_dialog_applies_the_suggestion():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    from desktop.widgets.dialogs import PostDialog

    d = PostDialog(None, text="Video gốc: X\n", checklist=[], suggest=lambda: {})
    d._suggested(postcopy.parse(REPLY))
    assert d.text.toPlainText().startswith("Tài xế xe buýt xuyên không tận thế — Tập 6\n\nVideo gốc")
    assert d.text.toPlainText().rstrip().endswith("#anime")
    assert "Hồi hộp tới phút cuối" in d.alt_titles.text()
    d._suggest_failed("RuntimeError: offline")
    assert "offline" in d.status.text() and d.ai_btn.isEnabled()
    plain = PostDialog(None, text="x", checklist=[])
    assert plain.ai_btn is None
