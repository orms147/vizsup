"""ENG-2 eval: did Demucs actually strip the speech? Measures the SPEECH-BAND
(~200-4000 Hz) mean volume of bg.wav vs the source. Acceptance (from
docs/plan/B-engine-quality.md): in a SPEECH section the instrumental drops >= 10 dB;
in a MUSIC-only section it stays within ~3 dB (music preserved). Run each section
separately with --range.

Manual tool — needs a real speech+music clip and bg.wav (run keep-music first).

    python backend/scripts/duck_eval.py --source storage/<id>/source.mp4 \
        --bg storage/<id>/bg.wav --range 12:18
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # make `app` importable

from app.ffmpegutil import ffmpeg_bin   # noqa: E402


def speech_band_db(path: Path, rng: str | None) -> float | None:
    """Mean volume (dB) of the ~200-4000 Hz speech band, via ffmpeg volumedetect."""
    cmd = [ffmpeg_bin(), "-hide_banner"]
    if rng:
        a, b = rng.split(":")
        cmd += ["-ss", a, "-to", b]
    cmd += ["-i", str(path),
            "-af", "bandpass=f=2100:width_type=h:w=3800,volumedetect", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?\d+\.?\d*) dB", res.stderr)
    return float(m.group(1)) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Demucs speech-band drop eval")
    ap.add_argument("--source", required=True)
    ap.add_argument("--bg", required=True, help="the Demucs instrumental (bg.wav)")
    ap.add_argument("--range", default=None, help="SS:TO seconds for one section, e.g. 12:18")
    args = ap.parse_args()

    src = speech_band_db(Path(args.source), args.range)
    bg = speech_band_db(Path(args.bg), args.range)
    if src is None or bg is None:
        print("[duck_eval] could not measure volume (no audio?)", file=sys.stderr)
        return 2
    drop = src - bg
    print(f"speech-band mean: source {src:.1f} dB · bg {bg:.1f} dB · drop {drop:.1f} dB")
    print(f"SPEECH section target: drop >= 10 dB → {'PASS' if drop >= 10 else 'CHECK'}")
    print(f"MUSIC section target:  drop <= 3 dB  → {'PASS' if drop <= 3 else 'CHECK'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
