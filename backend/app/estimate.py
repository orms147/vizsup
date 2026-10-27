"""F-5: what a video will cost and how long it will take — shown BEFORE Start.

Competing tools are criticised above all for surprise charges (docs/plan/F-roadmap.md).
The estimate is a range, because the number of subtitle lines is only known after the
Chinese subtitles exist (a song has ~8 lines a minute, a talking vlog ~28).

Cost = per-line passes priced by the model each role uses NOW (the Settings package +
the user's own picks: ``LINE_COST`` / ``BRIEF_COST``, measured per model) + the audio.
With Kimi K3 as refiner (runs of 2026-10-14: a 137 s vlog, 63 lines, $0.045; a 329 s
song, $0.043; 2026-10-26: 27:42, $0.285) the refine pass was ~86% of a run; with the
2026-10-27 default package the cloud ASR is the biggest part (~$0.0016 / minute in all).
A model nobody measured gets a wide range and a note.

Time: those two short runs were first fitted with per-minute rates only, which charged
their fixed latency (first request of every stage, brief, VAD) to every minute — a 27:42
tech review was then shown "8–19 phút" and reached the edit gate in 287 s (2026-10-26:
ASR 99 s, brief 19 s, draft + refine + AI review ~3 min, cloud ASR, RTX 4070 laptop).
The model is now a fixed part plus per-minute rates; all three runs land inside it.

Past projects calibrate it: the median of (their real figure ÷ what this model says for
their length and options) scales the range — a ratio, not a raw per-minute rate, so
short clips (fixed latency) don't inflate the estimate for long ones. Only runs that
reached the edit gate count.
"""
from __future__ import annotations

import json
import statistics
import wave
from dataclasses import dataclass, field
from pathlib import Path

LINES_PER_MIN = (8.0, 28.0)        # song … talking vlog (measured 2026-10-14; 27/min 2026-10-26)

# USD per subtitle line for ONE translate pass (draft, or refine) by the role's model —
# incl. its share of the pre-gate auto-shorten. Measured, not list prices: 2026-10-27 cost
# pass (120 lines of job 703e…, docs/plan/G-cost-log.md) and the 2026-10-26 full run (Kimi
# K3: $0.248 / 756 lines; today's list price, 20 $/M out, would make it ~$0.0006).
LINE_COST: dict[str, tuple[float, float]] = {
    "deepseek/deepseek-v4.1-flash": (0.000012, 0.000020),       # 120 lines $0.0015; run $0.0135/756
    "openai/gpt-6-luna": (0.000012, 0.000020),                  # draft $0.0019, refine $0.0018 /120
    "deepseek/deepseek-v4.1-flash-1017": (0.000018, 0.000030),  # refine $0.0026 / 120
    "qwen/qwen3.8-flash": (0.00001, 0.00002),                   # AI review: $0.0058 / 756 pairs
    "moonshotai/kimi-k3": (0.0003, 0.0006),
}
UNKNOWN_LINE_COST = (0.00002, 0.0006)   # a model nobody measured: cheap … Kimi-level
# USD per context brief (one call, ~5k tokens of transcript in, ~0.3–0.7k out) by model
BRIEF_COST: dict[str, tuple[float, float]] = {
    "deepseek/deepseek-v4.1-flash-1017": (0.0005, 0.0015),      # $0.0007 measured
    "deepseek/deepseek-v4.1-flash": (0.0004, 0.0012),           # $0.0005
    "openai/gpt-6-luna": (0.0005, 0.0015),                      # $0.0008
    "qwen/qwen3.8-flash": (0.0005, 0.0015),
    "moonshotai/kimi-k3": (0.009, 0.019),
}
UNKNOWN_BRIEF_COST = (0.0005, 0.02)
LISTEN = (0.0002, 0.001)           # the ≤60 s "who is speaking" listen (Qwen-Omni)
ASR_PER_MIN = (0.0005, 0.0015)     # Qwen-Omni transcript (~$0.0002, 2026-10-26: $0.0048 for
#                                    27.7 min) + Whisper word track ($0.00045 on OpenRouter)
OCR_CLOUD_PER_MIN = (0.0007, 0.0012)  # qwen3.7-flash tiles: 137 s → $0.002

