"""N-18: long-video render construction. 300 cues used to blow the Windows 32k
CreateProcess argv ceiling via one giant -filter_complex argument; the graph now
lives in filter_complex.txt (-filter_complex_script) and the dub mix is chunked.
Construct-only — probe/ffmpeg are monkeypatched, nothing encodes.
"""
from __future__ import annotations

import re

import pytest

from app.models import Cue, Job
from app.pipeline import assemble


@pytest.fixture()
def big_job(tmp_path, monkeypatch):
    job = Job(id="t", work_dir=tmp_path / "job")
    segments = []
    for i in range(1, 301):
        seg = job.tts_dir / f"seg_{i:04d}.mp3"
        segments.append((Cue(index=i, start=2.0 * i, end=2.0 * i + 1.5, text=f"c{i}"), seg))
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 1.2)
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    return job, segments


def _build(job, segments, **kw):
    return assemble._build_render(job, segments,
                                  audio_mode=kw.pop("audio_mode", "replace"),
                                  orig_volume=kw.pop("orig_volume", 1.0),
                                  dub_volume=kw.pop("dub_volume", 1.0), **kw)


def test_300_segments_argv_small_and_script_written(big_job):
    job, segments = big_job
    cmd = _build(job, segments)
    filter_args = sum(len(a) for a in cmd)          # rough argv length in chars
    assert "-filter_complex" not in cmd             # graph no longer inline
    assert "-filter_complex_script" in cmd
    assert filter_args < 8_000                      # was >32k inline before
    assert (job.work_dir / "filter_complex.txt").exists()


def test_every_input_label_used_exactly_once(big_job):
    job, segments = big_job
    _build(job, segments)
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    for i in range(1, 301):
        # each ffmpeg input feeds exactly one per-segment chain [i:a]...[ai]
        assert len(re.findall(rf"\[{i}:a\]", script)) == 1, i
        produced = len(re.findall(rf"\[a{i}\]", script))
        assert produced == 2, i                     # produced once + consumed once


def test_chunked_amix_no_giant_input_count(big_job):
    job, segments = big_job
    _build(job, segments)
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    for m in re.finditer(r"amix=inputs=(\d+)", script):
        assert int(m.group(1)) <= 32                # no 300-input amix anywhere


def test_mix_without_audio_stream_falls_back_to_replace(big_job, monkeypatch):
    job, segments = big_job
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: False)
    logs: list[str] = []
    _build(job, segments[:3], audio_mode="mix", log=logs.append)
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[0:a]" not in script                    # nothing references the missing track
    assert any("không có track âm thanh" in m for m in logs)


def test_corrupt_segment_skipped_not_fatal(big_job, monkeypatch):
    job, segments = big_job
    good = dict.fromkeys((str(s) for _, s in segments), 1.2)
    good[str(segments[5][1])] = 0.0                 # probe failure → 0.0 (corrupt mp3)
    monkeypatch.setattr(assemble, "probe_duration", lambda p: good[str(p)])
    _build(job, segments[:10])
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[6:a]" not in script                    # corrupt input dropped from the graph
    assert "[7:a]" in script                        # neighbours unaffected


def test_cover_blur_uses_detected_region_when_present(big_job):
    job, segments = big_job
    import json
    (job.work_dir / "hardsub_region.json").write_text(
        json.dumps({"x": 0.1, "y": 0.72, "w": 0.8, "h": 0.2}), encoding="utf-8")
    assemble._build_render(job, segments[:3], audio_mode="replace",
                           orig_volume=1.0, dub_volume=1.0, cover_mode="blur")
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "crop=iw*0.8000:ih*0.2000:iw*0.1000:ih*0.7200" in script   # detected band, not bottom-third
    assert "overlay=main_w*0.1000:main_h*0.7200" in script
    assert "overlay=0:main_h*2/3" not in script


def test_cover_blur_graph(big_job):
    job, segments = big_job
    assemble._build_render(job, segments[:3], audio_mode="replace",
                           orig_volume=1.0, dub_volume=1.0, cover_mode="blur")
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "boxblur" in script and "overlay=0:main_h*2/3" in script
    assert "[vbg]ass=subs.ass:fontsdir=fonts[v]" in script   # subs burned on top of the blur


