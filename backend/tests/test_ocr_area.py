""""Khoanh vùng chữ Trung" (owner, 2026-11-01): after the download the owner draws the band
OCR reads; it is kept for the project (the render covers that band) and for the series (later
episodes — the watcher's, the bot's — use it without asking)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline import ocr, ocr_area, series  # noqa: E402

BAND = {"x": 0.25, "y": 0.84, "w": 0.5, "h": 0.14}


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


def _job(storage, name="ep3", n=3) -> Job:
    d = storage / name
    d.mkdir()
    j = Job(id=name, work_dir=d, metadata={"title": f"第2集 | 《末日校车》{n:02d}"})
    series.resolve(j)
    return j


def test_clean_and_the_crop_filter():
    assert ocr_area.clean({"x": -0.1, "y": 0.8, "w": 0.6, "h": 0.5}) == \
        {"x": 0.0, "y": 0.8, "w": 0.6, "h": 0.2}                      # kept inside the frame
    assert ocr_area.clean({"x": 0.1, "y": 0.1, "w": 0.01, "h": 0.3}) is None   # a mis-click
    assert ocr_area.clean(None) is None and ocr_area.clean({"x": 1}) is None
    assert ocr.frame_filter(2.0, "bottom-third", BAND) == (
        "fps=2.0,crop=trunc(iw*0.5000/2)*2:trunc(ih*0.1400/2)*2:trunc(iw*0.2500):trunc(ih*0.8400)")
    assert ocr.frame_filter(1.0, "bottom-third") == "fps=1.0,crop=iw:ih/3:0:ih*2/3"


def test_the_band_is_kept_for_the_project_and_the_next_episode(storage):
    ep3 = _job(storage)
    assert ocr_area.for_job(ep3) is None
    kept = ocr_area.remember(ep3, BAND)
    assert kept == BAND
    assert json.loads((ep3.work_dir / "hardsub_region.json").read_text()) == BAND   # the cover
    assert series.profile_of(ep3).ocr_area == BAND
    ep4 = _job(storage, "ep4", 4)
    assert ocr_area.for_job(ep4) == BAND                               # inherited, no question
    ocr_area.remember(ep4, {**BAND, "y": 0.5}, series_too=False)
    assert ocr_area.for_job(ep4)["y"] == 0.5 and series.profile_of(ep4).ocr_area == BAND
    assert ocr_area.remember(ep4, {"x": 0, "y": 0, "w": 0, "h": 0}) is None


def test_the_series_profile_round_trips_and_can_forget_it(storage):
    ep3 = _job(storage)
    ocr_area.remember(ep3, BAND)
    p = series.load(series.profile_of(ep3).slug)
    assert p.ocr_area == BAND
    p.ocr_area = {}
    series.save(p)
    assert ocr_area.for_job(_job(storage, "ep5", 5)) is None


# --- the worker asks, the dialog answers ----------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_worker_waits_for_the_band_and_remembers_it(qapp, storage, monkeypatch):
    from desktop.worker import PipelineWorker

    job = _job(storage)
    (job.work_dir / "hardsub_region.json").write_text(json.dumps(BAND), encoding="utf-8")
    monkeypatch.setattr("app.pipeline.hardsub_detect.detect_hardsubs", lambda *a, **k: True)
    frame = storage / "f.jpg"
    frame.write_bytes(b"x")
    monkeypatch.setattr(ocr_area, "sample_frames", lambda j, n=6: [frame])
    w = PipelineWorker(job, {})
    asked = []

    def answer(ask):
        asked.append(ask)
        w.answer_area({**BAND, "w": 0.6}, True)
    w.area_needed.connect(answer)
    got = w._ask_area(job, {"subtitle_source": "auto"})
    assert asked and asked[0]["suggestion"] == BAND and asked[0]["series"] == "末日校车"
    assert got["w"] == 0.6 and series.profile_of(job).ocr_area["w"] == 0.6


def test_no_burned_subtitles_means_no_question(qapp, storage, monkeypatch):
    from desktop.worker import PipelineWorker

    job = _job(storage)
    monkeypatch.setattr("app.pipeline.hardsub_detect.detect_hardsubs", lambda *a, **k: False)
    w = PipelineWorker(job, {})
    w.area_needed.connect(lambda _a: pytest.fail("asked although nothing is burned in"))
    assert w._ask_area(job, {"subtitle_source": "auto"}) is None


def test_the_dialog_returns_the_drawn_band_or_none(qapp, tmp_path):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QDialog

    from desktop.widgets.area_dialog import AreaDialog

    img = QImage(320, 180, QImage.Format_RGB32)
    img.fill(QColor("#334455"))
    f = tmp_path / "a.jpg"
    img.save(str(f))
    dlg = AreaDialog([f, f], BAND, "末日校车")
    assert dlg.picker.value()["y"] == pytest.approx(0.84)                # the guess, drawn
    assert "末日校车" in dlg.remember.text() and dlg.remember.isChecked()
    dlg.picker.rect = QRectF(0.2, 0.8, 0.6, 0.15)
    dlg._accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.result_area() == {"x": 0.2, "y": 0.8, "w": 0.6, "h": 0.15}
    other = AreaDialog([f], None, None)
    other.reject()
    assert other.result_area() is None and not other.remember.isVisible()


def test_a_reread_puts_the_old_lines_aside_and_keeps_the_rest(storage):
    job = _job(storage)
    d = job.work_dir
    for n in ("cn.srt", "vi.srt", "ocr_frames.json", "overrides.json", "style.json",
              "source.mp4", "approved.json", "ocr_area.json"):
        (d / n).write_text(n, encoding="utf-8")
    dest = ocr_area.set_aside(d)
    assert dest is not None and dest.parent.name == "backup"
    for n in ("cn.srt", "vi.srt", "ocr_frames.json", "overrides.json", "ocr_area.json"):
        assert not (d / n).exists() and (dest / n).read_text(encoding="utf-8") == n
    for n in ("style.json", "source.mp4", "approved.json"):
        assert (d / n).exists()                                    # look, source, history stay
    assert ocr_area.set_aside(d) is None                           # nothing left to move


def test_the_library_offers_a_reread_for_an_episode(qapp, storage):
    from desktop.views.library_view import LibraryView  # noqa: F401 (the signal exists)
    from desktop.views import library_view

    assert hasattr(library_view.LibraryView, "rereadRequested")
