"""Stage: hardsub detection (the branch point). STUB — implement in P5.

Plan (see docs/research.md): sample frames ~2-5 fps, run a PaddleOCR PP-OCRv5
*detection-only* pass over a bottom-third crop. A text box that recurs in a
stable location across many frames ⇒ hardsubs present → OCR path; otherwise → ASR.
Cropping to the bottom third is the biggest accuracy+speed lever and removes
Douyin watermarks/logos.
"""
from __future__ import annotations

from app.models import Job


def detect_hardsubs(job: Job, *, sample_fps: float = 3.0) -> bool:
    raise NotImplementedError(
        "Hardsub detection not implemented yet (P5). "
        "Plan: PP-OCRv5 detection-only over bottom-third crop across sampled frames."
    )
