"""N-3: translate() must never silently overwrite an existing vi.srt — that file
is what the human edit gate produces. Without force it refuses BEFORE any paid
API call; with force it backs the old file up first.
"""
from __future__ import annotations

import pytest

from app.models import Job
from app.pipeline import translate
from app.pipeline.srt import parse_srt


class _Fake:
    def available(self):
        return True

    def translate_texts(self, texts, **kw):
        return [f"vi:{t}" for t in texts]


CN = "1\n00:00:00,000 --> 00:00:01,000\n你好\n\n2\n00:00:01,000 --> 00:00:02,000\n再见\n"


def _job(tmp_path, with_vi: bool):
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text(CN, encoding="utf-8")
    if with_vi:
        job.vi_srt.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nbản sửa tay\n", encoding="utf-8")
    return job


def test_existing_vi_srt_refused_without_force(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(translate, "get_translator",
                        lambda name: calls.append(name) or _Fake())
    job = _job(tmp_path, with_vi=True)
    before = job.vi_srt.read_bytes()
    with pytest.raises(RuntimeError, match="không ghi đè"):
        translate.translate(job, draft="fake", glossary={})
    assert job.vi_srt.read_bytes() == before      # untouched
    assert calls == []                            # refused BEFORE any provider call


def test_force_backs_up_then_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _Fake())
    job = _job(tmp_path, with_vi=True)
    translate.translate(job, draft="fake", glossary={}, force=True)
    baks = list(job.work_dir.glob("vi.srt.bak-*"))
    assert len(baks) == 1
    assert "bản sửa tay" in baks[0].read_text(encoding="utf-8")   # old content preserved
    new = parse_srt(job.vi_srt)
    assert [c.text for c in new] == ["vi:你好", "vi:再见"]


def test_fresh_job_writes_without_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(translate, "get_translator", lambda name: _Fake())
    job = _job(tmp_path, with_vi=False)
    translate.translate(job, draft="fake", glossary={})
    assert job.vi_srt.exists()
    assert list(job.work_dir.glob("vi.srt.bak-*")) == []
