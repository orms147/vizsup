"""The dub waits out a TTS server that limits us (edge "no audio" nights) instead of failing
the render — an unattended render (autopilot / Telegram) has nobody to click "Dựng lại"."""
from __future__ import annotations

import pytest

from app.pipeline import render
from app.providers.tts.edge import TTSThrottled


def _limited() -> RuntimeError:
    try:
        raise TTSThrottled("no audio")
    except TTSThrottled as cause:
        try:
            raise RuntimeError("Máy chủ giọng đọc đang giới hạn — đã tạo 82/83 câu") from cause
        except RuntimeError as exc:
            return exc


def test_waits_then_finishes_the_dub():
    calls, slept, logs = [], [], []

    def make():
        calls.append(1)
        if len(calls) < 3:
            raise _limited()
        return ["seg"]

    out = render.with_throttle_waits(make, log=logs.append, waits=(0.01, 0.01, 0.01),
                                     sleep=slept.append)
    assert out == ["seg"] and len(calls) == 3
    assert len(logs) == 2 and "tự thử tiếp" in logs[0]


def test_gives_up_after_the_last_wait_and_other_errors_pass_through():
    def always():
        raise _limited()

    with pytest.raises(RuntimeError, match="82/83"):
        render.with_throttle_waits(always, waits=(0.0, 0.0), sleep=lambda _s: None)

    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("giọng không tồn tại")

    with pytest.raises(RuntimeError, match="không tồn tại"):
        render.with_throttle_waits(broken, waits=(0.0,), sleep=lambda _s: None)
    assert len(calls) == 1                                  # not a throttle → no retry


def test_cancel_during_the_wait():
    def always():
        raise _limited()

    with pytest.raises(InterruptedError):
        render.with_throttle_waits(always, waits=(30.0,), cancel=lambda: True,
                                   sleep=lambda _s: None)
