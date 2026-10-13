"""Long videos: OCR frames stream from ffmpeg (no PNG dump), report progress, cancel
between frames and resume from ocr_frames.json."""
from __future__ import annotations

import io
import json

import numpy as np
import pytest

from app.models import Job
from app.pipeline import ocr

cv2 = pytest.importorskip("cv2")

FPS = 2.0
# 6 subtitles, 4 frames (2 s) each, then 2 empty frames
SCRIPT = [k // 4 + 1 if k < 24 else 0 for k in range(26)]
TEXT = dict(enumerate(["今天我们去学校上课", "老师讲了一个故事", "下午一起打篮球",
                       "晚上回家吃饭", "妈妈做了红烧肉", "明天见各位朋友"], start=1))


class _Out:
    def __init__(self, txts):
        self.txts = tuple(txts)
        self.scores = tuple(0.99 for _ in txts)
        self.boxes = None


class _Engine:
    calls = 0

    def __call__(self, img):
        type(self).calls += 1
        n = int(img[0, 0, 0])
        return _Out([TEXT[n]] if n else [])


def _frames(start_index=0):
    for n in SCRIPT[start_index:]:
        yield np.full((6, 10, 3), n, dtype=np.uint8)


@pytest.fixture()
def job(tmp_path, monkeypatch):
    j = Job(id="o", work_dir=tmp_path / "o")
    j.source_video.write_bytes(b"video")
    monkeypatch.setattr(ocr, "_build_ocr", _Engine)
    monkeypatch.setattr("app.pipeline.assemble.probe_duration", lambda p: len(SCRIPT) / FPS)
    _Engine.calls = 0
    return j


def test_frames_stream_out_of_a_bmp_pipe_in_any_chunk_size(monkeypatch):
    imgs = [np.full((5, 7, 3), v, dtype=np.uint8) for v in (10, 200, 77)]
    blob = b"".join(cv2.imencode(".bmp", im)[1].tobytes() for im in imgs)

    class Trickle(io.BytesIO):
        def read(self, n=-1):                       # a pipe hands back a few bytes at a time
            return super().read(min(n, 13) if n and n > 0 else 13)

    class Proc:
        stdout = Trickle(blob)

        def poll(self):
            return 0

        def wait(self):
            return 0

    seen = []
    monkeypatch.setattr(ocr.subprocess, "Popen", lambda cmd, **kw: seen.append(cmd) or Proc())
    out = list(ocr._frame_stream(ocr.Path("v.mp4"), "fps=2", start=12.5))
    assert [int(im[0, 0, 0]) for im in out] == [10, 200, 77]
    assert seen[0][seen[0].index("-ss") + 1] == "12.500" and "image2pipe" in seen[0]


def test_ocr_cues_progress_and_no_png_dump(job, monkeypatch):
    monkeypatch.setattr(ocr, "_frame_stream", lambda src, vf, start=0.0: _frames())
    ticks = []
    ocr.extract_hardsubs(job, progress=lambda f, m: ticks.append((f, m)))
    cues = [ln for ln in job.cn_srt.read_text(encoding="utf-8").splitlines() if ln in TEXT.values()]
    assert cues == [TEXT[n] for n in range(1, 7)]
    report = json.loads((job.work_dir / "ocr_report.json").read_text(encoding="utf-8"))
    assert report[1]["rep_t"] == pytest.approx(2.0, abs=1.0) and "rep_frame" not in report[1]
    assert ticks[-1][0] == 1.0 and "khung" in ticks[-1][1]
    assert not (job.work_dir / "frames").exists() or not any((job.work_dir / "frames").iterdir())


def test_cancelled_ocr_resumes_where_it_stopped(job, monkeypatch):
    monkeypatch.setattr(ocr, "CHECKPOINT_EVERY", 5)
    starts = []

    def stream(src, vf, start=0.0):
        starts.append(start)
        return _frames(int(round(start * FPS)))

    monkeypatch.setattr(ocr, "_frame_stream", stream)
    n = {"k": 0}

    def cancel():
        n["k"] += 1
        return n["k"] > 12                          # stop after 12 frames were queued

    with pytest.raises(InterruptedError):
        ocr.extract_hardsubs(job, cancel=cancel)
    saved = json.loads((job.work_dir / ocr.CHECKPOINT).read_text(encoding="utf-8"))["frames"]
    assert len(saved) == 12 and _Engine.calls == 12
    ocr.extract_hardsubs(job)                       # resume
    assert starts[-1] == pytest.approx(12 / FPS) and _Engine.calls == len(SCRIPT)
    cues = [ln for ln in job.cn_srt.read_text(encoding="utf-8").splitlines() if ln in TEXT.values()]
    assert cues == [TEXT[n] for n in range(1, 7)]   # same as an uninterrupted run


def test_a_different_source_or_region_starts_over(job, monkeypatch):
    monkeypatch.setattr(ocr, "_frame_stream", lambda src, vf, start=0.0: _frames())
    ocr.extract_hardsubs(job)
    first = _Engine.calls
    ocr.extract_hardsubs(job, crop="bottom-quarter")     # other crop → other key
    assert _Engine.calls == 2 * first
