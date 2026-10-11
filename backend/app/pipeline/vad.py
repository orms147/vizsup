"""Voice activity (VAD) for "tự canh lời": where in the audio someone is speaking or
singing, as (start, end) spans in seconds.

Energy (ffmpeg silencedetect, the RMS waveform) is useless here — Douyin vlogs and
livestreams have background music that never drops below any silence threshold — so
this runs Silero VAD (MIT, ~2 MB ONNX) through onnxruntime, which is already installed
for RapidOCR. Local only: no audio leaves the machine. The model is downloaded once
into ``<storage>/_models`` (pinned release + sha256) and every result is cached in the
job dir as ``vad.json`` keyed by the source file, model and parameters.

Measured on a 329 s Douyin livestream (01/10/2026 clip): ~1.5 s CPU. Clear speech and
solo singing score 0.6–0.9; singing under loud music often stays below 0.5 and is
NOT reported — snapping then simply leaves those cues alone.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from app.models import Job

MODEL_URL = ("https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/"
             "src/silero_vad/data/silero_vad.onnx")
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
RATE = 16000
FRAME = 512             # samples per model call at 16 kHz (32 ms)
CONTEXT = 64            # samples carried over from the previous frame (Silero v5+)
FRAME_SEC = FRAME / RATE

# get_speech_timestamps defaults, plus MERGE_GAP: pauses shorter than this are inside
# a sentence, so an edge must never snap into them.
THRESHOLD = 0.5
NEG_THRESHOLD = 0.35
MIN_SPEECH = 0.25
MIN_SILENCE = 0.10
PAD = 0.03
MERGE_GAP = 0.25
_PARAMS = f"v1:{THRESHOLD}:{NEG_THRESHOLD}:{MIN_SPEECH}:{MIN_SILENCE}:{PAD}:{MERGE_GAP}"
_CACHE = "vad.json"

Span = tuple[float, float]


# --- pure: probabilities → spans ---------------------------------------------------

def segments(probs: list[float], *, frame_sec: float = FRAME_SEC,
             threshold: float = THRESHOLD, neg_threshold: float = NEG_THRESHOLD,
             min_speech: float = MIN_SPEECH, min_silence: float = MIN_SILENCE,
             pad: float = PAD, merge_gap: float = MERGE_GAP) -> list[Span]:
    """Hysteresis over per-frame speech probabilities (a port of Silero's
    get_speech_timestamps): speech starts at >= threshold, ends after
    ``min_silence`` below ``neg_threshold``; spans shorter than ``min_speech`` are
    dropped, then padded and merged across gaps < ``merge_gap``."""
    spans: list[list[float]] = []
    start = None
    quiet_since = None
    for i, p in enumerate(probs):
        t = i * frame_sec
        if start is None:
            if p >= threshold:
                start, quiet_since = t, None
            continue
        if p < neg_threshold:
            if quiet_since is None:
                quiet_since = t
            if t - quiet_since >= min_silence:
                if quiet_since - start >= min_speech:
                    spans.append([start, quiet_since])
                start = quiet_since = None
        else:
            quiet_since = None
    total = len(probs) * frame_sec
    if start is not None and total - start >= min_speech:
        spans.append([start, total])

    out: list[list[float]] = []
    for a, b in spans:
        a, b = max(0.0, a - pad), min(total, b + pad)
        if out and a - out[-1][1] < merge_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


# --- model --------------------------------------------------------------------------

def model_path() -> Path:
    from app.config import settings

    return Path(settings.vizsup_storage_dir) / "_models" / "silero_vad.onnx"


def model_ready() -> bool:
    return model_path().exists()


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ensure_model(log=None) -> Path:
    """The Silero model, downloading it once (pinned tag, sha256-checked, atomic)."""
    path = model_path()
    if path.exists():
        return path
    import httpx

    if log:
        log("Tải bộ nhận diện giọng nói Silero VAD (một lần, ~2 MB)…")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with httpx.Client(follow_redirects=True, timeout=60) as c:
        r = c.get(MODEL_URL)
        r.raise_for_status()
        tmp.write_bytes(r.content)
    got = _sha256(tmp)
    if got != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Silero VAD tải về sai checksum ({got[:12]}…)")
    tmp.replace(path)
    return path


def _session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(path), sess_options=opts,
                                providers=["CPUExecutionProvider"])
    names = {i.name for i in sess.get_inputs()}
    if names != {"input", "state", "sr"}:
        raise RuntimeError(f"Silero VAD: đầu vào không như mong đợi {sorted(names)}")
    return sess


# --- audio → probabilities ------------------------------------------------------------

def speech_probs(src: Path, model: Path) -> list[float]:
    """Per-32 ms speech probability over the whole file."""
    import numpy as np

    from app.ffmpegutil import ffmpeg_bin

    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(src), "-vn", "-ac", "1",
                          "-ar", str(RATE), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    sess = _session(model)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    ctx = np.zeros((1, CONTEXT), dtype=np.float32)
    sr = np.array(RATE, dtype=np.int64)
    probs: list[float] = []
    for i in range(0, len(x) - FRAME + 1, FRAME):
        frame = np.concatenate([ctx, x[i:i + FRAME][None, :]], axis=1)
        out, state = sess.run(None, {"input": frame, "state": state, "sr": sr})
        probs.append(float(out[0][0]))
        ctx = frame[:, -CONTEXT:]
    return probs


def _source(job: Job) -> Path | None:
    for p in (job.source_audio, job.source_video):
        if p.exists():
            return p
    return None


def _key(src: Path) -> str:
    st = src.stat()
    return f"{src.name}:{st.st_size}:{int(st.st_mtime)}:{MODEL_SHA256[:12]}:{_PARAMS}"


def speech_spans(job: Job, *, log=None) -> list[Span] | None:
    """Cached voice spans for the job's audio. None (never raises) when there is no
    source, the model can't be fetched/loaded, or decoding fails — callers then just
    skip snapping."""
    src = _source(job)
    if src is None:
        return None
    cache = job.work_dir / _CACHE
    key = _key(src)
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("key") == key:
            return [tuple(s) for s in data["spans"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        model = ensure_model(log)
        spans = segments(speech_probs(src, model))
    except Exception as exc:  # noqa: BLE001 - VAD is an aid; never break a run over it
        if log:
            log(f"⚠ Không chạy được nhận diện giọng nói ({type(exc).__name__}: {exc})")
        return None
    try:
        cache.write_text(json.dumps({"key": key, "spans": spans}), encoding="utf-8")
    except OSError:
        pass
    return spans
