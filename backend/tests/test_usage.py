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


def test_cached_prompt_tokens_are_recorded(tmp_path):
    """C2: the share of the input served from the provider's prompt cache, per model."""
    from types import SimpleNamespace as NS

    from app import usage

    assert usage.cached_tokens(None) == 0
    assert usage.cached_tokens({"prompt_tokens": 100}) == 0
    assert usage.cached_tokens(NS(prompt_tokens_details=NS(cached_tokens=64))) == 64
    assert usage.cached_tokens({"prompt_tokens_details": {"cached_tokens": 128}}) == 128
    assert usage.cached_tokens({"prompt_cache_hit_tokens": 32}) == 32          # DeepSeek's own field
    with usage.track(tmp_path, phase="translate"):
        usage.record("m/x", {"prompt_tokens": 1000, "completion_tokens": 50, "cost": 0.001,
                             "prompt_tokens_details": {"cached_tokens": 600}})
        usage.record("m/x", {"prompt_tokens": 500, "completion_tokens": 20, "cost": 0.0005})
    import json

    m = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))["models"]["m/x"]
    assert (m["in"], m["cached"], m["calls"]) == (1500, 600, 2)


def test_hosts_are_counted_per_model_and_absent_without_one(tmp_path):
    import json

    with usage.track(tmp_path, phase="gate"):
        usage.record("m/x", U(10, 5, 0.001), host="Morph")
        usage.record("m/x", U(10, 5, 0.001), host="Morph")
        usage.record("m/x", U(10, 5, 0.001), host="DeepInfra")
        usage.record("m/y", U(10, 5, 0.001))
    m = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))["models"]
    assert m["m/x"]["hosts"] == {"Morph": 2, "DeepInfra": 1}
    assert "hosts" not in m["m/y"]
    assert usage.summary(tmp_path)["calls"] == 4
