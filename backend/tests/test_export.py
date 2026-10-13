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
    assert "-map 0:v:0 -map 1:a:0? -map 2:0" in joined              # dub mix from output.mp4
    assert "-c:v copy" in joined and "-c:s ass" in joined           # remux, styles kept
    attached = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-attach"]
    assert attached == ["fonts/BeVietnamPro-Bold.ttf", "fonts/BeVietnamPro-Regular.ttf"]
    assert "-metadata:s:t:1" in cmd and cmd[-1] == str(dest)


def test_export_softsub_needs_a_finished_render(tmp_path):
    job = Job(id="j", work_dir=tmp_path / "j")
    (job.work_dir / "source.mp4").write_bytes(b"v")
    with pytest.raises(FileNotFoundError, match="output.mp4"):
        export.export_softsub(job, tmp_path / "x.mkv")
