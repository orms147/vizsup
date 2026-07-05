"""API-7 (the core OCR-path task): when the Chinese text came from OCR, the
translate prompts tolerate OCR noise BY DEFAULT via a private _ocr_source flag,
so the separate double-send proofread pass becomes opt-in. The flag is plumbing —
it must never render as prompt meta text — and a non-OCR re-run of the same job
dir must not inherit a stale ocr_report.json.
"""
from __future__ import annotations

from app.models import Job
from app.pipeline import translate
from app.providers.translation._common import (
    OCR_HINT,
    build_refine_prompt,
    build_user_prompt,
)


def test_builders_include_hint_once_and_never_leak_key():
    ctx = {"title": "T", "_ocr_source": True}
    u = build_user_prompt(["你好"], context=ctx)
    assert u.count(OCR_HINT) == 1 and "_ocr_source" not in u
    r = build_refine_prompt(["中文"], ["nháp"], context=ctx)
    assert r.count(OCR_HINT) == 1 and "_ocr_source" not in r


def test_no_hint_without_flag():
    assert OCR_HINT not in build_user_prompt(["你好"], context={"title": "T"})
    assert OCR_HINT not in build_refine_prompt(["中文"], ["nháp"], context={"title": "T"})


class RecordingProvider:
    def __init__(self):
        self.contexts: list[dict | None] = []

    def available(self):
        return True

    def translate_texts(self, texts, *, context=None, glossary=None, **kw):
        self.contexts.append(context)
        return [f"vi:{t}" for t in texts]


def test_translate_sets_ocr_source_when_report_present(monkeypatch, tmp_path):
    prov = RecordingProvider()
    monkeypatch.setattr(translate, "get_translator", lambda name: prov)
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    (job.work_dir / "ocr_report.json").write_text("{}", encoding="utf-8")
    translate.translate(job, draft="fake", glossary={})
    assert prov.contexts and prov.contexts[0].get("_ocr_source") is True


def test_translate_no_ocr_source_without_report(monkeypatch, tmp_path):
    prov = RecordingProvider()
    monkeypatch.setattr(translate, "get_translator", lambda name: prov)
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    translate.translate(job, draft="fake", glossary={})
    assert prov.contexts and not prov.contexts[0].get("_ocr_source")


def test_clear_ocr_provenance_removes_stale_report(tmp_path):
    """The srt/asr branches call this so an SRT/ASR re-run doesn't claim OCR."""
    job = Job(id="t", work_dir=tmp_path / "job")
    (job.work_dir / "ocr_report.json").write_text("{}", encoding="utf-8")
    translate.clear_ocr_provenance(job)
    assert not (job.work_dir / "ocr_report.json").exists()
    translate.clear_ocr_provenance(job)   # idempotent when already absent (no raise)
