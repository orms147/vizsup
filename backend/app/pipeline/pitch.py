"""Local voice-pitch measurement (YIN, numpy only) — an objective second opinion on the
speakers' gender for E-4. Nothing is uploaded.

Talking voices separate well: men mostly below ~165 Hz, women (and children) above
~185 Hz. Singing and music beds do not (a tenor's chorus sits where a woman talks), so
callers only use the verdict for talking clips.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SR = 16000
FMIN, FMAX = 75.0, 600.0
HOP, WIN = 0.02, 0.05          # 20 ms hop, 50 ms window (≥ 2 periods at FMIN)
CONF = 0.1                     # YIN aperiodicity threshold: lower = clearly voiced
LOW_HZ, HIGH_HZ = 165.0, 185.0
SHARE = 0.7                    # this share of voiced frames on one side → a verdict
MIN_FRAMES = 50                # ~1 s of clear voice before saying anything


def f0_frames(x: np.ndarray, sr: int = SR, conf: float = CONF) -> np.ndarray:
    """F0 (Hz) of the voiced frames of mono float audio. Vectorized YIN; ``conf`` is the
    aperiodicity threshold (CONF = clearly voiced; a looser 0.2 keeps far more frames of
    reverberant / dubbed speech and, being less strict, also finds the true period instead
    of its half)."""
    win, hop = int(WIN * sr), int(HOP * sr)
    tmax, tmin = int(sr / FMIN), int(sr / FMAX)
    w = win - tmax
    if len(x) < win or w <= 0:
        return np.zeros(0)
    idx = np.arange(0, len(x) - win, hop)[:, None] + np.arange(win)[None, :]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    loud = np.sqrt((fr ** 2).mean(axis=1)) > 0.01
    fr = fr[loud]
    if not len(fr):
        return np.zeros(0)
    n = 1 << int(np.ceil(np.log2(win + w)))
    r = np.fft.irfft(np.fft.rfft(fr, n) * np.conj(np.fft.rfft(fr[:, :w], n)), n)[:, :tmax + 1]
    sq = np.concatenate([np.zeros((len(fr), 1)), np.cumsum(fr ** 2, axis=1)], axis=1)
    taus = np.arange(tmax + 1)
    e_tau = sq[:, taus + w] - sq[:, taus]                      # energy of x[tau : tau+w]
    d = sq[:, [w]] + e_tau - 2 * r                             # YIN difference function
    d[:, 0] = 0.0
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] * taus[1:] / np.maximum(np.cumsum(d[:, 1:], axis=1), 1e-12)
    out = []
    for row in cmnd:
        below = np.nonzero(row[tmin:tmax] < conf)[0]
        if not len(below):
            continue
        t = tmin + below[0]
        while t + 1 < tmax and row[t + 1] < row[t]:            # walk to the local minimum
            t += 1
        a, b, c = row[t - 1], row[t], row[t + 1]
        den = a - 2 * b + c
        t_f = t + (0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0)
        f = sr / t_f
        if FMIN <= f <= FMAX:
            out.append(f)
    return np.asarray(out)


def verdict(f0: np.ndarray) -> dict | None:
    """{'median', 'low', 'high', 'voice'} where voice is 'low' (usually a man), 'high'
    (a woman or a child) or None when mixed / too little voice. Pure."""
    if len(f0) < MIN_FRAMES:
        return None
    low = float((f0 < LOW_HZ).mean())
    high = float((f0 > HIGH_HZ).mean())
    voice = "low" if low >= SHARE else "high" if high >= SHARE else None
    return {"median": round(float(np.median(f0))), "low": round(low, 2), "high": round(high, 2),
            "voice": voice}


def measure(src: Path, a: float, b: float,
            spans: list[tuple[float, float]] | None = None) -> dict | None:
    """Pitch verdict for [a, b] of ``src`` (only inside voice spans when given)."""
    from app.ffmpegutil import ffmpeg_bin

    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}",
                          "-i", str(src), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if spans:
        keep = np.zeros(len(x), dtype=bool)
        for s, e in spans:
            i, j = int(max(0.0, s - a) * SR), int(max(0.0, min(e, b) - a) * SR)
            if j > i:
                keep[i:j] = True
        if keep.any():
            x = x[keep]
    return verdict(f0_frames(x))
