"""F-5: what a video will cost and how long it will take — shown BEFORE Start.

Competing tools are criticised above all for surprise charges (docs/plan/F-roadmap.md).
The estimate is a range, because the number of subtitle lines is only known after the
Chinese subtitles exist (a song has ~8 lines a minute, a talking vlog ~28).

Base rates come from two real runs with the default role models on 2026-10-14 (copies of
a 137 s vlog: 63 lines, $0.045, 87 s; a 329 s song: 42 lines, $0.043, 114 s): the cost is
mostly a fixed context brief plus a per-line refine pass (Kimi K3 output tokens), not the
audio. Once a few projects have a usage.json, their own per-minute figures take over.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

LINES_PER_MIN = (8.0, 28.0)        # song … talking vlog (measured 2026-10-14)

# USD, default role models (app/providers/roles.py), measured / derived 2026-10-14
BRIEF = (0.010, 0.020)             # context brief (Kimi K3) + the ≤60 s listen (Qwen-Omni)
REFINE_PER_LINE = (0.0003, 0.0006)  # Kimi K3 refine
DRAFT_PER_LINE = (0.00002, 0.00006)  # DeepSeek V4.1 Flash
QA_PER_LINE = (0.00001, 0.00002)   # Qwen 3.8 Flash review
ASR_PER_MIN = (0.0002, 0.006)      # Qwen-Omni transcript (~$0.0002) + Whisper word track
#                                    (not itemised by OpenRouter; up to OpenAI's list $0.006)
OCR_CLOUD_PER_MIN = (0.0007, 0.0012)  # qwen3.7-flash tiles: 137 s → $0.002

# seconds of processing (download not included); together they reproduce the two runs
# (vlog 2.3 min → 87 s, song 5.5 min → 114 s) inside the range
SEC_FIXED = (5.0, 15.0)
ASR_SEC_PER_MIN = (6.0, 18.0)
TRANSLATE_SEC_PER_MIN = (10.0, 22.0)   # draft + refine + AI review, batches in parallel
BRIEF_SEC = (8.0, 20.0)
OCR_LOCAL_SEC_PER_MIN = (30.0, 60.0)   # 2 fps × 0.25-0.5 s per frame (CPU)
OCR_GPU_SEC_PER_MIN = (10.0, 20.0)     # DirectML, 2026-10-16: 137 s in 23 s, 148 s in 45 s
OCR_CLOUD_SEC_PER_MIN = (8.0, 15.0)    # 137 s video in 22 s


@dataclass
class Estimate:
    minutes: float
    cost: tuple[float, float]
    seconds: tuple[float, float]
    lines: tuple[int, int]
    basis: str                       # "đo 14/10" or "từ N dự án trước"
    notes: list[str] = field(default_factory=list)

    def label(self) -> str:
        lo, hi = self.cost
        money = "AI miễn phí" if hi < 0.0005 else f"AI ~{_usd(lo)}–{_usd(hi)}"
        return (f"≈ {_dur(self.minutes * 60)} video · {money} · "
                f"{_span(*self.seconds)} xử lý (chưa gồm tải)")


def _usd(x: float) -> str:
    return f"${x:.3f}" if x < 0.1 else f"${x:.2f}"


def _dur(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _span(lo: float, hi: float) -> str:
    def m(x: float) -> str:
        return f"{max(1, round(x / 60))} phút" if x >= 90 else f"{max(10, round(x / 10) * 10)} giây"
    a, b = m(lo), m(hi)
    return a if a == b else f"{a.split()[0]}–{b}" if a.split()[1:] == b.split()[1:] else f"{a}–{b}"


def _local_ocr_speed() -> tuple[float, float]:
    try:
        from app.pipeline.ocr import accelerator

        return OCR_GPU_SEC_PER_MIN if accelerator() != "cpu" else OCR_LOCAL_SEC_PER_MIN
    except Exception:  # noqa: BLE001
        return OCR_LOCAL_SEC_PER_MIN


def estimate(duration_s: float, opts: dict, *, history: list[dict] | None = None) -> Estimate:
    """Cost and processing time up to the review step, for these start-screen options."""
    minutes = max(duration_s, 1.0) / 60
    lines = (round(minutes * LINES_PER_MIN[0]), round(minutes * LINES_PER_MIN[1]))
    src = opts.get("subtitle_source") or "auto"
    translator = opts.get("translator") or "passthrough"
    cloud_ocr = (opts.get("ocr_engine") or "rapidocr") != "rapidocr"
    notes: list[str] = []

    lo = hi = 0.0

    def add(rate: tuple[float, float], n_lo: float, n_hi: float | None = None) -> None:
        nonlocal lo, hi
        lo += rate[0] * n_lo
        hi += rate[1] * (n_lo if n_hi is None else n_hi)

    if src in ("asr", "auto"):
        add(ASR_PER_MIN, minutes)
    if src in ("ocr", "auto") and cloud_ocr:
        add(OCR_CLOUD_PER_MIN, minutes)
    if translator != "passthrough":
        add(DRAFT_PER_LINE, *lines)
        if opts.get("refine"):
            add(REFINE_PER_LINE, *lines)
        if opts.get("ai_review", True):
            add(QA_PER_LINE, *lines)
        if opts.get("auto_context") or (opts.get("context_note") or "").strip():
            add(BRIEF, 1)
        if translator != "openrouter":
            notes.append("Ước tính theo model mặc định của OpenRouter — nhà cung cấp khác giá khác.")
    else:
        notes.append("Không dịch (passthrough) — chỉ tốn phần nhận dạng.")

    secs = SEC_FIXED
    if src in ("asr", "auto"):
        secs = (secs[0] + ASR_SEC_PER_MIN[0] * minutes, secs[1] + ASR_SEC_PER_MIN[1] * minutes)
    if translator != "passthrough":
        secs = (secs[0] + TRANSLATE_SEC_PER_MIN[0] * minutes,
                secs[1] + TRANSLATE_SEC_PER_MIN[1] * minutes)
        if opts.get("auto_context") or (opts.get("context_note") or "").strip():
            secs = (secs[0] + BRIEF_SEC[0], secs[1] + BRIEF_SEC[1])
    if src in ("ocr", "auto"):
        per = OCR_CLOUD_SEC_PER_MIN if cloud_ocr else _local_ocr_speed()
        if opts.get("ocr_fast"):                       # 1 frame/s instead of 2
            per = (per[0] / 2, per[1] / 2)
        if src == "ocr":
            secs = (secs[0] + per[0] * minutes, secs[1] + per[1] * minutes)
        else:                                          # auto: OCR only if hardsubs are found
            secs = (secs[0], secs[1] + per[1] * minutes)
    cost = (lo, hi)
    basis = "đo 14/10"
    learned = _from_history(history, minutes) if translator != "passthrough" else None
    if learned is not None:
        cost, secs, n = learned
        basis = f"từ {n} dự án trước"
    if minutes > 45 and opts.get("split_long", True):
        from app.pipeline import split

        notes.append(f"Video dài — sẽ chia {split.part_count(duration_s)} phần ~30 phút.")
    return Estimate(minutes, (round(cost[0], 4), round(cost[1], 4)),
                    (round(secs[0]), round(secs[1])), lines, basis, notes)


def _from_history(history: list[dict] | None, minutes: float):
    """(cost range, seconds range, n) scaled from past projects' own per-minute figures —
    only with ≥ 3 usable projects; the middle 80 % of them gives the range."""
    rows = [h for h in history or [] if h.get("minutes", 0) > 0.2 and h.get("seconds")]
    if len(rows) < 3:
        return None
    cpm = sorted(h["cost"] / h["minutes"] for h in rows)
    spm = sorted(h["seconds"] / h["minutes"] for h in rows)

    def band(xs):
        if len(xs) < 5:
            return min(xs), max(xs)
        q = statistics.quantiles(xs, n=10)
        return q[0], q[-1]

    c, s = band(cpm), band(spm)
    return ((c[0] * minutes, c[1] * minutes), (s[0] * minutes, s[1] * minutes), len(rows))


def history(storage: Path, limit: int = 30) -> list[dict]:
    """Per-project {minutes, cost, seconds} of the gate phase, newest first."""
    out = []
    for u in sorted(storage.glob("*/usage.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(u.read_text(encoding="utf-8"))
            meta = json.loads((u.parent / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dur = float(meta.get("duration") or 0)
        gate = float((data.get("seconds") or {}).get("gate") or 0)
        cost = sum(float(m.get("cost") or 0) for m in (data.get("models") or {}).values())
        if dur > 0 and gate > 0:
            out.append({"minutes": dur / 60, "cost": cost, "seconds": gate})
        if len(out) >= limit:
            break
    return out
