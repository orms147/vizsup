"""E-14: what each video cost in AI calls, and how long its runs took.

OpenRouter returns ``usage.cost`` (credits ≈ USD) on every response — chat and
/audio/transcriptions alike (the Whisper word track: ``usage {seconds, cost}``; a vendor
without it is priced per audio minute, see ``record_audio``). The worker opens a
``track(job_dir)`` scope around a run; any AI call made meanwhile — from any thread —
is added to ``<job>/usage.json``. Outside a scope nothing is recorded. Best effort:
accounting must never break a call.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from app.fsutil import atomic_write_text

_FILE = "usage.json"
_lock = threading.Lock()
_sink: Path | None = None          # job dir of the run in progress (one run at a time)


@contextmanager
def track(job_dir: Path, *, phase: str):
    """Record AI calls into ``job_dir`` and the wall time of this ``phase``."""
    global _sink
    prev, _sink = _sink, Path(job_dir)
    t0 = time.monotonic()
    try:
        yield
    finally:
        _add(Path(job_dir), seconds={phase: time.monotonic() - t0})
        _sink = prev


def _getter(usage):
    if isinstance(usage, dict):
        return usage.get
    extra = getattr(usage, "model_extra", None) or {}

    def get(k, d=None):
        v = getattr(usage, k, None)
        return extra.get(k, d) if v is None else v
    return get


def _usage_fields(usage) -> tuple[int, int, float | None]:
    """(input tokens, output tokens, cost) — cost None when the response carried none.
    Chat responses name tokens prompt/completion, transcription ones input/output."""
    if usage is None:
        return 0, 0, None
    get = _getter(usage)
    cost = get("cost", None)
    pin = get("prompt_tokens", None) or get("input_tokens", 0)
    pout = get("completion_tokens", None) or get("output_tokens", 0)
    return int(pin or 0), int(pout or 0), (None if cost is None else float(cost))


def record(model: str, usage) -> None:
    """Add one AI call (its response's ``usage``) to the job being tracked."""
    sink = _sink
    if sink is None:
        return
    try:
        pin, pout, cost = _usage_fields(usage)
        _add(sink, model=model, pin=pin, pout=pout, cost=cost or 0.0)
    except Exception:  # noqa: BLE001 - accounting never breaks a call
        pass


def record_audio(model: str, usage, seconds: float, price_per_min: float) -> None:
    """Add one speech-to-text call (/audio/transcriptions). Its ``usage.cost`` when the
    response has one (OpenRouter's does); otherwise ``seconds`` of audio (``usage.seconds``
    when given) × ``price_per_min`` — these APIs bill per audio second."""
    sink = _sink
    if sink is None:
        return
    try:
        pin, pout, cost = _usage_fields(usage)
        if cost is None:
            heard = _getter(usage)("seconds", None) if usage is not None else None
            cost = float(heard or seconds or 0.0) / 60 * price_per_min
        _add(sink, model=model, pin=pin, pout=pout, cost=cost)
    except Exception:  # noqa: BLE001 - accounting never breaks a call
        pass


def _add(job_dir: Path, *, model: str | None = None, pin: int = 0, pout: int = 0,
         cost: float = 0.0, seconds: dict | None = None) -> None:
    path = job_dir / _FILE
    with _lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if model:
            m = data.setdefault("models", {}).setdefault(model, {"calls": 0, "in": 0, "out": 0,
                                                                 "cost": 0.0})
            m["calls"] += 1
            m["in"] += pin
            m["out"] += pout
            m["cost"] = round(m["cost"] + cost, 6)
        for phase, sec in (seconds or {}).items():
            data.setdefault("seconds", {})[phase] = round(
                data.get("seconds", {}).get(phase, 0.0) + sec, 1)
        try:
            atomic_write_text(path, json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


def summary(job_dir: Path) -> dict:
    """``{"cost": $, "calls": n, "seconds": s}`` for a job (zeros when unknown)."""
    try:
        data = json.loads((Path(job_dir) / _FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cost": 0.0, "calls": 0, "seconds": 0.0}
    models = data.get("models", {}).values()
    return {"cost": round(sum(m.get("cost", 0.0) for m in models), 4),
            "calls": sum(m.get("calls", 0) for m in models),
            "seconds": round(sum(data.get("seconds", {}).values()), 1)}


def label(job_dir: Path) -> str:
    """Short Vietnamese line for the UI, e.g. 'AI $0.012 · 2 phút 05'."""
    s = summary(job_dir)
    parts = []
    if s["calls"]:
        parts.append(f"AI ${s['cost']:.3f}")
    if s["seconds"]:
        m, sec = divmod(int(s["seconds"]), 60)
        parts.append(f"{m} phút {sec:02d}" if m else f"{sec} giây")
    return " · ".join(parts)