# seconds of processing (download not included). Runs reproduced inside the range:
# vlog 2.3 min → 87 s, song 5.5 min → 114 s (2026-10-14), review 27.7 min → 287 s (10-26)
SEC_FIXED = (5.0, 15.0)                # extract audio, VAD + snap, waveform
ASR_SEC_FIXED = (10.0, 25.0)           # the first wave of chunk uploads
ASR_SEC_PER_MIN = (2.5, 5.0)           # cloud: 27.7 min in 99 s (7 chunks, 3 in parallel)
TRANSLATE_SEC_FIXED = (10.0, 20.0)     # the first batch of each pass
TRANSLATE_SEC_PER_MIN = (4.0, 8.0)     # draft + refine + AI review: 756 lines in ~180 s
BRIEF_SEC = (10.0, 30.0)               # 19 s measured
DETECT_SEC = (3.0, 10.0)               # auto: hardsub check on ~16 frames
OCR_LOCAL_SEC_PER_MIN = (30.0, 60.0)   # 2 fps × 0.25-0.5 s per frame (CPU)
OCR_GPU_SEC_PER_MIN = (10.0, 20.0)     # DirectML, 2026-10-16: 137 s in 23 s, 148 s in 45 s
OCR_CLOUD_SEC_PER_MIN = (8.0, 15.0)    # 137 s video in 22 s
STATIC_BASIS = "đo 26/10"
MIN_HISTORY = 3                        # past projects needed before they calibrate
RATIO_CLAMP = (0.25, 4.0)              # one odd history can't make the estimate absurd


@dataclass
class Estimate:
    minutes: float
    cost: tuple[float, float]
    seconds: tuple[float, float]
    lines: tuple[int, int]
    basis: str                       # "đo 26/10" or "từ N dự án trước"
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


Range = tuple[float, float]


def _plus(a: Range, b: Range, k: float = 1.0) -> Range:
    return a[0] + b[0] * k, a[1] + b[1] * k


def _either(a: Range, b: Range) -> Range:
    """One of two paths will run (auto source): the faster one's low, the slower one's high."""
    return min(a[0], b[0]), max(a[1], b[1])


def _role_models() -> dict[str, str]:
    """The OpenRouter model each priced role uses right now (package + the user's picks)."""
    from app.providers import roles

    return {r: roles.model_for(r) for r in ("draft", "refine", "brief", "qa")}


def _line_cost(model: str, notes: list[str]) -> Range:
    if model in LINE_COST:
        return LINE_COST[model]
    notes.append(f"Chưa đo giá model {model} — ước tính rộng (rẻ … ngang Kimi K3).")
    return UNKNOWN_LINE_COST


def _model(minutes: float, opts: dict) -> tuple[Range, Range, list[str]]:
    """(cost, seconds, notes) of the static model for these start-screen options, priced
    with the role models chosen now (Settings → AI và mô hình)."""
    lines = (minutes * LINES_PER_MIN[0], minutes * LINES_PER_MIN[1])
    src = opts.get("subtitle_source") or "auto"
    translator = opts.get("translator") or "passthrough"
    cloud_ocr = (opts.get("ocr_engine") or "rapidocr") != "rapidocr"
    brief = bool(opts.get("auto_context") or (opts.get("context_note") or "").strip())
    notes: list[str] = []

    # the Chinese subtitles: ASR, OCR, or (auto) whichever the hardsub check picks
    asr_cost = _plus((0.0, 0.0), ASR_PER_MIN, minutes)
    asr_sec = _plus(ASR_SEC_FIXED, ASR_SEC_PER_MIN, minutes)
    per = OCR_CLOUD_SEC_PER_MIN if cloud_ocr else _local_ocr_speed()
    if opts.get("ocr_fast"):                       # 1 frame/s instead of 2
        per = (per[0] / 2, per[1] / 2)
    ocr_cost = _plus((0.0, 0.0), OCR_CLOUD_PER_MIN, minutes) if cloud_ocr else (0.0, 0.0)
    ocr_sec = _plus((0.0, 0.0), per, minutes)
    if src == "asr":
        cost, secs = asr_cost, _plus(SEC_FIXED, asr_sec)
    elif src == "ocr":
        cost, secs = ocr_cost, _plus(SEC_FIXED, ocr_sec)
    elif src == "auto":
        cost, secs = _either(asr_cost, ocr_cost), _plus(_plus(SEC_FIXED, DETECT_SEC),
                                                        _either(asr_sec, ocr_sec))
        if ocr_sec[1] > asr_sec[1]:
            notes.append("Tự động: nếu video có phụ đề cứng sẽ chạy OCR (lâu hơn) — "
                         "mức cao của ước tính là trường hợp đó.")
    else:                                          # a provided .srt
        cost, secs = (0.0, 0.0), SEC_FIXED

    if translator != "passthrough":
        models = _role_models()
        priced: list[str] = []          # each unmeasured model is noted once
        per_line = [_line_cost(models["draft"], priced)]
        if opts.get("refine"):
            per_line.append(_line_cost(models["refine"], priced))
        if opts.get("ai_review", True):
            per_line.append(_line_cost(models["qa"], priced))
        for lo, hi in per_line:
            cost = (cost[0] + lo * lines[0], cost[1] + hi * lines[1])
        secs = _plus(_plus(secs, TRANSLATE_SEC_FIXED), TRANSLATE_SEC_PER_MIN, minutes)
        if brief:
            b = BRIEF_COST.get(models["brief"])
            if b is None:
                b = UNKNOWN_BRIEF_COST
                priced.append(f"Chưa đo giá model {models['brief']} — ước tính rộng.")
            cost, secs = _plus(_plus(cost, b), LISTEN), _plus(secs, BRIEF_SEC)
        notes.extend(dict.fromkeys(priced))
        if translator != "openrouter":
            notes.append("Ước tính theo model mặc định của OpenRouter — nhà cung cấp khác giá khác.")
    else:
        notes.append("Không dịch (passthrough) — chỉ tốn phần nhận dạng.")
    return cost, secs, notes


