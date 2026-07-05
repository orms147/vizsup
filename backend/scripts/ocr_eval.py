"""ENG-4 eval: does suspect-only OCR proofread hold quality while cutting LLM cost?

Measures character error rate (via app.qa.srt_text_cer) of the OCR'd cn.srt against
a hand-fixed reference, for three states: raw OCR, suspect-only proofread, and full
proofread. Acceptance target (from docs/plan/B-engine-quality.md): suspect-only CER
within 0.5 pt of full-proofread CER while the LLM-sent line count drops >= 50%.

Manual tool — needs a real translator key (it calls the provider) and a reference
SRT you fixed by hand. Runs on COPIES of cn.srt so it never mutates the job.

    python backend/scripts/ocr_eval.py --workdir storage/<id> \
        --ref samples/refs/<clip>.cn.ref.srt --provider openrouter
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # make `app` importable
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")                    # console may be cp1252

from app.models import Job                                      # noqa: E402
from app.pipeline.ocr import proofread_srt                     # noqa: E402
from app.qa import srt_text_cer                                # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="OCR suspect-only vs full proofread CER eval")
    ap.add_argument("--workdir", required=True, help="job dir with cn.srt + ocr_report.json")
    ap.add_argument("--ref", required=True, help="hand-fixed reference cn.srt")
    ap.add_argument("--provider", default="openrouter", help="translator id (must proofread)")
    args = ap.parse_args()

    work = Path(args.workdir)
    ref = Path(args.ref)
    job = Job(id=work.name, work_dir=work)
    if not job.cn_srt.exists():
        print(f"[ocr_eval] no cn.srt in {work}", file=sys.stderr)
        return 2
    raw = job.cn_srt.read_text(encoding="utf-8")   # the untouched OCR output

    def run(only_suspect: bool) -> tuple[float, int]:
        job.cn_srt.write_text(raw, encoding="utf-8")           # restore before each run
        sent = proofread_srt(job, provider=args.provider, only_suspect=only_suspect,
                             log=lambda m: None)
        return srt_text_cer(job.cn_srt, ref), sent

    raw_cer = srt_text_cer(job.cn_srt, ref)
    cer_suspect, n_suspect = run(only_suspect=True)
    cer_full, n_full = run(only_suspect=False)
    job.cn_srt.write_text(raw, encoding="utf-8")               # leave the job as we found it

    print(f"raw OCR         CER {raw_cer:.4f}")
    print(f"suspect-only    CER {cer_suspect:.4f}   lines sent {n_suspect}")
    print(f"full proofread  CER {cer_full:.4f}   lines sent {n_full}")
    if n_full:
        drop = 100 * (1 - n_suspect / n_full)
        gap = abs(cer_suspect - cer_full)
        ok = gap <= 0.005 and drop >= 50
        print(f"\nCER gap {gap:.4f} ({'<=' if gap <= 0.005 else '>'} 0.5 pt) · "
              f"lines saved {drop:.0f}% · acceptance: {'PASS' if ok else 'CHECK'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
