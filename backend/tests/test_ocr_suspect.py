"""N-14 (light): OCR confidence sidecar + suspect-only proofread — the LLM only
sees lines worth fixing (a clean video costs 0 proofread tokens).
"""
from __future__ import annotations

import json

from app.models import Job
from app.pipeline import ocr
from app.pipeline.ocr import _clean_frame_conf, _suspect

CN = ("1\n00:00:00,000 --> 00:00:01,000\n你好世界\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\n有quai字形\n\n"
      "3\n00:00:02,000 --> 00:00:03,000\n再见朋友\n")


class _Recorder:
    can_proofread = True

    def __init__(self):
        self.seen: list[list[str]] = []

    def available(self):
        return True

    def proofread_texts(self, texts, **kw):
        self.seen.append(list(texts))
        return [t + "✔" for t in texts]


def _job(tmp_path, report=None):
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text(CN, encoding="utf-8")
    if report is not None:
        (job.work_dir / "ocr_report.json").write_text(
            json.dumps(report), encoding="utf-8")
    return job


def _wire(monkeypatch, prov):
    import app.providers.registry as registry

    monkeypatch.setattr(registry, "get_translator", lambda name: prov)
    return prov


# --- suspect classifier -----------------------------------------------------

def test_suspect_low_confidence():
    assert _suspect("你好世界", 0.6) is True
    assert _suspect("你好世界", 0.95) is False


def test_suspect_latin_glued_to_cjk():
    assert _suspect("有quai字形", 0.99) is True      # classic misread shape
    assert _suspect("iPhone 15 真好用", 0.99) is False  # latin separated by space is fine


def test_suspect_short_shaky_read():
    assert _suspect("好", 0.85) is True
    assert _suspect("好", 0.95) is False


def test_clean_frame_conf_is_weakest_kept_line():
    text, conf = _clean_frame_conf([("你好", 0.95), ("世界", 0.7), ("@#$", 0.99)], 0.5)
    assert text == "你好 世界"
    assert conf == 0.7                               # weakest KEPT read taints the frame


# --- suspect-only proofread ---------------------------------------------------

def test_only_suspect_lines_sent(tmp_path, monkeypatch):
    prov = _wire(monkeypatch, _Recorder())
    job = _job(tmp_path, report=[
        {"index": 1, "conf": 0.98, "suspect": False},
        {"index": 2, "conf": 0.55, "suspect": True},
        {"index": 3, "conf": 0.97, "suspect": False},
    ])
    done = ocr.proofread_srt(job, provider="fake")
    assert done == 1
    assert prov.seen == [["有quai字形"]]              # only the suspect line hit the API
    text = job.cn_srt.read_text(encoding="utf-8")
    assert "有quai字形✔" in text and "你好世界✔" not in text


def test_no_suspects_costs_zero_tokens(tmp_path, monkeypatch):
    prov = _wire(monkeypatch, _Recorder())
    job = _job(tmp_path, report=[{"index": i, "conf": 0.99, "suspect": False}
                                 for i in (1, 2, 3)])
    logs: list[str] = []
    assert ocr.proofread_srt(job, provider="fake", log=logs.append) == 0
    assert prov.seen == []
    assert any("0 token" in m for m in logs)


def test_missing_report_proofreads_everything(tmp_path, monkeypatch):
    prov = _wire(monkeypatch, _Recorder())     # old job dir: no ocr_report.json
    job = _job(tmp_path, report=None)
    assert ocr.proofread_srt(job, provider="fake") == 3
    assert len(prov.seen[0]) == 3


def test_escape_hatch_only_suspect_false(tmp_path, monkeypatch):
    prov = _wire(monkeypatch, _Recorder())
    job = _job(tmp_path, report=[{"index": 1, "conf": 0.5, "suspect": True},
                                 {"index": 2, "conf": 0.9, "suspect": False},
                                 {"index": 3, "conf": 0.9, "suspect": False}])
    assert ocr.proofread_srt(job, provider="fake", only_suspect=False) == 3
    assert len(prov.seen[0]) == 3
