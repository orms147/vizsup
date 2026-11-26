"""Res "1440" (the one size allowed above the source, for YouTube) and the upscale engines
(pipeline/upscale.py): sizes, the filter graph order (upscale → cas → cover → burn), the engine
choice and fallbacks, the AI cache key, the Real-ESRGAN model shape. Construct-only."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.models import Cue, Job
from app.pipeline import assemble, upscale


@pytest.fixture()
def job(tmp_path, monkeypatch):
    j = Job(id="u", work_dir=tmp_path / "job")
    monkeypatch.setattr(assemble, "probe_duration", lambda p: 1.2)
    monkeypatch.setattr(assemble, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(assemble, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(upscale, "_VULKAN", "0")
    segs = [(Cue(index=i, start=2.0 * i, end=2.0 * i + 1.5, text=f"c{i}"),
             j.tts_dir / f"seg_{i:04d}.mp3") for i in range(1, 3)]
    return j, segs


def _build(j, segs, **kw):
    cmd = assemble._build_render(j, segs, audio_mode="replace", orig_volume=1.0,
                                 dub_volume=1.0, **kw)
    return cmd, (j.work_dir / "filter_complex.txt").read_text(encoding="utf-8")


def test_1440_upscales_a_source_frame_only():
    assert assemble.burn_size((1920, 1080), "source", "1440") == (2560, 1440)
    assert assemble.burn_size((1080, 1920), "source", "1440") == (1440, 2560)
    assert assemble.burn_size((1922, 1080), "source", "1440") == (2560, 1440)   # snapped 16:9
    assert assemble.burn_size((3840, 2160), "source", "1440") == (2560, 1440)   # a downscale
    assert assemble.burn_size((2560, 1440), "source", "1440") == (2560, 1440)
    # the other sizes still never upscale; 9:16 stays 1080×1920
    assert assemble.burn_size((1280, 720), "source", "1080") == (1280, 720)
    assert assemble.output_size((1920, 1080), "tiktok", "1440") == (1080, 1920)


def test_gpu_upscale_comes_first_then_a_lighter_cas_then_the_burn(job):
    j, segs = job
    cmd, graph = _build(j, segs, res="1440", burn_w=2560, burn_h=1440, upscale="gpu",
                        sharpen=True, cover_mode="blur")
    assert cmd[2:6] == ["-init_hw_device", "vulkan=vk:0", "-filter_hw_device", "vk"]
    assert cmd[cmd.index("-i") + 1] == "source.mp4"
    up = graph.index("libplacebo=w=2560:h=1440:upscaler=ewa_lanczossharp")
    assert graph.index("hqdn3d") < up < graph.index("cas=0.4") < graph.index("boxblur=13:2") \
        < graph.index("ass=subs.ass")
    assert "[vscl]" not in graph and f"cas={assemble.SHARPEN_CAS:g}[" not in graph


def test_cpu_upscale_and_ai_input(job):
    j, segs = job
    cmd, graph = _build(j, segs, res="1440", burn_w=2560, burn_h=1440, upscale="cpu")
    assert "-init_hw_device" not in cmd
    assert "hqdn3d=1.5:1.5:3:3,scale=2560:1440:flags=lanczos[vup]" in graph
    cmd, graph = _build(j, segs, res="1440", burn_w=2560, burn_h=1440, upscale="ai",
                        src_file="upscaled/k.mkv", sharpen=True)
    assert cmd[cmd.index("-i") + 1] == "upscaled/k.mkv"
    assert "libplacebo" not in graph and "[vup]" not in graph and "[vscl]" not in graph
    assert "cas=0.4[vsharp]" in graph


def test_no_upscale_in_the_quick_proof_or_without_an_engine(job):
    j, segs = job
    cmd, graph = _build(j, segs, res="1440", burn_w=2560, burn_h=1440, upscale="gpu",
                        preview=True)
    assert "libplacebo" not in graph and "-init_hw_device" not in cmd
    _cmd, graph = _build(j, segs, res="1440", burn_w=2560, burn_h=1440)
    assert "scale=2560:1440:flags=lanczos,setsar=1[vscl]" in graph   # plain scale if no engine


def test_upscale_input_decisions(job, monkeypatch):
    j, _segs = job
    logs: list[str] = []
    args = dict(log=logs.append, say=lambda *_a: None, cancel=None)
    pick = assemble._upscale_input
    assert pick(j, (1920, 1080), (1920, 1080), "source", "source", False, "fast", **args) == \
        (None, "source.mp4")
    assert pick(j, (1920, 1080), (2560, 1440), "source", "1440", True, "fast", **args)[0] is None
    assert pick(j, (1920, 1080), (1080, 1920), "tiktok", "1440", False, "fast", **args)[0] is None
    assert pick(j, (3840, 2160), (2560, 1440), "source", "1440", False, "fast", **args)[0] is None
    monkeypatch.setattr(upscale, "fast_kind", lambda: "gpu")
    assert pick(j, (1920, 1080), (2560, 1440), "source", "1440", False, None, **args) == \
        ("gpu", "source.mp4")
    made = j.work_dir / "upscaled" / "x.mkv"
    monkeypatch.setattr(upscale, "ensure_ai", lambda *a, **k: made)
    assert pick(j, (1920, 1080), (2560, 1440), "source", "1440", False, "ai", **args) == \
        ("ai", "upscaled/x.mkv")

    def broken(*_a, **_k):
        raise RuntimeError("no CUDA")

    monkeypatch.setattr(upscale, "ensure_ai", broken)
    assert pick(j, (1920, 1080), (2560, 1440), "source", "1440", False, "ai", **args) == \
        ("gpu", "source.mp4")
    assert any("AI làm nét không chạy được" in m for m in logs)


def test_vulkan_device_prefers_the_discrete_card(monkeypatch):
    listing = ("[Vulkan @ 0x1] GPU listing:\n[Vulkan @ 0x1]     0: AMD Radeon 610M (integrated)\n"
               "[Vulkan @ 0x1]     1: NVIDIA GeForce RTX 4070 Laptop GPU (discrete) (0x2860)\n")
    monkeypatch.setattr(upscale, "_VULKAN", False)
    monkeypatch.setattr(upscale.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", listing))
    assert upscale.vulkan_device() == "1"
    assert upscale.hw_args("gpu") == ["-init_hw_device", "vulkan=vk:1", "-filter_hw_device", "vk"]
    assert upscale.hw_args("cpu") == []
    monkeypatch.setattr(upscale, "_VULKAN", False)
    monkeypatch.setattr(upscale.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no device"))
    assert upscale.vulkan_device() is None and upscale.fast_kind() == "cpu"


def test_ai_key_follows_the_source_and_size(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"a" * 2000)
    k = upscale.ai_key(src, 2560, 1440)
    assert k == upscale.ai_key(src, 2560, 1440) and k != upscale.ai_key(src, 1440, 2560)
    src.write_bytes(b"b" * 2000)
    assert upscale.ai_key(src, 2560, 1440) != k
    assert upscale.ai_seconds(60) == pytest.approx(60 * 30 / upscale.AI_FPS)


def test_ai_intermediate_is_reused(tmp_path, monkeypatch):
    j = Job(id="u", work_dir=tmp_path)
    j.source_video.write_bytes(b"x" * 100)
    have = upscale.ai_path(j, 2560, 1440)
    have.parent.mkdir()
    have.write_bytes(b"done")
    monkeypatch.setattr(upscale.subprocess, "Popen", lambda *a, **k: pytest.fail("ran the AI"))
    assert upscale.ensure_ai(j, 2560, 1440) == have


def test_realesrgan_model_layout():
    torch = pytest.importorskip("torch")
    from app.pipeline import upscale_ai

    m = upscale_ai.build_model(upscale.MODEL_CONVS)
    keys = list(m.state_dict())
    assert keys[0] == "body.0.weight" and keys[-1] == f"body.{2 * upscale.MODEL_CONVS + 2}.bias"
    with torch.inference_mode():
        assert tuple(m(torch.zeros(1, 3, 8, 12)).shape) == (1, 3, 32, 48)


def test_branch_quality_wins_over_the_series_resolution(tmp_path, monkeypatch):
    from app.pipeline import direct_export

    class P:
        render = {"res": "source", "voice": "x"}

    monkeypatch.setattr(direct_export.series, "profile_of", lambda job: P())
    monkeypatch.setattr(direct_export.batch, "opts_for", lambda wd, t: t)
    j = Job(id="d", work_dir=tmp_path)
    o = direct_export.render_options(j, {}, {"frame": "source", "res": "1440", "upscale": "ai"})
    assert o["res"] == "1440" and o["upscale"] == "ai"
    o = direct_export.render_options(j, {}, {"frame": "source"})
    assert o["res"] == "source"


def test_export_branch_quality_to_request():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from desktop.widgets import export_branches as eb

    assert eb.QUALITIES_BY_KEY["1440ai"] == {"res": "1440", "upscale": "ai"}
    assert eb.QUALITIES_BY_KEY["source"] == {"res": "source"}   # the label's promise
    assert eb.QUALITIES_BY_KEY[""] == {}                         # follow the series


def _child(monkeypatch, code: str):
    """ensure_ai runs this Python code as the AI child instead of the real model."""
    real = subprocess.Popen
    monkeypatch.setattr(upscale, "ai_available", lambda: True)
    monkeypatch.setattr(upscale, "ensure_model", lambda log=None: Path("m.pth"))
    monkeypatch.setattr(assemble, "_video_encoder", lambda: "libx264")
    monkeypatch.setattr(upscale.subprocess, "Popen",
                        lambda cmd, **kw: real([sys.executable, "-c", code.replace("PART", cmd[4])],
                                               **kw))


def test_ai_child_flooding_stderr_and_silent_still_cancels(tmp_path, monkeypatch):
    import time

    j = Job(id="u", work_dir=tmp_path)
    j.source_video.write_bytes(b"x" * 100)
    _child(monkeypatch, "import sys, time\nsys.stderr.write('e' * 1_000_000)\nsys.stderr.flush()"
                        "\ntime.sleep(60)")
    t0 = time.monotonic()
    with pytest.raises(InterruptedError):
        upscale.ensure_ai(j, 64, 36, cancel=lambda: time.monotonic() - t0 > 1.5)
    assert time.monotonic() - t0 < 10
    assert not list((tmp_path / "upscaled").glob("*.mkv"))


def test_ai_child_failure_leaves_no_cached_copy(tmp_path, monkeypatch):
    j = Job(id="u", work_dir=tmp_path)
    j.source_video.write_bytes(b"x" * 100)
    _child(monkeypatch, "import sys\nopen(r'PART', 'wb').write(b'half')\nprint('P 30 300')"
                        "\nraise SystemExit('ffmpeg decode failed after 30 of ~300 frames')")
    seen = []
    with pytest.raises(RuntimeError, match="decode failed"):
        upscale.ensure_ai(j, 64, 36, progress=seen.append)
    assert seen == [0.1]
    assert not list((tmp_path / "upscaled").glob("*.mkv"))
