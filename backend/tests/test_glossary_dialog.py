"""N-23: glossary editor — backend save helper + an offscreen construct test of
the dialog (skipped where PySide6 is absent).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.pipeline.translate import load_glossary, save_glossary

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`


def test_save_glossary_roundtrip_sorted_atomic(tmp_path):
    p = tmp_path / "glossary.json"
    save_glossary(p, {"小米": "Xiaomi", "华为": "Hoa Vi"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"小米": "Xiaomi", "华为": "Hoa Vi"}
    assert list(data) == sorted(data)                 # stable ordering on disk
    assert not p.with_suffix(".json.tmp").exists()    # atomic write left no temp


def test_save_glossary_empty_removes_file(tmp_path):
    p = tmp_path / "glossary.json"
    save_glossary(p, {"a": "b"})
    save_glossary(p, {})
    assert not p.exists()


def test_dialog_edits_flow_to_load_glossary(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication, QTableWidgetItem

    QApplication.instance() or QApplication([])
    from app.config import settings
    from app.models import Job
    from desktop.glossary_dialog import GlossaryDialog

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "storage")
    (tmp_path / "storage").mkdir()
    job = Job(id="t", work_dir=tmp_path / "job")

    dlg = GlossaryDialog(job=job)
    dlg.table.insertRow(0)
    dlg.table.setItem(0, 0, QTableWidgetItem("华为"))
    dlg.table.setItem(0, 1, QTableWidgetItem("Hoa Vi"))
    dlg._save_all()

    assert load_glossary(job) == {"华为": "Hoa Vi"}   # dialog → files → translate stage