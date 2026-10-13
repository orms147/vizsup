"""E-16: export names from the title, soft-subtitle MKV remux."""
from __future__ import annotations

import pytest

from app.models import Job
from app.pipeline import export


@pytest.mark.parametrize("title, want", [
    ("爱你没差 #翻唱 #livehouse", "爱你没差"),
    ('Tập 3: "Mẹ" / con? <final>', "Tập 3 Mẹ con final"),
    ("   ...  ", "abc123"),
    ("CON", "CON_"),
    (None, "abc123"),
])
def test_safe_name(title, want):
    assert export.safe_name(title, "abc123") == want


def test_safe_name_caps_length():
    assert len(export.safe_name("x" * 300, "f")) == 80


def test_softsub_cmd_pairs_clean_picture_with_rendered_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "ffmpeg_bin", lambda: "ffmpeg")
    job = Job(id="j", work_dir=tmp_path / "j")
    fonts = job.work_dir / "fonts"
    fonts.mkdir()
    for n in ("BeVietnamPro-Regular.ttf", "BeVietnamPro-Bold.ttf", "JetBrainsMono.ttf"):
        (fonts / n).write_bytes(b"f")
    dest = tmp_path / "out" / "Tên video.mkv"
    cmd = export.softsub_cmd(job, dest)
    joined = " ".join(cmd)
    assert cmd[cmd.index("-i") + 1] == "source.mp4"                 # picture without burned subs
    assert "-map 0:v:0 -c:v copy" in joined                         # picture copied as is
    assert "-map 1:a:0? -map 2:0" in joined                         # dub mix from output.mp4
    assert "-c:s ass" in joined and "-filter_complex" not in cmd    # remux, styles kept
    attached = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-attach"]
    assert attached == ["fonts/BeVietnamPro-Bold.ttf", "fonts/BeVietnamPro-Regular.ttf"]
    assert "-metadata:s:t:1" in cmd and cmd[-1] == str(dest)


def test_export_softsub_needs_a_finished_render(tmp_path):
    job = Job(id="j", work_dir=tmp_path / "j")
    (job.work_dir / "source.mp4").write_bytes(b"v")
    with pytest.raises(FileNotFoundError, match="output.mp4"):
        export.export_softsub(job, tmp_path / "x.mkv")


def test_softsub_repeats_the_blur_cover_of_the_render(tmp_path, monkeypatch):
    """A render that blurred the Chinese hardsub band → the .mkv picture is blurred the
    same way (re-encoded), instead of showing the original Chinese subtitles."""
    import json

    monkeypatch.setattr(export, "ffmpeg_bin", lambda: "ffmpeg")
    job = Job(id="j", work_dir=tmp_path / "j")
    (job.work_dir / "render.json").write_text(json.dumps({"cover_mode": "blur"}), encoding="utf-8")
    (job.work_dir / "hardsub_region.json").write_text(
        json.dumps({"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1}), encoding="utf-8")
    cmd = export.softsub_cmd(job, tmp_path / "o.mkv")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "crop=iw*0.8000:ih*0.1000:iw*0.1000:ih*0.8000,boxblur" in graph
    assert cmd[cmd.index("-map") + 1] == "[v]" and "libx264" in cmd and "copy" in cmd   # audio still copied


def test_full_render_records_how_it_was_made(tmp_path, monkeypatch):
    from app.pipeline import assemble

    job = Job(id="r", work_dir=tmp_path / "r")
    job.vi_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    monkeypatch.setattr(assemble, "_build_render", lambda *a, **k: ["ffmpeg"])
    monkeypatch.setattr(assemble.subprocess, "run", lambda *a, **k: None)
    assemble.assemble(job, [], cover_mode="blur", frame="tiktok", preview=True)
    assert assemble.load_render_info(job) == {}                     # a quick proof is not output.mp4
    assemble.assemble(job, [], cover_mode="blur", frame="tiktok")
    assert assemble.load_render_info(job)["cover_mode"] == "blur"
