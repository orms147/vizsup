"""N-18 + long videos: render construction. One ffmpeg input per line broke the
Windows 32k command line past ~1,600 lines and mixed in time ∝ lines × length; the
dub track is now built in windows (≤ WINDOW_MAX lines / ≤ WINDOW_SEC), each its own
small ffmpeg, and the final encode reads them through the concat demuxer.
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


def _plan(segments):
    placements, _sped, _flagged = assemble._plan_segments(segments)
    return placements


def test_final_encode_has_one_dub_input_however_many_lines(big_job):
    job, segments = big_job
    cmd = _build(job, segments)
    assert cmd.count("-i") == 2                     # source + the dub windows' concat list
    assert cmd[cmd.index("concat") + 4] == "dub/list.txt"
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[1:a]loudnorm" in script and "adelay" not in script and "amix" not in script


def test_windows_cut_in_silence_and_stay_small(big_job):
    _job, segments = big_job
    placements = _plan(segments)
    wins = assemble._windows(placements)
    assert sum(len(w["items"]) for w in wins) == len(placements) == 300
    for a, b in zip(wins, wins[1:]):
        assert a["hi"] == b["lo"] == b["items"][0]["start"]      # chained, no gap or overlap
        last = a["items"][-1]
        assert last["start"] + last["eff"] <= a["hi"] + 1e-9      # no line straddles a cut
    assert all(len(w["items"]) <= assemble.WINDOW_MAX for w in wins)
    assert all(w["hi"] - w["lo"] < assemble.WINDOW_SEC + 5 for w in wins)


def _window_samples(graph: str) -> int:
    lead = re.search(r"atrim=end_sample=(\d+),aformat[^\[]*\[lead\]", graph)
    pads = [int(n) for n in re.findall(r"apad=whole_len=(\d+)", graph)]
    return (int(lead.group(1)) if lead else 0) + sum(pads)


def test_window_graph_is_sample_exact_on_the_absolute_timeline(big_job):
    """Each window lasts exactly _sample(hi) - _sample(lo) samples, so chained windows
    put every line at round(start * 48000) — no drift after hours."""
    _job, segments = big_job
    wins = assemble._windows(_plan(segments))
    for w in wins:
        g = assemble._window_graph(w)
        assert _window_samples(g) == assemble._sample(w["hi"]) - assemble._sample(w["lo"])
        for k in range(len(w["items"])):
            assert len(re.findall(rf"\[{k}:a\]", g)) == 1       # each input used once
            assert len(re.findall(rf"\[s{k}\]", g)) == 2        # produced + consumed
        assert f"concat=n={len(w['items']) + ('[lead]' in g)}:v=0:a=1[out]" in g


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
    placements = _plan(segments[:10])
    assert [p["inp"] for p in placements] == [1, 2, 3, 4, 5, 7, 8, 9, 10]   # 6 dropped only


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
    assert "[2:a]aresample=48000,volume=0.800[bg]" in script  # source, dub, then bg
    assert "amix=inputs=2" in script and "[outa]" in script


def _duck_bed(vol: float) -> str:
    return f"[0:a]volume={vol * 10 ** (assemble.DUCK_BED_DB / 20):.3f}[bg]"


def test_keep_music_without_bg_falls_back_to_duck(big_job):
    """No separated instrumental: keep the original music + effects (ducked under the
    dub) — never silently drop them, never mix the Chinese voice at full level."""
    job, segments = big_job                                  # no bg.wav written
    logs: list[str] = []
    cmd = _build(job, segments[:3], audio_mode="keep_music", orig_volume=0.8, log=logs.append)
    assert "bg.wav" not in cmd                               # no phantom input
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert _duck_bed(0.8) in script and "sidechaincompress" in script
    assert any("nhạc, hiệu ứng gốc vẫn còn" in m for m in logs)


def test_keep_music_stale_bg_falls_back_to_duck(big_job):
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
    assert _duck_bed(0.8) in script                          # fell back to duck


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


# --- running the windows, the encoder, progress and cancel ---------------------------

class _Proc:
    """Popen stand-in: yields ffmpeg -progress lines, records terminate()."""

    def __init__(self, lines, rc=0):
        self.stdout = iter(lines)
        self.rc, self.terminated = rc, False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def poll(self):
        return self.rc if self.terminated or self.rc is not None else None

    def wait(self):
        return self.rc


def test_run_ffmpeg_reports_progress_and_cancel_kills(tmp_path, monkeypatch):
    seen = []
    lines = ["frame=1\n", "out_time_us=N/A\n", "out_time_us=30000000\n",
             "out_time_us=60000000\n", "out_time_us=45000000\n",   # never goes back
             "progress=end\n"]
    monkeypatch.setattr(assemble.subprocess, "Popen", lambda *a, **k: _Proc(lines))
    assemble._run_ffmpeg(["ffmpeg", "-i", "x"], tmp_path, total=120.0, progress=seen.append)
    assert seen == [0.25, 0.5]

    proc = _Proc(lines, rc=None)
    monkeypatch.setattr(assemble.subprocess, "Popen", lambda *a, **k: proc)
    with pytest.raises(InterruptedError):
        assemble._run_ffmpeg(["ffmpeg"], tmp_path, total=120.0, cancel=lambda: True)
    assert proc.terminated


def test_dub_windows_are_cached_and_stale_ones_removed(big_job, monkeypatch):
    job, segments = big_job
    for _c, seg in segments:
        seg.parent.mkdir(parents=True, exist_ok=True)
        seg.write_bytes(b"mp3")
    runs = []

    def fake_run(cmd, **kw):
        if str(cmd[-1]).endswith(".flac"):                      # a window render
            runs.append(cmd)
            (job.work_dir / cmd[-1]).write_bytes(b"flac")
        return assemble.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(assemble.subprocess, "run", fake_run)
    placements = _plan(segments)
    lst = assemble._render_dub(job, segments, placements)
    n = len(assemble._windows(placements))
    assert lst == "dub/list.txt" and len(runs) == n
    listed = (job.work_dir / "dub" / "list.txt").read_text(encoding="utf-8").splitlines()
    assert listed == [f"file 'win_{i:03d}.flac'" for i in range(n)]
    assert max(len([a for a in c if a == "-i"]) for c in runs) <= assemble.WINDOW_MAX

    assemble._render_dub(job, segments, placements)              # nothing changed
    assert len(runs) == n
    segments[150][1].write_bytes(b"re-voiced line")              # one line re-dubbed
    assemble._render_dub(job, segments, _plan(segments))
    assert len(runs) == n + 1                                    # only its window

    assemble._render_dub(job, segments[:50], _plan(segments[:50]))
    left = sorted(f.name for f in (job.work_dir / "dub").glob("win_*.flac"))
    assert len(left) == len(assemble._windows(_plan(segments[:50])))


def test_video_args_for_cpu_and_nvenc():
    assert assemble._video_args("libx264", False)[:6] == ["-c:v", "libx264", "-crf", "20",
                                                          "-preset", "veryfast"]
    gpu = assemble._video_args("h264_nvenc", True)
    assert gpu[:2] == ["-c:v", "h264_nvenc"] and "p1" in gpu and "yuv420p" in gpu


def test_nvenc_failure_falls_back_to_cpu(big_job, monkeypatch):
    job, segments = big_job
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    monkeypatch.setattr(assemble, "_NVENC", None)              # restored after the test
    monkeypatch.setattr(assemble, "_video_encoder", lambda: "h264_nvenc")
    monkeypatch.setattr(assemble, "_render_dub", lambda *a, **k: "dub/list.txt")
    tried = []

    def fake_run(cmd, cwd, **kw):
        enc = cmd[cmd.index("-c:v") + 1]
        tried.append(enc)
        if enc == "h264_nvenc":
            raise RuntimeError("ffmpeg: No NVENC capable devices found")

    monkeypatch.setattr(assemble, "_run_ffmpeg", fake_run)
    logs = []
    assemble.assemble(job, segments[:3], log=logs.append)
    assert tried == ["h264_nvenc", "libx264"] and any("NVENC" in m for m in logs)


def test_duck_keeps_the_original_after_the_last_dub_line(big_job):
    """sidechaincompress stops with its key: the key (the dub) is padded with silence,
    or the original audio was cut after the last Vietnamese line."""
    job, segments = big_job
    _build(job, segments[:3], audio_mode="duck")
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert "[scin]apad[scpad]" in script and "[bg][scpad]sidechaincompress" in script


def test_duck_keeps_a_lower_bed_and_ducks_further_under_the_dub(big_job):
    """Between lines the original (music, effects, the Chinese voice) stays a constant
    few dB lower — it used to jump back to full level, louder than the dub — and the
    sidechain pushes it further down while the dub speaks."""
    job, segments = big_job
    assert -12.0 <= assemble.DUCK_BED_DB <= -4.0
    _build(job, segments[:3], audio_mode="duck", orig_volume=1.0)
    script = (job.work_dir / "filter_complex.txt").read_text(encoding="utf-8")
    assert _duck_bed(1.0) in script and "volume=1.000[bg]" not in script
    assert f"[bg][scpad]sidechaincompress={assemble.DUCK_SC}[duck]" in script
    assert "[duck][dubv]amix=inputs=2:normalize=0[outa]" in script
