"""Background music ("Nhạc nền"): music.json, placement on the output timeline (F-4 pause
notes shift it like the subtitles), the ffmpeg graph, and how assemble mixes it into
every audio mode. Construct-only — probe/ffmpeg are monkeypatched, nothing encodes."""
from __future__ import annotations

import json

import pytest

from app.models import Cue, Job
from app.pipeline import approval, assemble, music as mu


@pytest.fixture()
def job(tmp_path, monkeypatch):
    j = Job(id="m", work_dir=tmp_path / "m")
    (j.work_dir / mu.DIR).mkdir()
    for name in ("song.mp3", "jingle.wav"):
        (j.work_dir / mu.DIR / name).write_bytes(b"audio")
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    return j


def _dur(song: float = 100.0, jingle: float = 5.0):
    return lambda p: {"song.mp3": song, "jingle.wav": jingle}.get(p.name, 1.2)


# --- music.json -----------------------------------------------------------------------

def test_clips_round_trip_cleaned_and_sorted(job):
    mu.save(job, [mu.Clip(file="music/jingle.wav", start=30, end=40, volume=0.5, loop=False),
                  mu.Clip(file="music/song.mp3", name="Bài hát.mp3")])
    raw = json.loads((job.work_dir / mu.FILE).read_text(encoding="utf-8"))
    assert set(raw["clips"][0]) == {"file", "start", "end", "volume", "fade_in", "fade_out",
                                    "loop", "duck", "name", "id"}
    clips = mu.load(job)
    assert [(c.file, c.start, c.end) for c in clips] == [("music/song.mp3", 0.0, None),
                                                          ("music/jingle.wav", 30.0, 40.0)]
    assert clips[0].name == "Bài hát.mp3" and clips[0].volume == mu.VOLUME_DEFAULT
    assert clips[1].loop is False and clips[1].duck is True
    mu.save(job, [])
    assert not (job.work_dir / mu.FILE).exists() and mu.load(job) == []


def test_bad_rows_are_dropped_and_values_clamped(job):
    (job.work_dir / mu.FILE).write_text(json.dumps({"clips": [
        "junk", {"start": 3}, {"file": "music/song.mp3", "start": -4, "end": "x",
                               "volume": 9, "fade_in": -1, "fade_out": 1e9},
        {"file": "music/jingle.wav", "start": 10, "end": 5}]}), encoding="utf-8")
    a, b = mu.load(job)
    assert (a.start, a.volume, a.fade_in, a.fade_out) == (0.0, mu.VOLUME_MAX, 0.0, mu.FADE_MAX)
    assert a.end is None                      # unreadable end → to the end of the video
    assert b.end is not None and b.end > b.start          # never an empty/negative span


def test_validate_reports_missing_files_and_silent_clips(job):
    probs = mu.validate(job, [mu.Clip(file="music/song.mp3"),
                              mu.Clip(file="music/gone.mp3", name="gone.mp3"),
                              mu.Clip(file="music/jingle.wav", volume=0.0)])
    assert len(probs) == 2 and "gone.mp3" in probs[0] and "âm lượng 0" in probs[1]


def test_import_copies_into_the_project_once(job, tmp_path):
    src = tmp_path / "My Song.MP3"
    src.write_bytes(b"\x00music" * 100)
    rel = mu.import_file(job, src)
    assert rel.startswith("music/bgm_") and rel.endswith(".mp3")
    assert (job.work_dir / rel).read_bytes() == src.read_bytes()
    assert mu.import_file(job, src) == rel                 # same content → same copy


def test_music_does_not_need_re_approval(job):
    """A render-time mix setting: adding a song must not re-lock the subtitles."""
    rev = approval.revision(job)
    mu.save(job, [mu.Clip(file="music/song.mp3")])
    assert approval.revision(job) == rev


def test_summary():
    assert mu.summary([]) == ""
    assert mu.summary([mu.Clip(file="a")]) == "Nhạc nền: cả video"
    assert mu.summary([mu.Clip(file="a", end=9), mu.Clip(file="b", start=20)]) == \
        "Nhạc nền: 2 đoạn"


# --- placement on the output timeline -----------------------------------------------------

def test_place_whole_video_loops_a_short_song(job):
    (p,) = mu.place([mu.Clip(file="music/jingle.wav")], [], 60.0, duration=_dur(), job=job)
    assert (p["start"], p["play"], p["loop"]) == (0.0, 60.0, True)
    (p,) = mu.place([mu.Clip(file="music/jingle.wav", loop=False)], [], 60.0, duration=_dur(),
                    job=job)
    assert (p["play"], p["loop"]) == (5.0, False)          # plays once, then silence
    assert p["fade_out"] <= 2.5                            # fades fit inside a short play