def test_keep_music_adds_bg_input_and_mix(big_job):
    job, segments = big_job
    (job.work_dir / "bg.wav").write_bytes(b"instrumental")   # Demucs stem present
    cmd = _build(job, segments[:3], audio_mode="keep_music", orig_volume=0.8)
    assert cmd.count("bg.wav") == 1                          # extra input added
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[4:a]aresample=48000,volume=0.800[bg]" in script  # 3 segs → bg is input 4
    assert "amix=inputs=2" in script and "[outa]" in script


def test_keep_music_without_bg_falls_back_to_mix(big_job):
    job, segments = big_job                                  # no bg.wav written
    cmd = _build(job, segments[:3], audio_mode="keep_music", orig_volume=0.8)
    assert "bg.wav" not in cmd                               # no phantom input
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[0:a]volume=0.800[bg]" in script                # mix path uses the original audio


def test_keep_music_stale_bg_falls_back_to_mix(big_job):
    import os
    job, segments = big_job
    job.source_video.write_bytes(b"re-downloaded video")
    bg = job.work_dir / "bg.wav"
    bg.write_bytes(b"old instrumental")
    src_mtime = job.source_video.stat().st_mtime
    os.utime(bg, (src_mtime - 30, src_mtime - 30))          # bg OLDER than the source
    cmd = _build(job, segments[:3], audio_mode="keep_music", orig_volume=0.8)
    assert "bg.wav" not in cmd                               # stale stem not used
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[0:a]volume=0.800[bg]" in script                # fell back to mix


def test_cover_off_keeps_plain_ass_chain(big_job):
    job, segments = big_job
    assemble._build_render(job, segments[:3], audio_mode="replace",
                           orig_volume=1.0, dub_volume=1.0)
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "boxblur" not in script
    assert "[0:v]ass=subs.ass:fontsdir=fonts[v]" in script   # bundled Be Vietnam Pro


def test_probe_duration_survives_ffprobe_error(monkeypatch, tmp_path):
    import subprocess

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "ffprobe")

    monkeypatch.setattr(assemble.subprocess, "run", boom)
    assert assemble.probe_duration(tmp_path / "broken.mp3") == 0.0


def test_preview_is_the_same_graph_scaled_down_and_fast(big_job):
    """E-15: "Xem nhanh" renders the real audio mix + burned subs at 640p, ultrafast,
    into preview.mp4 — output.mp4 is never touched."""
    job, segments = big_job
    final = _build(job, segments[:3])
    graph_final = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    quick = _build(job, segments[:3], preview=True)
    graph_quick = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert quick[-1] == "preview.mp4" and final[-1] == "output.mp4"
    assert "ultrafast" in quick and "veryfast" in final
    # same mix + subs; its own .ass so subs.ass keeps matching output.mp4 (soft-sub export)
    assert "preview.ass" in graph_quick and "subs.ass" not in graph_quick
    assert graph_quick.replace("preview.ass", "subs.ass").startswith(graph_final.rstrip("\n"))
    assert "[v]scale=-2:640[vp]" in graph_quick and "[vp]" in quick


def test_tiktok_frame_fits_before_the_burn_and_pads_after(big_job):
    """E-16: 1080x1920 — scale to fit, burn the subs on the picture, then the bars."""
    job, segments = big_job
    cmd = _build(job, segments[:3], frame="tiktok", cover_mode="blur")
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    fit = graph.index("[vbg]scale=1080:1920:force_original_aspect_ratio=decrease")
    burn = graph.index("[vfit]ass=subs.ass")
    pad = graph.index("[v]pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[vt]")
    assert fit < burn < pad and "[vt]" in cmd and cmd[-1] == "output.mp4"
    _build(job, segments[:3], frame="tiktok", preview=True)
    graph = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[0:v]scale=1080:1920" in graph and "[vt]scale=-2:640[vp]" in graph
