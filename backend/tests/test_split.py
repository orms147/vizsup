"""Long videos: split into parts at keyframes inside voice gaps, join them back."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.models import Cue, Job
from app.pipeline import split


def test_voice_gaps_cover_before_between_and_after():
    assert split.voice_gaps([(2.0, 5.0), (4.0, 6.0), (8.0, 9.0)], 12.0) == [
        (0.0, 2.0), (6.0, 8.0), (9.0, 12.0)]


def test_part_count_and_threshold():
    assert split.part_count(3 * 3600) == 6 and split.part_count(50 * 60) == 2
    assert not split.should_split(40 * 60) and split.should_split(46 * 60)


def test_cut_is_the_keyframe_inside_a_gap_nearest_to_the_boundary():
    duration = 3600.0                                   # 2 parts → boundary at 1800
    kfs = [1790.0, 1795.0, 1801.0, 1806.0, 1830.0]
    gaps = [(1794.6, 1795.6), (1829.5, 1830.6), (1805.9, 1806.05)]   # 1806 gap too short
    cuts = split.plan_cuts(duration, gaps, lambda lo, hi: kfs)
    assert [(c.t, c.in_gap) for c in cuts] == [(1795.0, True)]


def test_no_keyframe_in_a_gap_falls_back_to_the_nearest_one_and_says_so():
    cuts = split.plan_cuts(3600.0, [(1810.0, 1811.0)], lambda lo, hi: [1700.0, 1808.0, 1900.0])
    assert [(c.t, c.in_gap) for c in cuts] == [(1808.0, False)]


def test_no_keyframe_at_all_is_an_error():
    with pytest.raises(RuntimeError, match="keyframe"):
        split.plan_cuts(3600.0, [], lambda lo, hi: [])


def test_merge_subtitles_shifts_each_part_and_renumbers():
    a = [Cue(1, 0.5, 1.5, "một"), Cue(2, 2.0, 3.0, "hai")]
    b = [Cue(1, 0.2, 1.0, "ba")]
    out = split.merge_subtitles([(a, 0.0), (b, 1800.04)])
    assert [(c.index, c.start, c.end, c.text) for c in out] == [
        (1, 0.5, 1.5, "một"), (2, 2.0, 3.0, "hai"), (3, 1800.24, 1801.04, "ba")]


def test_readiness_needs_a_render_newer_than_the_approval(tmp_path):
    import os

    from app.pipeline import approval

    group = {"id": "g", "parts": [{"id": "p1", "index": 1}, {"id": "p2", "index": 2}]}
    for pid in ("p1", "p2"):
        j = Job(id=pid, work_dir=tmp_path / pid)
        j.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
        approval.approve(j)
    p1 = Job(id="p1", work_dir=tmp_path / "p1")
    p1.output_video.write_bytes(b"x")
    stamp = (tmp_path / "p1" / "approved.json").stat().st_mtime
    os.utime(p1.output_video, (stamp + 5, stamp + 5))
    r = split.readiness(group, tmp_path)
    assert [(x["approved"], x["rendered"]) for x in r] == [(True, True), (True, False)]
    p1.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nđã sửa\n", encoding="utf-8")
    assert split.readiness(group, tmp_path)[0]["approved"] is False      # edited after


# --- real ffmpeg ------------------------------------------------------------------------

def _ffmpeg():
    try:
        from app.ffmpegutil import ffmpeg_bin

        return ffmpeg_bin()
    except Exception:  # noqa: BLE001
        return None


needs_ffmpeg = pytest.mark.skipif(_ffmpeg() is None or shutil.which("ffprobe") is None
                                  and _ffmpeg() is None, reason="ffmpeg not available")


def _split_and_check(tmp_path, monkeypatch, *, video_first: list[str], fmt: list[str]):
    """6 minutes, a 1 s pause every 7 s, keyframes every 1.5 s, B-frames; parts of ~2 min.
    Every cut must land ON the planned keyframe (not the next one), inside a pause, with
    the parts' sound in sync with their picture as in the source."""
    from app.pipeline import vad

    monkeypatch.setattr(vad, "spans_for", lambda *a, **k: None)      # → silencedetect fallback
    job = Job(id="g", work_dir=tmp_path / "g")
    subprocess.run([_ffmpeg(), "-v", "error", "-y", *video_first,
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=360",
                    "-f", "lavfi", "-i", "anoisesrc=d=361:c=pink:r=16000:a=0.4:seed=7",
                    "-af", "volume='if(lt(mod(t,7),6),1,0)':eval=frame",
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "libx264", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
                    "-c:a", "aac", "-t", "360", *fmt, str(job.source_video)], check=True)
    storage = tmp_path / "storage"
    index, _first = split.keyframe_index(job.source_video, offset=split.start_time(job.source_video))
    pts_of = {n: pts for pts, n in index}
    asked: list[int] = []
    real_segment = split._segment
    monkeypatch.setattr(split, "_segment", lambda src, out, frames, **kw:
                        asked.extend(frames) or real_segment(src, out, frames, **kw))
    group = split.split_job(job, storage, title="Video thử", target=120.0)
    parts = group["parts"]
    assert len(parts) == 3 and json.loads((job.work_dir / "group.json").read_text())["id"] == "g"
    for p, frame in zip(parts[1:], asked, strict=True):
        assert p["in_gap"]
        assert abs(p["start"] - pts_of[frame]) < 0.01          # exactly the planned keyframe
        assert 6.0 <= p["start"] % 7 <= 7.0 or 6.0 <= (p["start"] - _first) % 7 <= 7.0
    for p in parts:                                             # sound and picture as in the source
        err = split.sync_error(job.source_video, storage / p["id"] / "source.mp4", p["start"])
        assert err is not None and abs(err) < 0.012, (p["index"], err)
    return job, storage, parts


