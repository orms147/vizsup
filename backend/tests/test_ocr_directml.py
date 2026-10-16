"""OCR on the GPU through DirectML (onnxruntime-directml): picked automatically, and
never with two sessions at once — parallel DirectML runs crashed the process."""
from __future__ import annotations

import sys
import types

from app.pipeline import ocr


def _fake_ort(monkeypatch, providers):
    mod = types.SimpleNamespace(get_available_providers=lambda: providers)
    monkeypatch.setitem(sys.modules, "onnxruntime", mod)


def test_accelerator_prefers_directml_then_cuda(monkeypatch):
    _fake_ort(monkeypatch, ["DmlExecutionProvider", "CPUExecutionProvider"])
    assert ocr.accelerator() == "dml"
    _fake_ort(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])
    assert ocr.accelerator() == "cuda"
    _fake_ort(monkeypatch, ["CPUExecutionProvider"])
    assert ocr.accelerator() == "cpu"


def test_directml_reads_with_one_worker(monkeypatch):
    from app.providers.ocr import rapid

    seen = []

    class Pool:
        def __init__(self, max_workers):
            seen.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def map(self, fn, items):
            return map(fn, items)

    monkeypatch.setattr(rapid, "ThreadPoolExecutor", Pool)
    for accel, want in (("dml", 1), ("cpu", rapid.WORKERS)):
        monkeypatch.setattr(ocr, "accelerator", lambda a=accel: a)
        with rapid.RapidOCRLocal().reader():
            pass
        assert seen[-1] == want
