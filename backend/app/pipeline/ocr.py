"""Stage: OCR (hardcoded-subtitle extraction). STUB — implement in P5.

Plan (see docs/research.md): VideOCR / video-subtitle-extractor on a bottom-third
crop with PaddleOCR PP-OCRv5, SSIM frame dedup + edit-distance line merge → CN SRT
(written to job.cn_srt). Keep Google-Lens hybrid mode opt-in (it uploads frames).
Add an optional LLM proofread pass for stylized/colored-outline subs.
"""
from __future__ import annotations

from app.models import Job


def extract_hardsubs(job: Job, *, crop: str = "bottom-third", proofread: bool = False) -> Job:
    raise NotImplementedError(
        "Hardsub OCR not implemented yet (P5). "
        "Plan: PP-OCRv5 + SSIM dedup → job.cn_srt; optional LLM proofread."
    )