@needs_ffmpeg
def test_split_a_real_video_at_keyframes_in_silence(tmp_path, monkeypatch):
    job, storage, parts = _split_and_check(tmp_path, monkeypatch, video_first=[], fmt=[])
    total = sum(p["duration"] for p in parts)
    assert abs(total - 360.0) < 0.15                        # no overlap at the cuts
    meta = json.loads((storage / parts[1]["id"] / "metadata.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Video thử · Phần 2/3" and meta["group"]["total"] == 3


@needs_ffmpeg
def test_split_when_the_picture_starts_after_the_sound(tmp_path, monkeypatch):
    """MPEG-TS whose picture starts 0.5 s after its sound: cut TIMES were read against the
    first frame, so every cut slipped to the next keyframe (1.5 s, maybe mid-sentence)."""
    _split_and_check(tmp_path, monkeypatch, video_first=["-itsoffset", "0.5"],
                     fmt=["-f", "mpegts"])


@needs_ffmpeg
def test_join_concatenates_outputs_and_shifts_subtitles(tmp_path, monkeypatch):
    from app.pipeline import approval

    storage = tmp_path / "storage"
    gdir = storage / "g"
    gdir.mkdir(parents=True)
    parts = []
    for k, dur in enumerate((4.0, 3.0), 1):
        j = Job(id=f"p{k}", work_dir=storage / f"p{k}")
        j.vi_srt.write_text(f"1\n00:00:01,000 --> 00:00:02,000\ncâu {k}\n", encoding="utf-8")
        approval.approve(j)
        subprocess.run([_ffmpeg(), "-v", "error", "-y",
                        "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={dur}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                        "-shortest", str(j.output_video)], check=True)
        parts.append({"id": j.id, "index": k})
    (gdir / "group.json").write_text(json.dumps({"id": "g", "parts": parts}), encoding="utf-8")
    out = split.join(gdir, storage)
    from app.pipeline.assemble import probe_duration

    assert abs(probe_duration(out) - 7.0) < 0.3
    srt = (gdir / "vi.srt").read_text(encoding="utf-8")
    assert "câu 2" in srt and "00:00:05," in srt                # part 2 shifted by ~4 s


def test_join_refuses_when_a_part_is_not_rendered(tmp_path):
    storage = tmp_path / "s"
    (storage / "g").mkdir(parents=True)
    (storage / "g" / "group.json").write_text(
        json.dumps({"id": "g", "parts": [{"id": "p1", "index": 1}, {"id": "p2", "index": 2}]}),
        encoding="utf-8")
    with pytest.raises(RuntimeError, match="1, 2"):
        split.join(storage / "g", storage)


def test_render_is_pinned_to_the_source_length(tmp_path, monkeypatch):
    """A part's output must last exactly as long as its source (join offsets), so the
    dub is padded and the encode is cut with -t."""
    from app.pipeline import assemble

    job = Job(id="r", work_dir=tmp_path / "r")
    seg = job.tts_dir / "seg_0001.mp3"
    seg.write_bytes(b"x")
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 1.0)
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    cmd = assemble._build_render(job, [(Cue(1, 0.0, 1.5, "a"), seg)], audio_mode="replace",
                                 orig_volume=1.0, dub_volume=1.0, encoder="libx264",
                                 duration=1799.968)
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "apad[aout]" in graph and cmd[cmd.index("-t") + 1] == "1799.968"
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "[aout]"


def test_cli_join_reports_parts_not_ready(tmp_path, capsys):
    from app import cli

    g = tmp_path / "g"
    g.mkdir()
    (g / "group.json").write_text(json.dumps({"id": "g", "parts": [{"id": "p1", "index": 1}]}),
                                  encoding="utf-8")
    assert cli.main(["join", "--group", str(g)]) == 2
    assert "Chưa dựng xong" in capsys.readouterr().err
    assert cli.main(["join", "--group", str(tmp_path / "none")]) == 2


@needs_ffmpeg
def test_join_survives_quotes_in_paths_and_tracks_staleness(tmp_path):
    """Review findings: a ' in the storage path broke the concat list; a join that was
    cancelled left a half output.mp4 shown as finished; a re-rendered part didn't make
    the joined video stale."""
    import os
    import time

    from app.pipeline import approval, assemble

    storage = tmp_path / "Bach's videos"
    gdir = storage / "g"
    gdir.mkdir(parents=True)
    parts = []
    for k in (1, 2):
        j = Job(id=f"p{k}", work_dir=storage / f"p{k}")
        j.vi_srt.write_text(f"1\n00:00:01,000 --> 00:00:02,000\ncâu {k}\n", encoding="utf-8")
        approval.approve(j)
        time.sleep(0.02)
        subprocess.run([_ffmpeg(), "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                        "-shortest", str(j.output_video)], check=True)
        parts.append({"id": j.id, "index": k})
    (gdir / "group.json").write_text(json.dumps({"id": "g", "parts": parts}), encoding="utf-8")
    assert not split.joined_is_current(gdir, storage)
    out = split.join(gdir, storage)
    assert out.exists() and split.joined_is_current(gdir, storage)

    good = out.read_bytes()

    def boom(*a, **k):
        (gdir / split.TMP_OUT).write_bytes(b"half")
        raise InterruptedError("Đã hủy.")

    real = assemble._run_ffmpeg
    assemble._run_ffmpeg = boom
    try:
        with pytest.raises(InterruptedError):
            split.join(gdir, storage)
    finally:
        assemble._run_ffmpeg = real
    assert out.read_bytes() == good and not (gdir / split.TMP_OUT).exists()

    p2 = storage / "p2" / "output.mp4"
    later = out.stat().st_mtime + 5
    os.utime(p2, (later, later))                       # part 2 re-rendered after the join
    assert not split.joined_is_current(gdir, storage)


def test_orphan_group_still_listed_for_deletion(tmp_path, monkeypatch):
    from app.config import settings
    from desktop import store

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    (tmp_path / "gid").mkdir()
    (tmp_path / "gid" / "group.json").write_text(
        json.dumps({"id": "gid", "title": "Video dài", "parts": [{"id": "gone", "index": 1}]}),
        encoding="utf-8")
    rows = store.list_projects()
    assert [r["kind"] for r in rows] == ["group"] and rows[0]["title"] == "Video dài"
