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


@pytest.fixture(autouse=True)
def _package(monkeypatch):
    """The default model package, no hand-picked role models (the developer's .env must
    not change what these tests price)."""
    from app.config import settings
    from app.providers.roles import ROLES

    monkeypatch.setattr(settings, "model_preset", "")
    for r in ROLES.values():
        monkeypatch.setattr(settings, r.attr, "")
    return settings


@pytest.mark.parametrize("seconds, cost, wall", [(137, 0.045, 87), (329, 0.043, 114),
                                                 (1662, 0.285, 287)])
def test_the_measured_runs_fall_inside_the_range(seconds, cost, wall, _package):
    """Real runs with the user's usual options and Kimi K3 as refiner (the package "kimi"
    = the defaults until 2026-10-27): vlog, song (2026-10-14) and a 27:42 tech review
    (2026-10-26, job 703e…: gate at 287 s, $0.272 + ~$0.0125 Whisper word track)."""
    _package.model_preset = "kimi"
    e = est.estimate(seconds, USUAL)
    assert e.cost[0] <= cost <= e.cost[1]
    assert e.seconds[0] <= wall <= e.seconds[1]
    assert e.basis == est.STATIC_BASIS


def test_a_long_talk_is_no_longer_estimated_at_two_to_four_times_its_real_time():
    """The 27:42 review was shown '8–19 phút' (ASR) and reached the gate in 287 s: the
    per-minute rates had absorbed the fixed latency of two short clips."""
    e = est.estimate(1662, USUAL)
    assert e.seconds[1] <= 2 * 287 and e.seconds[0] >= 287 / 2
    assert "4–8 phút" in e.label()


def test_auto_source_costs_one_path_not_both(monkeypatch):
    """auto runs OCR OR ASR: the high end is the slower path, not their sum."""
    monkeypatch.setattr(est, "_local_ocr_speed", lambda: est.OCR_GPU_SEC_PER_MIN)
    asr = est.estimate(1662, USUAL)
    ocr = est.estimate(1662, {**USUAL, "subtitle_source": "ocr"})
    auto = est.estimate(1662, {**USUAL, "subtitle_source": "auto"})
    detect = est.DETECT_SEC
    assert auto.seconds[0] == pytest.approx(asr.seconds[0] + detect[0], abs=1)
    assert auto.seconds[1] == pytest.approx(ocr.seconds[1] + detect[1], abs=1)
    assert auto.seconds[1] < asr.seconds[1] + ocr.seconds[1] - est.SEC_FIXED[1] - 60
    assert any("OCR" in n for n in auto.notes)


def test_local_ocr_stays_honest_on_a_long_video(monkeypatch):
    """CPU OCR is 0.25–0.5 s a frame at 2 fps: 30 minutes take 15–30 minutes."""
    monkeypatch.setattr(est, "_local_ocr_speed", lambda: est.OCR_LOCAL_SEC_PER_MIN)
    e = est.estimate(1800, {**USUAL, "subtitle_source": "ocr"})
    assert e.seconds[0] >= 15 * 60 and e.seconds[1] >= 30 * 60


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


def _project(base: Path, name: str, *, minutes: float, cost: float, secs: float,
             vi: bool = True, meta: dict | None = None, opts: dict | None = None) -> Path:
    d = base / name
    d.mkdir()
    (d / "metadata.json").write_text(json.dumps({"duration": minutes * 60} if meta is None
                                                else meta), encoding="utf-8")
    (d / "usage.json").write_text(json.dumps(
        {"models": {"m": {"calls": 1, "cost": cost}}, "seconds": {"gate": secs}}), encoding="utf-8")
    if vi:
        (d / "vi.srt").write_text("", encoding="utf-8")
    if opts is not None:
        (d / "job.json").write_text(json.dumps({"opts": opts}), encoding="utf-8")
    return d


def _mid(r):
    return (r[0] + r[1]) / 2