def test_place_follows_the_picture_through_pause_notes(job):
    """A 4 s pause at 10 s (F-4): music placed at 20–30 s on the VIDEO plays at 24–34 s
    of the output; a clip spanning the pause keeps playing through the freeze."""
    ins = [(10.0, 4.0)]
    clips = [mu.Clip(file="music/song.mp3", start=20, end=30),
             mu.Clip(file="music/song.mp3", start=5, end=15),
             mu.Clip(file="music/song.mp3", start=10, end=12),     # starts AT the pause
             mu.Clip(file="music/song.mp3")]                       # whole video
    a, b, c, d = mu.place(clips, ins, 64.0, duration=_dur(), job=job)
    assert (a["start"], a["play"]) == (24.0, 10.0)
    assert (b["start"], b["play"]) == (5.0, 14.0)          # 10 s of video + the 4 s freeze
    assert (c["start"], c["play"]) == (10.0, 6.0)          # plays under the freeze
    assert (d["start"], d["play"]) == (0.0, 64.0)          # the whole OUTPUT
    (intro,) = mu.place([mu.Clip(file="music/song.mp3")], [(0.0, 3.0)], 63.0,
                        duration=_dur(), job=job)
    assert intro["start"] == 0.0                           # an intro note gets music too
    # the subtitles use the same shift
    from app.pipeline import commentary as cm
    assert cm.to_out(20.0, ins) == a["start"]


def test_place_skips_missing_silent_and_out_of_range_clips(job):
    clips = [mu.Clip(file="music/nope.mp3"), mu.Clip(file="music/song.mp3", volume=0),
             mu.Clip(file="music/song.mp3", start=70)]
    assert mu.place(clips, [], 60.0, duration=_dur(), job=job) == []
    (p,) = mu.place([mu.Clip(file="music/song.mp3", start=50, end=90)], [], 60.0,
                    duration=_dur(), job=job)
    assert p["play"] == 10.0                               # cut at the end of the video


def test_place_without_a_known_length_never_loops_forever(job):
    (p,) = mu.place([mu.Clip(file="music/jingle.wav")], [], 0.0, duration=_dur(), job=job)
    assert p["loop"] is False and p["play"] == 5.0


# --- the ffmpeg graph -----------------------------------------------------------------

def test_clip_filter_cuts_fades_and_delays():
    f = mu.clip_filter({"file": "x", "start": 12.5, "play": 20.0, "loop": True, "volume": 0.3,
                        "fade_in": 1.0, "fade_out": 2.0, "duck": True})
    assert f == ("aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:"
                 "channel_layouts=stereo,atrim=end=20.000,asetpts=PTS-STARTPTS,volume=0.300,"
                 "afade=t=in:st=0:d=1.000,afade=t=out:st=18.000:d=2.000,adelay=12500:all=1")
    f = mu.clip_filter({"file": "x", "start": 0.0, "play": 5.0, "loop": False, "volume": 1.0,
                        "fade_in": 0.0, "fade_out": 0.0, "duck": False})
    assert "volume" not in f and "afade" not in f and "adelay" not in f


def test_input_args_loop_at_the_demuxer():
    args = mu.input_args([{"file": "music/a.mp3", "loop": True},
                          {"file": "music/b.wav", "loop": False}])
    assert args == ["-stream_loop", "-1", "-i", "music/a.mp3", "-i", "music/b.wav"]


def test_graph_ducks_only_the_clips_that_ask(job):
    placed = [{"file": "a", "start": 0, "play": 9, "loop": False, "volume": 0.3,
               "fade_in": 0, "fade_out": 0, "duck": True},
              {"file": "b", "start": 3, "play": 2, "loop": False, "volume": 0.3,
               "fade_in": 0, "fade_out": 0, "duck": False}]
    parts, out = mu.graph(placed, 2, key="[k]")
    g = ";".join(parts)
    assert "[2:a]" in g and "[3:a]" in g and out == "[music]"
    assert f"[musdk][k]sidechaincompress={mu.DUCK}[musd]" in g
    assert "[mus0]anull[musdk]" in g and "[mus1]anull[musp]" in g
    parts, out = mu.graph(placed, 2, key=None)              # no Vietnamese voice: no ducking
    assert "sidechaincompress" not in ";".join(parts) and out == "[musp]"


# --- assemble: every audio mode keeps the music -----------------------------------------

