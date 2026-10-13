"""E-14: per-job AI cost and run time."""
from __future__ import annotations

import threading

from app import usage


class U:                                   # an OpenAI-SDK-like usage object
    def __init__(self, pin, pout, cost):
        self.prompt_tokens, self.completion_tokens = pin, pout
        self.model_extra = {"cost": cost}


def test_calls_inside_a_run_are_added_up_per_job(tmp_path):
    with usage.track(tmp_path, phase="gate"):
        usage.record("a/x", U(100, 50, 0.002))
        t = threading.Thread(target=usage.record, args=("b/y", {"prompt_tokens": 10,
                                                                "completion_tokens": 5,
                                                                "cost": 0.001}))
        t.start()
        t.join()                           # worker threads (parallel batches) count too
    s = usage.summary(tmp_path)
    assert s["calls"] == 2 and abs(s["cost"] - 0.003) < 1e-9 and s["seconds"] >= 0
    assert usage.label(tmp_path).startswith("AI $0.003")


def test_nothing_is_recorded_outside_a_run(tmp_path):
    usage.record("a/x", U(1, 1, 1.0))
    assert usage.summary(tmp_path) == {"cost": 0.0, "calls": 0, "seconds": 0.0}


def test_bad_usage_never_raises(tmp_path):
    with usage.track(tmp_path, phase="render"):
        usage.record("a/x", object())
        usage.record("a/x", None)
    assert usage.summary(tmp_path)["calls"] == 2