def test_own_history_calibrates_the_model_after_three_projects(tmp_path):
    """Past projects that took twice what the model says → the range doubles."""
    for k, minutes in enumerate((2, 5, 10)):
        cost, secs, _n = est._model(minutes, USUAL)
        _project(tmp_path, f"j{k}", minutes=minutes, cost=2 * _mid(cost), secs=2 * _mid(secs))
    hist = est.history(tmp_path)
    assert len(hist) == 3
    static = est.estimate(1662, USUAL)
    e = est.estimate(1662, USUAL, history=hist)
    assert e.basis == "từ 3 dự án trước"
    assert e.seconds == pytest.approx((2 * static.seconds[0], 2 * static.seconds[1]), abs=1)
    assert e.cost == pytest.approx((2 * static.cost[0], 2 * static.cost[1]), rel=1e-3)
    assert est.estimate(600, USUAL, history=hist[:2]).basis == est.STATIC_BASIS


def test_short_clips_in_history_do_not_inflate_a_long_video(tmp_path):
    """Raw seconds-per-minute of 2-minute clips (mostly fixed latency: 87 s for 2.3 min =
    38 s/min) used to be multiplied by 27.7 minutes. Clips that ran as modelled leave a
    long video's estimate where the model puts it."""
    for k, minutes in enumerate((2.3, 2.0, 2.5)):
        cost, secs, _n = est._model(minutes, USUAL)
        _project(tmp_path, f"c{k}", minutes=minutes, cost=_mid(cost), secs=_mid(secs))
    e = est.estimate(1662, USUAL, history=est.history(tmp_path))
    assert e.seconds == pytest.approx(est.estimate(1662, USUAL).seconds, abs=1)
    assert e.seconds[0] <= 287 <= e.seconds[1]


def test_history_skips_runs_that_never_reached_the_gate(tmp_path):
    """A failed run (2026-10-26: ASR only, 171 s, no vi.srt) is not what a run costs."""
    _project(tmp_path, "done", minutes=27.7, cost=0.27, secs=287)
    _project(tmp_path, "failed", minutes=27.7, cost=0.0047, secs=171.5, vi=False)
    assert [h["seconds"] for h in est.history(tmp_path)] == [287]


