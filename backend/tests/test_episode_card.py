"""Series episodes (2026-10-29): "Tập 9" chip for the first seconds, "Còn tiếp · Tập 10"
end card for the last ones — templates resolved from each video's title, so one setting
fits a whole batch; set on the render page and burned by the render as Tag events."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import Cue  # noqa: E402
from app.pipeline import batch, disclosure  # noqa: E402
from app.pipeline.srt import write_ass  # noqa: E402

ON = {**disclosure.CARD_DEFAULT, "on": True}


def test_texts_from_the_title():
    assert disclosure.card_texts(ON, "第4集 | 《末日校车》06 我一个") == ("Tập 6", "Còn tiếp · Tập 7")
    assert disclosure.card_texts(ON, "第3集 | 《末日校车》1-5 我一个") == ("Tập 1–5",
                                                                     "Còn tiếp · Tập 6")
    assert disclosure.card_texts(ON, "a vlog without numbers") == ("", "")
    # user text can't smuggle ASS overrides in
    assert disclosure.card_texts({"chip": "{\\b1}Tập {n}", "end": ""}, "Tập 5")[0] == "\\b1Tập 5"


def test_events_chip_at_the_start_end_card_at_the_end():
    assert disclosure.episode_events({**ON, "on": False}, "Tập 5", 100) == []
    assert disclosure.episode_events(ON, "no number", 100) == []
    chip, end = disclosure.episode_events(ON, "Tập 5", 100.0)
    assert (chip["start"], chip["end"], chip["align"]) == (0.0, disclosure.TAG_SECONDS, 8)
    assert chip["text"].endswith("Tập 5") and chip["margin_v"] == disclosure.CHIP_MARGIN_V
    assert (end["start"], end["end"], end["align"]) == (96.0, 100.0, 5)
    assert end["text"].endswith("Còn tiếp · Tập 6")
    (only,) = disclosure.episode_events(ON, "Tập 5", 6.0)     # too short for an end card
    assert only["align"] == 8
    # an empty end text keeps just the chip
    assert len(disclosure.episode_events({**ON, "end": ""}, "Tập 5", 100)) == 1


def test_ass_lines(tmp_path):
    out = tmp_path / "s.ass"
    write_ass([Cue(1, 0.0, 1.0, "xin chào")], out,
              extra=disclosure.episode_events(ON, "Tập 5", 60.0))
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ",Tag,," in ln]
    assert lines[0] == ("Dialogue: 1,0:00:00.00,0:00:05.00,Tag,,0,0,165,,"
                        "{\\an8}{\\fscx140\\fscy140}Tập 5")
    assert lines[1].startswith("Dialogue: 1,0:00:56.00,0:01:00.00,Tag,,0,0,0,,{\\an5}")


def test_batch_describe_mentions_it():
    assert "thẻ tập" in batch.describe({"audio_mode": "replace", "episode_card": ON})
    assert "thẻ tập" not in batch.describe({"audio_mode": "replace", "episode_card": None})


def test_assemble_burns_it_from_the_project_title(tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import assemble

    seen = {}

    def fake_write_ass(cues, path, **kw):
        seen["extra"] = kw.get("extra")
        raise RuntimeError("stop after the subtitles")

    (tmp_path / "metadata.json").write_text(json.dumps({"title": "《末日校车》08 我一个"}),
                                            encoding="utf-8")
    (tmp_path / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n",
                                     encoding="utf-8")
    monkeypatch.setattr(assemble, "write_ass", fake_write_ass)
    with pytest.raises(RuntimeError):
        assemble.assemble(Job(id="x", work_dir=tmp_path), [], episode_card=ON)
    assert [e["text"].split("}")[-1] for e in seen["extra"]] == ["Tập 8"]  # no duration → chip only


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_render_page_remembers_it_and_sends_it(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from desktop import store
    from desktop.views.render_view import RenderView

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = RenderView()
    assert v.collect_opts()["episode_card"] is None           # off by default
    v.card_cb.setChecked(True)
    v.card_end.setText("Hẹn gặp ở tập {next}")
    sent = v.collect_opts()["episode_card"]
    assert sent == {"on": True, "chip": "Tập {n}", "end": "Hẹn gặp ở tập {next}"}
    assert store.load_ui_state()["episode_card"] == sent
    assert "không có số tập" in v.card_hint.text()            # no project open
    v.shutdown()
    again = RenderView()
    assert again.episode_card() == sent
    again.shutdown()
