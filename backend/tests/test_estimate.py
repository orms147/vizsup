"""F-5: cost and time shown before Start."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from app import estimate as est

USUAL = {"subtitle_source": "asr", "asr": "audio_llm", "translator": "openrouter",
         "refine": "openrouter", "auto_context": True, "ai_review": True}


@pytest.mark.parametrize("seconds, cost, wall", [(137, 0.045, 87), (329, 0.043, 114)])
def test_the_two_measured_runs_fall_inside_the_range(seconds, cost, wall):
    """Real runs on 2026-10-14 with the user's usual options (vlog, song)."""
    e = est.estimate(seconds, USUAL)
    assert e.cost[0] <= cost <= e.cost[1]
    assert e.seconds[0] <= wall <= e.seconds[1]
    assert e.basis == "đo 14/10"


def test_choices_change_the_estimate():
    base = est.estimate(300, USUAL)
    no_refine = est.estimate(300, {**USUAL, "refine": None})
    assert no_refine.cost[1] < base.cost[1]
    free = est.estimate(300, {"subtitle_source": "srt", "translator": "passthrough"})
    assert free.cost == (0.0, 0.0) and "miễn phí" in free.label() and free.seconds[1] <= 20
    local = est.estimate(300, {**USUAL, "subtitle_source": "ocr"})
    cloud = est.estimate(300, {**USUAL, "subtitle_source": "ocr", "ocr_engine": "vision_llm"})
    assert local.seconds[1] > cloud.seconds[1] and cloud.cost[1] > local.cost[1]


def test_long_video_mentions_the_parts_and_reads_in_hours():
    e = est.estimate(3 * 3600, USUAL)
    assert "3:00:00" in e.label() and any("6 phần" in n for n in e.notes)


def test_own_history_takes_over_after_three_projects(tmp_path):
    for k, (minutes, cost, secs) in enumerate([(2, 0.04, 80), (5, 0.05, 120), (10, 0.12, 300)]):
        d = tmp_path / f"j{k}"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({"duration": minutes * 60}), encoding="utf-8")
        (d / "usage.json").write_text(json.dumps(
            {"models": {"m": {"cost": cost}}, "seconds": {"gate": secs}}), encoding="utf-8")
    hist = est.history(tmp_path)
    assert len(hist) == 3
    e = est.estimate(600, USUAL, history=hist)
    assert e.basis == "từ 3 dự án trước"
    assert 0.09 <= e.cost[0] <= e.cost[1] <= 0.21          # $0.01–0.02 per minute × 10 min
    assert est.estimate(600, USUAL, history=hist[:2]).basis == "đo 14/10"


def test_start_screen_shows_the_estimate_for_a_file(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.providers.registry import list_providers
    from desktop.views import input_view

    monkeypatch.setattr("app.pipeline.assemble.probe_duration", lambda p: 329.0)
    view = input_view.InputView(list_providers())
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"v")
    view._set_file(str(f))
    view._update_estimate()                                  # skip the debounce
    for _ in range(100):
        app.processEvents()
        if "AI" in view.estimate.text() or "miễn phí" in view.estimate.text():
            break
        time.sleep(0.02)
    assert view.estimate.isVisibleTo(view) and "5:29 video" in view.estimate.text()
    view.shutdown()


def test_an_expired_cookie_is_flagged_before_start(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.pipeline import download
    from app.providers.registry import list_providers
    from desktop.views import input_view

    def expired(url):
        raise RuntimeError("Douyin cần cookie mới để tải. Cookie trong tệp đã hết hạn…")

    monkeypatch.setattr(download, "probe", expired)
    view = input_view.InputView(list_providers())
    view.url.setText("https://www.douyin.com/video/7688721076042876378")
    view._update_estimate()
    for _ in range(100):
        app.processEvents()
        if "cookie" in view.estimate.text():
            break
        time.sleep(0.02)
    assert "cookie mới" in view.estimate.text()
    view.shutdown()


def test_local_ocr_time_follows_the_gpu_and_the_fast_option(monkeypatch):
    ocr_job = {**USUAL, "subtitle_source": "ocr"}
    monkeypatch.setattr(est, "_local_ocr_speed", lambda: est.OCR_LOCAL_SEC_PER_MIN)
    cpu = est.estimate(1800, ocr_job)
    monkeypatch.setattr(est, "_local_ocr_speed", lambda: est.OCR_GPU_SEC_PER_MIN)
    gpu = est.estimate(1800, ocr_job)
    fast = est.estimate(1800, {**ocr_job, "ocr_fast": True})
    assert cpu.seconds[1] > gpu.seconds[1] > fast.seconds[1]
    ocr_part = est.OCR_GPU_SEC_PER_MIN[1] * 30
    assert gpu.seconds[1] - fast.seconds[1] == pytest.approx(ocr_part / 2)
