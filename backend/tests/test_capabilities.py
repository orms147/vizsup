"""N-12: provider capability honesty.

- A stub TTS engine must never advertise available()=True (a keyed user used to
  pass preflight and die on segment 1 with NotImplementedError).
- can_refine/can_proofread must exactly mirror which providers actually override
  the base no-op methods — a drift here silently re-creates the "✓ Đã rà soát"
  lie this task removed.
- proofread_srt: skips incapable providers with count 0, and a mid-run API error
  keeps the batches already fixed instead of killing the OCR run.
"""
from __future__ import annotations

from app.models import Job
from app.pipeline import ocr
from app.providers.base import TranslationProvider
from app.providers.registry import TRANSLATORS, TTS_PROVIDERS, list_providers


def test_stub_tts_not_available():
    info = {p["name"]: p for p in list_providers()["tts"]}
    for name in ("azure", "fpt", "google"):
        assert info[name]["available"] is False, name
        assert info[name]["unavailable_note"] == "sắp có"


def test_stub_tts_synthesize_is_really_a_stub():
    # the available()=False hardcode is only honest while synthesize still raises;
    # this fails the moment someone implements one so they remember to flip it back
    import pytest

    for name in ("azure", "fpt", "google"):
        with pytest.raises(NotImplementedError):
            TTS_PROVIDERS[name].synthesize("x", None)


def test_capability_flags_mirror_overrides():
    for name, p in TRANSLATORS.items():
        overrides_refine = type(p).refine_texts is not TranslationProvider.refine_texts
        overrides_proof = type(p).proofread_texts is not TranslationProvider.proofread_texts
        assert p.can_refine == overrides_refine, f"{name}: can_refine lies"
        assert p.can_proofread == overrides_proof, f"{name}: can_proofread lies"


def test_info_exposes_flags():
    for p in list_providers()["translation"]:
        assert "can_refine" in p and "can_proofread" in p


CN = ("1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\n第二句\n\n"
      "3\n00:00:02,000 --> 00:00:03,000\n第三句\n")


class _NoProof:
    can_proofread = False

    def available(self):
        return True


class _FlakyProof:
    """Fixes batch 1, dies on batch 2."""
    can_proofread = True

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def proofread_texts(self, texts, **kw):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("429 rate limit")
        return [t + "✔" for t in texts]


def _job(tmp_path):
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text(CN, encoding="utf-8")
    return job


def test_proofread_skips_incapable_provider(tmp_path, monkeypatch):
    import app.providers.registry as registry

    monkeypatch.setattr(registry, "get_translator", lambda name: _NoProof())
    logs: list[str] = []
    job = _job(tmp_path)
    before = job.cn_srt.read_bytes()
    assert ocr.proofread_srt(job, provider="fake", log=logs.append) == 0
    assert job.cn_srt.read_bytes() == before
    assert any("không hỗ trợ" in m for m in logs)


def test_proofread_partial_failure_keeps_fixed_batches(tmp_path, monkeypatch):
    import app.providers.registry as registry

    monkeypatch.setattr(registry, "get_translator", lambda name: _FlakyProof())
    monkeypatch.setattr(ocr, "_PROOF_BATCH", 2)          # 3 lines → 2 batches
    logs: list[str] = []
    job = _job(tmp_path)
    done = ocr.proofread_srt(job, provider="fake", log=logs.append)
    assert done == 2                                     # batch 1 landed, batch 2 died
    text = job.cn_srt.read_text(encoding="utf-8")
    assert "第一句✔" in text and "第二句✔" in text       # fixes kept
    assert "第三句✔" not in text and "第三句" in text     # original preserved
    assert any("dừng giữa chừng" in m for m in logs)