def estimate(duration_s: float, opts: dict, *, history: list[dict] | None = None) -> Estimate:
    """Cost and processing time up to the review step, for these start-screen options."""
    minutes = max(duration_s, 1.0) / 60
    lines = (round(minutes * LINES_PER_MIN[0]), round(minutes * LINES_PER_MIN[1]))
    cost, secs, notes = _model(minutes, opts)
    basis = STATIC_BASIS
    translator = opts.get("translator") or "passthrough"
    if translator == "openrouter":
        from app.providers import presets

        own = " + model bạn tự chọn" if presets.is_custom() else ""
        notes.insert(0, f"Gói mô hình: {presets.active().label}{own} (Cài đặt → AI và mô hình).")
    learned = _from_history(history, opts) if translator != "passthrough" else None
    if learned is not None:
        kc, ks, n = learned
        cost, secs = (cost[0] * kc, cost[1] * kc), (secs[0] * ks, secs[1] * ks)
        basis = f"từ {n} dự án trước"
    if minutes > 45 and opts.get("split_long", True):
        from app.pipeline import split

        notes.append(f"Video dài — sẽ chia {split.part_count(duration_s)} phần ~30 phút.")
    return Estimate(minutes, (round(cost[0], 4), round(cost[1], 4)),
                    (round(secs[0]), round(secs[1])), lines, basis, notes)


def _from_history(history: list[dict] | None, opts: dict):
    """(cost factor, time factor, n) from past projects — the median of real ÷ modelled,
    each project modelled with its own length and options (else the current ones). None
    below MIN_HISTORY usable projects."""
    rows = [h for h in history or [] if h.get("minutes", 0) > 0.2 and h.get("seconds")]
    if len(rows) < MIN_HISTORY:
        return None

    def mid(r: Range) -> float:
        return (r[0] + r[1]) / 2

    current = set(_role_models().values())
    kc, ks = [], []
    for h in rows:
        cost, secs, _notes = _model(h["minutes"], h.get("opts") or opts)
        # a project paid with OTHER priced models (Kimi K3 before the 2026-10-27 packages)
        # says nothing about what the current ones cost — its time still calibrates
        used = {m for m in h.get("models") or () if m in LINE_COST or m in BRIEF_COST}
        if mid(cost) > 0 and h.get("cost", 0) > 0 and used <= current:
            kc.append(h["cost"] / mid(cost))
        ks.append(h["seconds"] / mid(secs))

    def factor(xs: list[float]) -> float:
        if not xs:
            return 1.0
        return min(max(statistics.median(xs), RATIO_CLAMP[0]), RATIO_CLAMP[1])

    return factor(kc), factor(ks), len(rows)


def _duration(job_dir: Path, meta: dict) -> float:
    """Video length: metadata.json, else the extracted source.wav's header (a local file's
    metadata has no duration). Header reads only — this runs on the UI thread."""
    dur = float(meta.get("duration") or 0)
    if dur > 0:
        return dur
    try:
        with wave.open(str(job_dir / "source.wav"), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return 0.0


def _opts(job_dir: Path) -> dict:
    """The options the project ran with (job.json), ``subtitle_source`` resolved to the
    path auto actually took — an OCR run leaves ocr_report.json."""
    try:
        opts = dict(json.loads((job_dir / "job.json").read_text(encoding="utf-8")).get("opts") or {})
    except (OSError, ValueError, AttributeError):
        return {}
    if (opts.get("subtitle_source") or "auto") == "auto":
        opts["subtitle_source"] = "ocr" if (job_dir / "ocr_report.json").exists() else "asr"
    return opts


def history(storage: Path, limit: int = 30) -> list[dict]:
    """Per-project {minutes, cost, seconds, opts} of the gate phase, newest first — only
    projects that reached the edit gate (a vi.srt): a failed or cancelled run's partial
    time and cost are not what a run costs."""
    out = []
    for u in sorted(storage.glob("*/usage.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        d = u.parent
        if not (d / "vi.srt").exists():
            continue
        try:
            data = json.loads(u.read_text(encoding="utf-8"))
            meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dur = _duration(d, meta if isinstance(meta, dict) else {})
        gate = float((data.get("seconds") or {}).get("gate") or 0)
        used = data.get("models") or {}
        cost = sum(float(m.get("cost") or 0) for m in used.values())
        if dur > 0 and gate > 0:
            out.append({"minutes": dur / 60, "cost": cost, "seconds": gate, "opts": _opts(d),
                        "models": sorted(used)})
        if len(out) >= limit:
            break
    return out
