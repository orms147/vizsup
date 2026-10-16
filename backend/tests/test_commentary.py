"""F-4: the user's own commentary — notes in commentary.json, a voice other than the
dub's, "pause" notes freeze the picture and push the rest of the video later."""
from __future__ import annotations

import json

import pytest

from app.models import Cue, Job
from app.pipeline import approval, assemble, commentary as cm


@pytest.fixture()
def job(tmp_path, monkeypatch):
    j = Job(id="c", work_dir=tmp_path / "c")
    j.vi_srt.write_text("1\n00:00:02,000 --> 00:00:05,000\nxin chào\n\n"
                        "2\n00:00:10,000 --> 00:00:12,000\ntạm biệt\n", encoding="utf-8")
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    return j


def _voiced(job, *rows):
    out = []
    for at, mode, dur in rows:
        p = job.work_dir / f"n{at}.mp3"
        p.write_bytes(b"x")
        out.append(cm.Voiced(cm.Note(at=at, text=f"bình luận ở {at}", mode=mode), p, dur))
    return out


def test_notes_round_trip_and_count_towards_the_approved_revision(job):
    rev = approval.revision(job)
    cm.save(job, [cm.Note(at=12.0, text="Đoạn này hay", mode="over"),
                  cm.Note(at=0.0, text="Chào các bạn")])
    notes = cm.load(job)
    assert [(n.at, n.mode) for n in notes] == [(0.0, "pause"), (12.0, "over")]
    assert approval.revision(job) != rev                  # a new note re-locks rendering
    cm.save(job, [])
    assert not (job.work_dir / cm.FILE).exists() and approval.revision(job) == rev


def test_commentary_voice_differs_from_the_dub():
    voices = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    assert cm.pick_voice(voices, "vi-VN-HoaiMyNeural") == "vi-VN-NamMinhNeural"
    assert cm.pick_voice(voices, None) == "vi-VN-HoaiMyNeural"
    assert cm.pick_voice(["vi-VN-HoaiMyNeural"], "vi-VN-HoaiMyNeural") is None


def test_pause_pushes_what_comes_after_it():
    ins = [(10.0, 3.0)]
    assert cm.to_out(5.0, ins) == 5.0
    assert cm.to_out(10.0, ins) == 13.0                   # starts at the pause → after it
    assert cm.to_out(10.0, ins, end=True) == 10.0         # ends at the pause → before it
    shifted = cm.shift_cues([Cue(1, 2, 5, "a"), Cue(2, 10, 12, "b")], ins)
    assert [(c.start, c.end) for c in shifted] == [(2, 5), (13, 15)]
    assert cm.to_out(0.0, [(0.0, 4.0)]) == 4.0           # intro: everything moves


def test_note_speech_sits_inside_its_own_freeze(job):
    voiced = _voiced(job, (10.0, "pause", 2.0), (3.0, "over", 1.5))
    ins = cm.inserts(voiced)
    assert ins == [(10.0, cm.LEAD + 2.0 + cm.TAIL)]
    speech = {c.text: (c.start, c.end) for c, _p in cm.speech_cues(voiced, ins)}
    assert speech["bình luận ở 10.0"] == (10.0 + cm.LEAD, 10.0 + cm.LEAD + 2.0)
    assert speech["bình luận ở 3.0"] == (3.0, 4.5)        # over the video, not shifted


def test_pause_graph_inner_intro_and_outro():
    parts, v, (a,) = assemble._pause_graph([(10.0, 3.0)], 60.0, "[0:v]", ["[0:a]"])
    g = ";".join(parts)
    assert "[0:v]split=2[vs0][vs1]" in g and v == "[vfz]" and a == "[a0fz]"
    assert "[vs0]trim=end=10.000,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=3.000" in g
    assert "[vs1]trim=start=10.000,setpts=PTS-STARTPTS[vp1]" in g
    assert "apad=pad_dur=3.000" in g and "concat=n=2:v=0:a=1[a0fz]" in g
    parts, v, _ = assemble._pause_graph([(0.0, 2.0), (60.0, 1.5)], 60.0, "[0:v]", [])
    g = ";".join(parts)
    assert "split" not in g
    # one tpad for both ends — a second chained tpad drops its stop padding in ffmpeg
    assert "tpad=start_mode=clone:start_duration=2.000:stop_mode=clone:stop_duration=1.500" in g
    assert g.count("tpad=") == 1


def test_render_with_a_pause_note(job, monkeypatch):
    seen = {}
    monkeypatch.setattr(assemble, "_render_dub",
                        lambda j, segs, placements, **k: seen.setdefault("segs", segs) and "dub/list.txt")
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda cmd, cwd, **k: seen.setdefault("cmd", cmd))
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 60.0 if "source" in str(p) else 1.2)
    job.source_video.write_bytes(b"v")
    seg1, seg2 = job.tts_dir / "seg_0001.mp3", job.tts_dir / "seg_0002.mp3"
    for s in (seg1, seg2):
        s.write_bytes(b"x")
    voiced = _voiced(job, (8.0, "pause", 2.0))
    cues = [Cue(1, 2, 5, "xin chào"), Cue(2, 10, 12, "tạm biệt")]
    assemble.assemble(job, [(cues[0], seg1), (cues[1], seg2)], audio_mode="duck",
                      commentary=voiced)
    d = cm.LEAD + 2.0 + cm.TAIL
    starts = sorted(round(c.start, 3) for c, _p in seen["segs"])
    assert starts == [2.0, round(8.0 + cm.LEAD, 3), round(10.0 + d, 3)]   # the dub moved too
    cmd = seen["cmd"]
    assert cmd[cmd.index("-t") + 1] == f"{60.0 + d:.3f}"                 # longer output
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[vfz]ass=subs.ass" in graph and "[a0fz]volume=" in graph
    ass = (job.work_dir / "subs.ass").read_text(encoding="utf-8")
    assert "Style: Comment," in ass and ",Comment,,0,0,0,,bình luận ở 8.0" in ass
    out_srt = (job.work_dir / "output.srt").read_text(encoding="utf-8")
    assert "bình luận ở 8.0" in out_srt and "00:00:12,500" in out_srt    # 10 s + 2.5 s
    info = assemble.load_render_info(job)
    assert info["inserts"] == [[8.0, d]] and 0 < info["commentary_share"] < 0.05


def test_softsub_mkv_freezes_like_the_render(job, monkeypatch):
    from app.pipeline import export

    monkeypatch.setattr(export, "ffmpeg_bin", lambda: "ffmpeg")
    (job.work_dir / "render.json").write_text(json.dumps(
        {"inserts": [[8.0, 2.5]], "duration": 60.0, "cover_mode": "box"}), encoding="utf-8")
    cmd = export.softsub_cmd(job, job.work_dir / "o.mkv")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop_duration=2.500" in graph and "libx264" in cmd


def test_join_uses_each_parts_rendered_subtitles(job):
    from app.pipeline import split

    assert split._rendered_srt(job) == job.vi_srt
    (job.work_dir / "output.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n",
                                             encoding="utf-8")
    assert split._rendered_srt(job) == job.work_dir / "output.srt"
