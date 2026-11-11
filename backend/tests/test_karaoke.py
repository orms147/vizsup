"""«Chữ chạy theo lời» (pipeline/karaoke.py): word weights, the \\k tags over the speech span,
write_ass wiring (off = byte-identical), assemble's spans from the dub plan, the Short preset."""
from __future__ import annotations

import re
from pathlib import Path

from app.models import Cue
from app.pipeline import karaoke
from app.pipeline.srt import write_ass


def ks(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\\k(\d+)", text)]


def test_weights_count_spoken_syllables_and_pauses():
    assert karaoke.weight("chào") == 1
    assert karaoke.weight("bạn,") == 1.5
    assert karaoke.weight("đi!") == 1.9
    assert karaoke.weight("2026") > 3              # "hai nghìn không trăm hai mươi sáu"
    assert karaoke.weight("—") == 0.5


def test_tagged_fills_the_speech_span_exactly():
    out = karaoke.tagged("Xin chào\\Nbạn nhé", 10.0, 13.0, (10.5, 12.5),
                         colour="#FFD400", base="#FFFFFF")
    assert out.startswith("{\\1c&H00D4FF&\\2c&HFFFFFF&}{\\k50}")   # BGR; half a second of lead
    d = ks(out)
    assert d[0] == 50 and sum(d[1:]) == 200        # 2.0 s of speech over 4 words, no drift
    assert "\\N" in out and out.count("{\\k") == 5
    assert out.endswith("nhé")


def test_tagged_falls_back_to_the_cue_and_keeps_tags():
    out = karaoke.tagged("{\\i1}một hai", 1.0, 2.0, None, colour="#3DDC84", base="#FFFFFF")
    assert "{\\i1}" in out and sum(ks(out)) == 100
    tiny = karaoke.tagged("một hai", 1.0, 2.0, (1.5, 1.55), colour="#3DDC84", base="#FFFFFF")
    assert sum(ks(tiny)) == 100                    # a 50-ms span is not trusted
    assert karaoke.tagged("...", 0, 1, None, colour="#FFD400", base="#FFFFFF").count("\\k") == 1
    assert karaoke.tagged("", 0, 1, None, colour="#FFD400", base="#FFFFFF") == ""


def test_colour_for_glass_and_garbage():
    assert karaoke.colour_for({"glass": True}, "#FFD400") == karaoke.GLASS_HIGHLIGHT
    assert karaoke.colour_for({}, "nope") == karaoke.HIGHLIGHT
    assert karaoke.colour_for(None, "#ff5ca8") == "#ff5ca8"


def test_write_ass_off_is_unchanged_and_on_tags_dialogue_only(tmp_path):
    cues = [Cue(index=1, start=0.0, end=2.0, text="xin chào"),
            Cue(index=2, start=2.0, end=4.0, text="tạm biệt")]
    plain = write_ass(cues, tmp_path / "a.ass").read_text(encoding="utf-8")
    off = write_ass(cues, tmp_path / "b.ass", karaoke=None).read_text(encoding="utf-8")
    assert plain == off
    on = write_ass(cues, tmp_path / "c.ass", style={"primary": "#00FF00"},
                   comments=[Cue(index=100001, start=0, end=1, text="ghi chú")],
                   karaoke={"colour": "#FFD400", "spans": {2: (2.5, 3.5)}}).read_text(encoding="utf-8")
    lines = [ln for ln in on.splitlines() if ln.startswith("Dialogue:")]
    assert "\\2c&H00FF00&" in lines[0] and sum(ks(lines[0])) == 200
    assert ks(lines[1])[0] == 50 and sum(ks(lines[1])) == 150
    assert "\\k" not in lines[2]                    # the owner's commentary stays plain


def test_spans_follow_the_dub_plan(monkeypatch):
    from app.pipeline import assemble

    cues = [Cue(index=7, start=1.0, end=3.0, text="a"), Cue(index=8, start=3.0, end=5.0, text="b")]
    segs = [(cues[0], Path("s1.mp3")), (cues[1], Path("s2.mp3"))]
    monkeypatch.setattr(assemble, "_durations", lambda paths: [2.6, 1.0])
    got = karaoke.spans(segs)
    assert got[7][0] == 1.0 and abs(got[7][1] - 3.0) < 0.01     # sped up to fit its slot
    assert got[8][0] >= 3.0
    assert karaoke.spans([]) == {}


def test_render_options_reach_assemble_and_the_short_preset():
    from app.pipeline import render, shorts

    assert render.picture_args({"karaoke": {"on": True}})["karaoke"] == {"on": True}
    assert shorts.DEFAULT_SHORT["karaoke"]["on"] is True


def test_assemble_passes_karaoke_to_write_ass(tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import assemble

    job = Job(id="k", work_dir=tmp_path / "k")
    job.vi_srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nxin chào bạn\n", encoding="utf-8")
    seen = {}

    def fake_write(cues, path, **kw):
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr(assemble, "write_ass", fake_write)
    monkeypatch.setattr(karaoke, "spans", lambda segs: {1: (1.2, 2.4)})
    try:
        assemble.assemble(job, [], karaoke={"on": True, "color": "#3DDC84"})
    except RuntimeError:
        pass
    assert seen["karaoke"]["colour"] == "#3DDC84" and seen["karaoke"]["spans"] == {1: (1.2, 2.4)}
    seen.clear()
    try:
        assemble.assemble(job, [], karaoke={"on": False})
    except RuntimeError:
        pass
    assert seen["karaoke"] is None
