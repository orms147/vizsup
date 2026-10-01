"""ASR eval: which engine transcribes Mandarin best, with the tightest timing?

Reports, per engine, CER of the transcript (punctuation/whitespace ignored) and
|Δstart|/|Δend| of the cue times (app.qa.timing_stats) against a reference SRT.
Originally ENG-3 (acceptance, docs/plan/B: the fused provider's median |Δstart| <=
FunASR-only AND <= 200 ms); extended for the cloud engines so you can pick a model
on YOUR audio — a clean TTS clip barely separates them, real Douyin audio does.

A provider may carry a model, to compare several models of one cloud engine:
    audio_llm:<model>   → AUDIO_LLM_MODEL for that run
    cloud_stt:<model>   → STT_API_MODEL for that run

Manual tool. Cloud engines UPLOAD the audio and cost a little; local ones need
their extras (.[asr], .[asr-accurate], .[asr-align]). References:
samples/refs/<clip>.cn.ref.srt — hand-labeled, or make_fixtures.py --only zh_speech.

    python backend/scripts/asr_timing_eval.py --media samples/zh_speech.wav \\
        --ref samples/refs/zh_speech.cn.ref.srt \\
        --providers faster_whisper cloud_stt audio_llm:qwen/qwen3.8-omni-flash \\
                    audio_llm:xiaomi/mimo-v2.6-flash
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # make `app` importable
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings               # noqa: E402
from app.pipeline.srt import parse_srt        # noqa: E402
from app.providers.registry import get_asr    # noqa: E402
from app.qa import cer, timing_stats          # noqa: E402

_PUNCT = re.compile(r"[\s，。！？、：；·…,.!?;:\"'“”‘’()（）]+")
_MODEL_SETTING = {"audio_llm": "audio_llm_model", "cloud_stt": "stt_api_model"}


def _text(cues) -> str:
    return _PUNCT.sub("", "".join(c.text for c in cues))


def main() -> int:
    ap = argparse.ArgumentParser(description="ASR accuracy + timing eval")
    ap.add_argument("--media", required=True)
    ap.add_argument("--ref", required=True, help="reference cn.srt (~20 cues)")
    ap.add_argument("--providers", nargs="+",
                    default=["faster_whisper", "whisperx_funasr", "funasr_aligned"],
                    help="provider ids; cloud ones may be `audio_llm:<model>` / `cloud_stt:<model>`")
    args = ap.parse_args()

    ref = parse_srt(Path(args.ref))
    ref_text = _text(ref)
    print(f"reference: {len(ref)} cues, {len(ref_text)} chars\n")
    results: dict[str, dict] = {}
    for spec in args.providers:
        name, _, model = spec.partition(":")
        if model:
            if name not in _MODEL_SETTING:
                print(f"{spec:44s} only {sorted(_MODEL_SETTING)} take a :model — skipped")
                continue
            setattr(settings, _MODEL_SETTING[name], model)
        prov = get_asr(name)
        if not prov.available():
            print(f"{spec:44s} unavailable (missing dep or key) — skipped")
            continue
        t0 = time.time()
        try:
            cues = prov.transcribe(Path(args.media))
        except Exception as exc:  # noqa: BLE001 - one engine failing must not end the run
            print(f"{spec:44s} FAILED {type(exc).__name__}: {str(exc)[:120]}")
            continue
        finally:
            prov.unload()
        dt = time.time() - t0
        st = timing_stats(cues, ref)
        results[name] = st
        ds, de = st["d_start"], st["d_end"]
        print(f"{spec:44s} {dt:6.1f}s · CER {cer(_text(cues), ref_text):.3f} · "
              f"matched {st['matched']}/{st['ref_total']} · "
              f"|Δstart| median {ds['median']}s p95 {ds['p95']}s · |Δend| median {de['median']}s")

    fused, base = results.get("funasr_aligned"), results.get("whisperx_funasr")
    if fused and base:
        fm, bm = fused["d_start"]["median"], base["d_start"]["median"]
        ok = fm is not None and bm is not None and fm <= bm and fm <= 0.2
        print(f"\nfused |Δstart| median {fm}s vs FunASR-only {bm}s · "
              f"acceptance (<= baseline AND <= 200 ms): {'PASS' if ok else 'CHECK'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
