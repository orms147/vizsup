"""ENG-1: auto term-extraction pre-pass + glossary compliance check.
Extraction is a tolerant `zh => vi` parse over a reused draft translator; the
compliance check is pure (flags lines whose glossary target is missing from the
VI); suggestions merge at the LOWEST precedence so the user's glossary always wins.
"""
from __future__ import annotations

import json

from app.models import Cue, Job
from app.pipeline import terms, translate


def _cues(pairs):
    return [Cue(index=i + 1, start=float(i), end=i + 0.9, text=t) for i, t in enumerate(pairs)]


# --- parse_terms ------------------------------------------------------------

def test_parse_terms_tolerant():
    reply = ("这是一些说明\n"
             "小明 => Tiểu Minh\n"
             "- 华为 => Huawei\n"
             "1. 北京 => Bắc Kinh\n"
             "garbage line without arrow\n")
    assert terms.parse_terms(reply) == {
        "小明": "Tiểu Minh", "华为": "Huawei", "北京": "Bắc Kinh"}


def test_parse_terms_empty_and_malformed():
    assert terms.parse_terms("") == {}
    assert terms.parse_terms("no arrows here at all") == {}
    assert terms.parse_terms("=> only target") == {}   # empty source dropped


# --- extract_terms (reuses the draft translator's rewrite) ------------------

class _FakeExtractor:
    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def available(self):
        return True

    def rewrite(self, body, instruction, *, max_tokens=300):
        self.calls += 1
        return self._reply


def test_extract_terms(monkeypatch):
    fake = _FakeExtractor("小明 => Tiểu Minh\n华为 => Huawei")
    monkeypatch.setattr(terms, "get_translator", lambda name: fake)
    out = terms.extract_terms(_cues(["小明来了", "用华为手机"]), "fake")
    assert out == {"小明": "Tiểu Minh", "华为": "Huawei"} and fake.calls == 1


def test_extract_terms_provider_without_rewrite_returns_empty(monkeypatch):
    class NoRewrite:
        def available(self):
            return True

    monkeypatch.setattr(terms, "get_translator", lambda name: NoRewrite())
    assert terms.extract_terms(_cues(["小明"]), "fake") == {}


# --- check_glossary (pure) --------------------------------------------------

def test_check_glossary_flags_missing_target():
    cn = _cues(["小明和华为", "普通一句", "华为很好"])
    vi = _cues(["Tiểu Minh và Huawei", "câu thường", "hãng kia rất tốt"])   # line 3 misses Huawei
    glo = {"小明": "Tiểu Minh", "华为": "Huawei"}
    out = terms.check_glossary(cn, vi, glo)
    assert out == [{"index": 3, "term": "华为", "expected": "Huawei"}]


def test_check_glossary_case_insensitive_diacritics_exact():
    cn = _cues(["北京", "河内"])
    # "Bac Kinh" (no diacritics) must NOT satisfy "Bắc Kinh"; case doesn't matter
    vi = _cues(["ở BẮC KINH", "tại Ha Noi"])
    glo = {"北京": "Bắc Kinh", "河内": "Hà Nội"}
    out = terms.check_glossary(cn, vi, glo)
    assert out == [{"index": 2, "term": "河内", "expected": "Hà Nội"}]   # diacritics wrong


# --- suggest_glossary writes the sidecar ------------------------------------

def test_suggest_glossary_writes_sidecar(tmp_path, monkeypatch):
    fake = _FakeExtractor("小明 => Tiểu Minh")
    monkeypatch.setattr(terms, "get_translator", lambda name: fake)
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n小明来了\n", encoding="utf-8")
    n = terms.suggest_glossary(job, "fake")
    assert n == 1
    data = json.loads((job.work_dir / "glossary.suggested.json").read_text(encoding="utf-8"))
    assert data == {"小明": "Tiểu Minh"}


# --- precedence: suggested < global < job -----------------------------------

def test_glossary_precedence_job_wins(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vizsup_storage_dir", str(tmp_path / "storage"))
    (tmp_path / "storage").mkdir()
    job = Job(id="t", work_dir=tmp_path / "job")
    (job.work_dir / "glossary.suggested.json").write_text(
        json.dumps({"华为": "SAI-suggested", "小明": "Tiểu Minh"}), encoding="utf-8")
    (tmp_path / "storage" / "glossary.json").write_text(
        json.dumps({"华为": "SAI-global"}), encoding="utf-8")
    (job.work_dir / "glossary.json").write_text(
        json.dumps({"华为": "Huawei"}), encoding="utf-8")   # job wins
    merged = translate.load_glossary(job)
    assert merged["华为"] == "Huawei"          # job overrides global overrides suggested
    assert merged["小明"] == "Tiểu Minh"       # suggested-only entry survives


# --- report writing ---------------------------------------------------------

def test_write_glossary_report(tmp_path):
    job = Job(id="t", work_dir=tmp_path / "job")
    cn = _cues(["华为很好"])
    vi = _cues(["hãng kia rất tốt"])
    n = terms.write_glossary_report(job, cn, vi, {"华为": "Huawei"})
    assert n == 1
    assert json.loads((job.work_dir / "glossary_report.json").read_text(encoding="utf-8")) == [
        {"index": 1, "term": "华为", "expected": "Huawei"}]
    # a clean run removes any stale report
    assert terms.write_glossary_report(job, cn, _cues(["Huawei tốt"]), {"华为": "Huawei"}) == 0
    assert not (job.work_dir / "glossary_report.json").exists()
