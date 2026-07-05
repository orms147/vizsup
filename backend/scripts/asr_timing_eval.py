"""ENG-3 eval: which ASR gives the tightest word timing? Compares faster_whisper,
whisperx_funasr (FunASR-only), and funasr_aligned (fused) via app.qa.timing_stats
against a hand-labeled reference SRT (~20 cues). Acceptance (docs/plan/B): the fused
provider's median |Δstart| <= FunASR-only AND <= 200 ms.

Manual tool — needs the ASR extras installed (.[asr], .[asr-accurate], optionally
.[asr-align]) and a reference clip you hand-labeled (samples/refs/<clip>.cn.ref.srt).

    python backend/scripts/asr_timing_eval.py --media samples/<clip>.mp4 \
        --ref samples/refs/<clip>.cn.ref.srt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # make `app` importable
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.pipeline.srt import parse_srt        # noqa: E402
from app.providers.registry import get_asr    # noqa: E402
from app.qa import timing_stats               # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="ASR word-timing accuracy eval")
    ap.add_argument("--media", required=True)
    ap.add_argument("--ref", required=True, help="hand-labeled reference cn.srt (~20 cues)")
    ap.add_argument("--providers", nargs="+",
                    default=["faster_whisper", "whisperx_funasr", "funasr_aligned"])
    args = ap.parse_args()

    ref = parse_srt(Path(args.ref))
    print(f"reference: {len(ref)} cues\n")
    results: dict[str, dict] = {}
    for name in args.providers:
        prov = get_asr(name)
        if not prov.available():
            print(f"{name:16s} unavailable (missing dep) — skipped")
            continue
        cues = prov.transcribe(Path(args.media))
        prov.unload()
        st = timing_stats(cues, ref)
        results[name] = st
        ds, de = st["d_start"], st["d_end"]
        print(f"{name:16s} matched {st['matched']}/{st['ref_total']} · "
              f"|Δstart| median {ds['median']}s p95 {ds['p95']}s · "
              f"|Δend| median {de['median']}s")

    fused, base = results.get("funasr_aligned"), results.get("whisperx_funasr")
    if fused and base:
        fm, bm = fused["d_start"]["median"], base["d_start"]["median"]
        ok = fm is not None and bm is not None and fm <= bm and fm <= 0.2
        print(f"\nfused |Δstart| median {fm}s vs FunASR-only {bm}s · "
              f"acceptance (<= baseline AND <= 200 ms): {'PASS' if ok else 'CHECK'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