@pytest.fixture()
def segs(job, monkeypatch):
    monkeypatch.setattr(assemble, "probe_duration", _dur())
    out = []
    for i in range(1, 4):
        seg = job.tts_dir / f"seg_{i:04d}.mp3"
        out.append((Cue(index=i, start=4.0 * i, end=4.0 * i + 2, text=f"c{i}"), seg))
    return out


def _render(job, segs, mode, clips, *, inserts=None, duration=60.0, **kw):
    placed = mu.place(clips, inserts or [], duration + sum(d for _a, d in inserts or []),
                      duration=assemble.probe_duration, job=job)
    cmd = assemble._build_render(job, segs, audio_mode=mode, orig_volume=1.0, dub_volume=1.0,
                                 duration=duration, inserts=inserts, music=placed, **kw)
    return cmd, (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["replace", "mix", "duck"])
def test_music_mixed_into_each_dub_mode_and_ducked_under_the_voice(job, segs, mode):
    clips = [mu.Clip(file="music/song.mp3", start=0, end=None, volume=0.25)]
    cmd, g = _render(job, segs, mode, clips)
    assert cmd[cmd.index("music/song.mp3") - 1] == "-i" and cmd.count("-i") == 3
    assert "[2:a]aresample=48000" in g                     # source, dub, then the song
    assert "asplit=2[dubm][mkey]" in g and "[mkey]apad[mkeyp]" in g
    assert "[musdk][mkeyp]sidechaincompress" in g
    assert "[music]" not in g                              # one group only
    assert f"amix=inputs=2:normalize=0:duration=first:dropout_transition=0,{mu.LIMIT}[amus]" in g
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "[amus]"
    # the mode's own chain still sees the dub
    assert "[dubm]" in g
    if mode == "duck":
        assert "[dubm]asplit=2[scin][dubv]" in g


def test_music_with_keep_music_comes_after_the_instrumental(job, segs):
    (job.work_dir / "bg.wav").write_bytes(b"stem")
    cmd, g = _render(job, segs, "keep_music", [mu.Clip(file="music/song.mp3")])
    assert cmd.index("bg.wav") < cmd.index("music/song.mp3")
    assert "[2:a]aresample=48000,volume=1.000[bg]" in g and "[3:a]aresample=48000,aformat" in g


def test_music_without_a_dub_is_not_ducked(job, segs):
    """"Giữ giọng gốc": no Vietnamese voice — the music goes under the original as is."""
    cmd, g = _render(job, [], "replace", [mu.Clip(file="music/song.mp3")])
    assert cmd.count("-i") == 2 and "[1:a]aresample=48000" in g
    assert "sidechaincompress" not in g and "[0:a][musp]amix" in g


def test_no_music_leaves_the_graph_alone(job, segs):
    _cmd, g = _render(job, segs, "replace", [])
    assert "amus" not in g and "alimiter" not in g and "[dubn]apad[aout]" in g


def test_music_after_a_pause_note_stays_on_its_picture(job, segs):
    clips = [mu.Clip(file="music/jingle.wav", start=20, end=30, loop=True)]
    cmd, g = _render(job, segs, "duck", clips, inserts=[(10.0, 4.0)])
    assert "adelay=24000:all=1" in g and "atrim=end=10.000" in g
    assert cmd[cmd.index("music/jingle.wav") - 3:cmd.index("music/jingle.wav")] == \
        ["-stream_loop", "-1", "-i"]
    assert cmd[cmd.index("-t") + 1] == "64.000"


def test_assemble_reads_music_json_on_every_render(job, segs, monkeypatch):
    job.vi_srt.write_text("1\n00:00:04,000 --> 00:00:06,000\nxin chào\n", encoding="utf-8")
    job.source_video.write_bytes(b"v")
    monkeypatch.setattr(assemble, "probe_duration",
                        lambda p: 60.0 if p.name == "source.mp4" else _dur()(p))
    monkeypatch.setattr(assemble, "_render_dub", lambda *a, **k: "dub/list.txt")
    seen = {}
    monkeypatch.setattr(assemble, "_run_ffmpeg", lambda cmd, cwd, **k: seen.update(cmd=cmd))
    assemble.assemble(job, segs, audio_mode="duck")
    assert "music/song.mp3" not in seen["cmd"]
    mu.save(job, [mu.Clip(file="music/song.mp3")])
    logs = []
    assemble.assemble(job, segs, audio_mode="duck", log=logs.append)
    assert "music/song.mp3" in seen["cmd"] and any("Nhạc nền: 1" in m for m in logs)
    assert assemble.load_render_info(job)["music"] == 1
