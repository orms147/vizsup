"""heavy_task: one heavy job on the graphics card at a time — threads of this process and other
processes (the open app + the autopilot task), re-entrant, cancellable while waiting."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import heavy
from app.config import settings

BACKEND = Path(__file__).resolve().parents[1]


def test_reentrant_in_one_thread():
    with heavy.heavy_task("outer"):
        with heavy.heavy_task("inner"):
            assert heavy.holder() == "outer"


def test_a_second_thread_waits_and_says_for_what():
    logs: list[str] = []
    order: list[str] = []
    entered = threading.Event()

    def other():
        with heavy.heavy_task("VieNeu", log=logs.append):
            order.append("other")

    with heavy.heavy_task("AI làm nét"):
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.8)
        order.append("first done")
        entered.set()
    t.join(5)
    assert order == ["first done", "other"]
    assert logs and "AI làm nét" in logs[0] and "VieNeu" in logs[0]


def test_cancel_while_waiting():
    stop = threading.Event()
    result: list[object] = []

    def other():
        try:
            with heavy.heavy_task("x", cancel=stop.is_set):
                result.append("ran")
        except InterruptedError:
            result.append("cancelled")

    with heavy.heavy_task("holder"):
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.3)
        stop.set()
        t.join(5)
    assert result == ["cancelled"]


def test_another_process_waits_for_the_card():
    """The lock file is an OS lock: a second Python process blocks until this one lets go."""
    code = ("import sys, time; sys.path.insert(0, sys.argv[2]); from app.config import settings;"
            "settings.vizsup_storage_dir = sys.argv[1]; from app import heavy;"
            "t = time.time();"
            "ctx = heavy.heavy_task('child'); ctx.__enter__(); print(round(time.time() - t, 2))")
    with heavy.heavy_task("parent"):
        p = subprocess.Popen([sys.executable, "-c", code, str(settings.vizsup_storage_dir),
                              str(BACKEND)], stdout=subprocess.PIPE, text=True)
        time.sleep(1.5)
        assert p.poll() is None                   # still waiting
    out, _ = p.communicate(timeout=20)
    assert float(out.strip()) >= 1.0
    with heavy.heavy_task("after"):               # and the lock is free again afterwards
        pass


def test_the_ai_pass_runs_under_the_lock(tmp_path, monkeypatch):
    from app.models import Job
    from app.pipeline import upscale

    j = Job(id="u", work_dir=tmp_path)
    j.source_video.write_bytes(b"x" * 100)
    monkeypatch.setattr(upscale, "ai_available", lambda: True)
    monkeypatch.setattr(upscale, "ensure_model", lambda log=None: Path("m.pth"))
    seen = []

    def fake(job, out, w, h, model, **k):
        seen.append(heavy.holder())
        return out

    monkeypatch.setattr(upscale, "_make_ai", fake)
    upscale.ensure_ai(j, 64, 36)
    assert seen == ["AI làm nét"]


@pytest.mark.parametrize("cancel_at", [0])
def test_vieneu_cancelled_while_waiting_reports_every_line(monkeypatch, cancel_at):
    from app.providers.tts import vieneu

    class Eng:
        backend = "pytorch"
        sample_rate = 24000

    monkeypatch.setattr(vieneu, "_acquire", lambda: Eng())
    monkeypatch.setattr(vieneu, "_release", lambda e: None)
    stop = threading.Event()
    got: list = []

    def run():
        got.extend(vieneu.VieNeuTTS().synthesize_many(
            [("1", "a", "p1"), ("2", "b", "p2")], cancel=stop.is_set))

    with heavy.heavy_task("AI làm nét"):
        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.3)
        stop.set()
        t.join(5)
    assert [k for k, _e in got] == ["1", "2"]
    assert all(isinstance(e, InterruptedError) for _k, e in got)
