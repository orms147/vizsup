"""Headless checks for the auto-loaded zh→vi glossary (translate.load_glossary).

Run from backend/:  pytest tests/test_glossary.py
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.models import Job
from app.pipeline.translate import load_glossary


def _job(tmp_path: Path) -> Job:
    return Job(id="t", work_dir=tmp_path / "job")


def test_missing_files_give_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "storage")
    assert load_glossary(_job(tmp_path)) == {}


def test_global_and_job_merge_job_wins(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", storage)
    job = _job(tmp_path)
    (storage / "glossary.json").write_text(
        json.dumps({"小米": "Xiaomi", "华为": "Huawei"}, ensure_ascii=False), encoding="utf-8")
    (job.work_dir / "glossary.json").write_text(
        json.dumps({"华为": "Hoa Vi"}, ensure_ascii=False), encoding="utf-8")
    assert load_glossary(job) == {"小米": "Xiaomi", "华为": "Hoa Vi"}


def test_invalid_json_is_skipped_not_fatal(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", storage)
    job = _job(tmp_path)
    (storage / "glossary.json").write_text("{oops", encoding="utf-8")          # broken
    (job.work_dir / "glossary.json").write_text(
        json.dumps({"你好": "xin chào"}, ensure_ascii=False), encoding="utf-8")  # good
    assert load_glossary(job) == {"你好": "xin chào"}


def test_non_dict_payload_ignored(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", storage)
    job = _job(tmp_path)
    (job.work_dir / "glossary.json").write_text(json.dumps(["a", "b"]), encoding="utf-8")
    assert load_glossary(job) == {}
