"""N-21: two heavy stages must never run concurrently (8 GB VRAM rule)."""
from __future__ import annotations

import threading
import time

from app.heavy import heavy_task


def test_heavy_tasks_serialize():
    active = 0
    peak = 0
    lock = threading.Lock()

    def work():
        nonlocal active, peak
        with heavy_task("t"):
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1                      # never two heavy bodies at once


def test_reentrant_no_deadlock():
    with heavy_task("outer"):
        with heavy_task("inner"):         # a stage calling another wrapped stage
            assert True