def test_history_reads_a_local_files_length_from_its_audio(tmp_path):
    """A local file's metadata.json is just {"platform": "local"}: that project was
    silently dropped from the history."""
    import wave

    d = _project(tmp_path, "local", minutes=0, cost=0.27, secs=287, meta={"platform": "local"})
    with wave.open(str(d / "source.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000 * 90)                   # 90 s
    (h,) = est.history(tmp_path)
    assert h["minutes"] == pytest.approx(1.5)


def test_history_models_each_project_with_its_own_options(tmp_path, monkeypatch):
    """Slow local-OCR projects that ran as the OCR model says must not make an ASR video
    look slow; auto resolves to the path it took (an OCR run leaves ocr_report.json)."""
    monkeypatch.setattr(est, "_local_ocr_speed", lambda: est.OCR_LOCAL_SEC_PER_MIN)
    ocr = {**USUAL, "subtitle_source": "ocr"}
    cost, secs, _n = est._model(5, ocr)
    _project(tmp_path, "o1", minutes=5, cost=_mid(cost), secs=_mid(secs), opts=ocr)
    _project(tmp_path, "o2", minutes=5, cost=_mid(cost), secs=_mid(secs), opts=ocr)
    a = _project(tmp_path, "a", minutes=5, cost=_mid(cost), secs=_mid(secs),
                 opts={**USUAL, "subtitle_source": "auto"})
    (a / "ocr_report.json").write_text("[]", encoding="utf-8")
    hist = est.history(tmp_path)
    assert sorted(h["opts"]["subtitle_source"] for h in hist) == ["ocr"] * 3
    e = est.estimate(1662, USUAL, history=hist)
    assert e.basis == "từ 3 dự án trước"
    assert e.seconds == pytest.approx(est.estimate(1662, USUAL).seconds, abs=1)
    _project(tmp_path, "s", minutes=5, cost=0.1, secs=300, opts={**USUAL, "subtitle_source": "auto"})
    assert "asr" in [h["opts"]["subtitle_source"] for h in est.history(tmp_path)]


def test_the_whisper_word_track_counts_in_a_projects_cost(tmp_path):
    """usage.json now itemises the Whisper timing calls; history adds every model."""
    d = _project(tmp_path, "w", minutes=27.7, cost=0.272, secs=287)
    data = json.loads((d / "usage.json").read_text(encoding="utf-8"))
    data["models"]["openai/whisper-large-v3"] = {"calls": 7, "in": 0, "out": 0, "cost": 0.0125}
    (d / "usage.json").write_text(json.dumps(data), encoding="utf-8")
    (h,) = est.history(tmp_path)
    assert h["cost"] == pytest.approx(0.2845)


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


# --- 2026-10-27 cost pass: the estimate follows the model package --------------------

def test_the_default_package_prices_the_cost_pass_measurement():
    """Cân bằng on the 27:42 review: measured $0.0016/min to the gate + $0.0007 brief
    (120-line bench extrapolated, docs/plan/G-cost-log.md) — ~1/6 of the Kimi run."""
    e = est.estimate(1662, USUAL)
    assert e.cost[0] <= 0.0016 * 27.7 + 0.0007 <= e.cost[1]
    assert e.cost[1] < 0.285 / 2
    assert any("Gói mô hình: Cân bằng" in n for n in e.notes)


@pytest.mark.parametrize("key", ["economy", "balanced", "quality", "kimi"])
def test_every_package_shows_a_cost_its_estimate_agrees_with(key, _package):
    """The $/minute shown next to a package in Settings lies inside what the before-Start
    estimate says for a long talk with that package."""
    from app.providers import presets

    _package.model_preset = key
    p = presets.PRESETS[key]
    e = est.estimate(1662, USUAL)
    assert e.cost[0] <= p.usd_per_min * 1662 / 60 + p.usd_per_video <= e.cost[1]


def test_packages_order_by_price(_package):
    his = {}
    for key in ("economy", "balanced", "quality", "kimi"):
        _package.model_preset = key
        his[key] = est.estimate(600, USUAL).cost[1]
    assert his["economy"] <= his["balanced"] < his["kimi"]
    assert his["kimi"] > 3 * his["balanced"]


def test_an_unmeasured_model_is_priced_wide_and_said_so(_package):
    _package.model_refine = "someone/new-model-9"
    e = est.estimate(600, USUAL)
    assert any("someone/new-model-9" in n for n in e.notes)
    assert any("tự chọn" in n for n in e.notes)
    no_refine = est.estimate(600, {**USUAL, "refine": None})
    assert e.cost[1] - no_refine.cost[1] == pytest.approx(est.UNKNOWN_LINE_COST[1] * 10 * 28,
                                                          abs=1e-3)


def test_kimi_projects_do_not_calibrate_the_cost_of_a_cheaper_package(tmp_path, _package):
    """History from runs paid with Kimi K3 would push the cheap package's estimate up to
    the 4× clamp; those runs still calibrate the time."""
    _package.model_preset = "kimi"
    for k, minutes in enumerate((2, 5, 10)):
        cost, secs, _n = est._model(minutes, USUAL)
        d = _project(tmp_path, f"k{k}", minutes=minutes, cost=_mid(cost), secs=2 * _mid(secs))
        (d / "usage.json").write_text(json.dumps(
            {"models": {"moonshotai/kimi-k3": {"calls": 3, "cost": _mid(cost)}},
             "seconds": {"gate": 2 * _mid(secs)}}), encoding="utf-8")
    hist = est.history(tmp_path)
    assert all(h["models"] == ["moonshotai/kimi-k3"] for h in hist)
    kimi = est.estimate(1662, USUAL, history=hist)
    assert kimi.cost == pytest.approx(est.estimate(1662, USUAL).cost, rel=1e-3)   # ratio 1
    _package.model_preset = ""
    cheap = est.estimate(1662, USUAL, history=hist)
    static = est.estimate(1662, USUAL)
    assert cheap.basis == "từ 3 dự án trước"
    assert cheap.cost == pytest.approx(static.cost, rel=1e-3)
    assert cheap.seconds == pytest.approx((2 * static.seconds[0], 2 * static.seconds[1]), abs=1)
