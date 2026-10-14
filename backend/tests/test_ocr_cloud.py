"""Cloud OCR (providers/ocr/vision_llm.py): strips tiled into one request, the JSON
answer parsed per strip, retries on network drops, the subtitle band cropped, and the
checkpoint never mixing two engines' reads. No network: the OpenRouter call is faked."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from app.models import Job
from app.pipeline import ocr
from app.providers.ocr import vision_llm
from app.providers.registry import get_ocr, list_providers

FPS = 2.0


def _resp(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeOpenRouter:
    """Answers each request from the strip numbers it was asked about."""

    def __init__(self, script, fail_first=0):
        self.script, self.fail_first, self.calls, self.models = script, fail_first, 0, []

    def available(self):
        return True

    def _chat(self, *, model, messages, **kw):
        self.calls += 1
        self.models.append(model)
        if self.calls <= self.fail_first:
            raise ConnectionError("Server disconnected without sending a response.")
        n = int(messages[0]["content"][0]["text"].split("stacks ")[1].split(" ")[0])
        return _resp("```json\n" + json.dumps({str(k): self.script(k) for k in range(1, n + 1)},
                                              ensure_ascii=False) + "\n```")


@pytest.fixture()
def fake(monkeypatch):
    def install(script, fail_first=0):
        f = FakeOpenRouter(script, fail_first)
        monkeypatch.setattr(vision_llm.VisionLLMOCR, "_client", lambda self: f)
        monkeypatch.setattr(vision_llm.time, "sleep", lambda s: None)
        return f
    return install


def test_registered_and_labelled_as_uploading():
    names = [p["name"] for p in list_providers()["ocr"]]
    assert names[0] == "rapidocr"                                  # local stays the default
    cloud = get_ocr("vision_llm")
    assert not cloud.local and "gửi" in cloud.display_name and cloud.crop_to_band


def test_parse_handles_fences_gaps_and_garbage():
    assert vision_llm.parse('```json\n{"1": "你好", "2": ""}\n```', 2) == ["你好", ""]
    assert vision_llm.parse('{"1": "a"}', 3) == ["a", None, None]
    assert vision_llm.parse("sorry, I cannot", 2) == [None, None]


def test_strips_per_request_follow_the_band_height():
    assert vision_llm.per_tile(94, 720) == 5          # a thin band → several strips
    assert vision_llm.per_tile(240, 1280) == 3
    assert vision_llm.per_tile(2000, 640) == 1


def test_tile_is_one_small_jpeg():
    imgs = [np.full((94, 720, 3), k * 20, np.uint8) for k in range(5)]
    jpg = vision_llm.tile(imgs)
    assert jpg[:2] == b"\xff\xd8" and len(jpg) < 60_000


def test_reader_retries_drops_and_splits_lines(fake):
    f = fake(lambda k: "作詞：蔡健雅\n作曲：蔡健雅" if k == 2 else f"第{k}句", fail_first=2)
    prov = get_ocr("vision_llm")
    imgs = [np.zeros((94, 720, 3), np.uint8)] * 7
    with prov.reader() as read:
        out = read(imgs)
    assert len(out) == 7 and out[0] == [("第1句", 1.0)]
    assert out[1] == [("作詞：蔡健雅", 1.0), ("作曲：蔡健雅", 1.0)]
    assert f.calls == 2 + 2                     # 7 strips → 2 requests, after 2 dropped sends
    assert set(f.models) == {"qwen/qwen3.7-flash"}      # the "ocr" role default


def test_reader_gives_up_with_a_resumable_message(fake, monkeypatch):
    fake(lambda k: "x", fail_first=99)
    with get_ocr("vision_llm").reader() as read, pytest.raises(RuntimeError, match="chạy tiếp"):
        read([np.zeros((94, 720, 3), np.uint8)])


def test_band_rows_map_the_full_frame_region_into_the_strip():
    # bottom-third strip covers y 0.667..1.0; a band at y 0.70..0.75 of the frame
    top, bot = ocr.band_rows({"x": 0, "y": 0.70, "w": 1, "h": 0.05}, "bottom-third")
    assert 0.0 < top < 0.10 < 0.25 < bot < 0.35        # padded, inside the strip
    assert ocr.band_rows(None, "bottom-third") is None


def test_cloud_ocr_end_to_end_crops_the_band_and_keys_the_checkpoint(fake, monkeypatch, tmp_path):
    job = Job(id="c", work_dir=tmp_path / "c")
    job.source_video.write_bytes(b"v")
    (job.work_dir / "hardsub_region.json").write_text(
        json.dumps({"x": 0.1, "y": 0.70, "w": 0.8, "h": 0.05}), encoding="utf-8")
    frames = [np.zeros((426, 720, 3), np.uint8) for _ in range(12)]
    seen_heights = []
    monkeypatch.setattr(ocr, "_frame_stream", lambda src, vf, start=0.0: iter(frames[int(start * FPS):]))
    monkeypatch.setattr("app.pipeline.assemble.probe_duration", lambda p: len(frames) / FPS)
    real_tile = vision_llm.tile
    monkeypatch.setattr(vision_llm, "tile",
                        lambda imgs: seen_heights.extend(i.shape[0] for i in imgs) or real_tile(imgs))
    text = {k: "你好世界" for k in range(1, 13)}
    fake(lambda k: text[k])
    ocr.extract_hardsubs(job, engine="vision_llm")
    assert seen_heights and max(seen_heights) < 426 * 0.5     # only the band was sent
    assert "你好世界" in job.cn_srt.read_text(encoding="utf-8")
    key = json.loads((job.work_dir / ocr.CHECKPOINT).read_text(encoding="utf-8"))["key"]
    assert "vision_llm:qwen/qwen3.7-flash" in key

    # the local engine must not resume from the cloud engine's reads
    reads = []

    class Local:
        def __call__(self, img):
            reads.append(1)
            return None

    monkeypatch.setattr(ocr, "_build_ocr", lambda: Local())
    ocr.extract_hardsubs(job, engine="rapidocr")
    assert len(reads) == len(frames)


def test_cloud_reads_have_no_invented_confidence(fake, monkeypatch, tmp_path):
    """Review: cloud cues carried conf 1.0, so 'AI sửa lỗi OCR' skipped everything as
    'no suspect line'. They now carry no score and the proofread covers every line."""
    job = Job(id="n", work_dir=tmp_path / "n")
    job.source_video.write_bytes(b"v")
    frames = [np.zeros((426, 720, 3), np.uint8) for _ in range(6)]
    monkeypatch.setattr(ocr, "_frame_stream", lambda src, vf, start=0.0: iter(frames[int(start * FPS):]))
    monkeypatch.setattr("app.pipeline.assemble.probe_duration", lambda p: len(frames) / FPS)
    monkeypatch.setattr(ocr, "_ensure_region", lambda job, crop, log: None)
    fake(lambda k: "你好世界")
    ocr.extract_hardsubs(job, engine="vision_llm")
    rep = json.loads((job.work_dir / "ocr_report.json").read_text(encoding="utf-8"))
    assert rep and all(r["conf"] is None and r["suspect"] is False for r in rep)
    assert ocr._suspect_indexes(job, len(rep)) is None          # → proofread every line


def test_auto_detection_uses_the_cloud_engine_without_local_ocr(fake, monkeypatch, tmp_path):
    """Review: with no local OCR installed, "Tự động" fell back to ASR even when the user
    picked the cloud reader. The sampled frames are now read by that reader."""
    import shutil
    import subprocess

    from app.ffmpegutil import ffmpeg_bin
    from app.pipeline import hardsub_detect

    if shutil.which(ffmpeg_bin()) is None and not __import__("os").path.exists(ffmpeg_bin()):
        pytest.skip("ffmpeg missing")
    job = Job(id="d", work_dir=tmp_path / "d")
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=5:duration=8", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(job.source_video)], check=True)

    def no_local():
        raise RuntimeError('OCR chưa được cài. Chạy: pip install -e ".[ocr]"')

    monkeypatch.setattr(ocr, "_build_ocr", no_local)
    with pytest.raises(RuntimeError):                       # local engine chosen: unchanged
        hardsub_detect.detect_hardsubs(job, samples=4)
    f = fake(lambda k: "这是字幕")
    assert hardsub_detect.detect_hardsubs(job, samples=4, engine="vision_llm") is True
    assert f.calls >= 1
    fake(lambda k: "")
    assert hardsub_detect.detect_hardsubs(job, samples=4, engine="vision_llm") is False


def test_strips_the_model_keeps_skipping_are_reported(monkeypatch):
    class Lazy(FakeOpenRouter):
        def _chat(self, *, model, messages, **kw):
            self.calls += 1
            return _resp('{"1": "你好"}')                     # never answers for strip 2

    lazy = Lazy(lambda k: "")
    monkeypatch.setattr(vision_llm.VisionLLMOCR, "_client", lambda self: lazy)
    monkeypatch.setattr(vision_llm.time, "sleep", lambda s: None)
    logs = []
    with get_ocr("vision_llm").reader(log=logs.append) as read:
        out = read([np.zeros((94, 720, 3), np.uint8)] * 2)
    assert out == [[("你好", 1.0)], []] and lazy.calls == vision_llm.ATTEMPTS
    assert logs and "1 khung" in logs[0]
