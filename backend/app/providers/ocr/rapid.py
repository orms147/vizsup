"""Local OCR: RapidOCR (ONNX PP-OCR) on this machine. Nothing is uploaded.

Measured 2026-10-14 (CPU, PP-OCRv6): ~0.25-0.5 s per frame, almost all of it text
detection; two workers are ~15 % faster than one, more only contend for the CPU.
On the GPU through DirectML (onnxruntime-directml, 2026-10-16): same output, detection
420 → 80 ms — but ONE worker: two DirectML sessions running at once crashed the process
(segfault inside rapidocr), as the ORT docs warn DirectML does not run in parallel.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from app.providers.base import OCRProvider, _installed

WORKERS = 2          # CPU; DirectML gets 1 (see the docstring)


class RapidOCRLocal(OCRProvider):
    name = "rapidocr"
    display_name = "Trên máy (RapidOCR) — không gửi hình đi đâu"
    cost_note = ("miễn phí · ~0,1–0,2 s mỗi khung trên GPU (DirectML), 0,25–0,5 s trên CPU · "
                 "chữ nghệ thuật / nền rối hay đọc sót")
    local = True
    batch_frames = 50

    def available(self) -> bool:
        return _installed("rapidocr") or _installed("rapidocr_onnxruntime")

    @contextmanager
    def reader(self, *, log=None, cancel=None):
        from app.pipeline import ocr

        engines = threading.local()

        def one(img):
            if not hasattr(engines, "ocr"):
                engines.ocr = ocr._build_ocr()
            return ocr._to_lines(engines.ocr(img))

        workers = 1 if ocr.accelerator() == "dml" else WORKERS
        with ThreadPoolExecutor(max_workers=workers) as ex:
            yield lambda images: list(ex.map(one, images))
