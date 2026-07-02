"""N-17: the unified sidecar loaders (pipeline/sidecars.py) — one implementation
where three near-identical copies used to drift; backend and editor must read
identical data from the same job dir, and the on-disk schema stays byte-
compatible with existing jobs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

from app.models import Job  # noqa: E402
from app.pipeline import sidecars  # noqa: E402


def _job(tmp_path):
    return Job(id="t", work_dir=tmp_path / "job")


def test_overrides_roundtrip_and_unlink_on_defaults(tmp_path):
    job = _job(tmp_path)
    rows = [{"gain_db": -2.5, "mute": False}, {"gain_db": 0.0, "mute": True}]
    sidecars.save_overrides(job, rows)
    assert sidecars.load_overrides(job) == rows
    sidecars.save_overrides(job, [{"gain_db": 0.0, "mute": False}] * 2)  # all defaults
    assert not (job.work_dir / "overrides.json").exists()
    assert sidecars.load_overrides(job) == []


def test_style_roundtrip_int_keys_and_unlink_on_empty(tmp_path):
    job = _job(tmp_path)
    sidecars.save_style_overrides(job, {"3": {"primary": "#FFD400"}})
    assert sidecars.load_style_overrides(job) == {"3": {"primary": "#FFD400"}}
    assert sidecars.load_cue_styles(job) == {3: {"primary": "#FFD400"}}   # int for write_ass
    sidecars.save_style_overrides(job, {})
    assert not (job.work_dir / "style_overrides.json").exists()
    assert sidecars.load_cue_styles(job) is None


def test_corrupt_files_degrade_to_empty(tmp_path):
    job = _job(tmp_path)
    (job.work_dir / "overrides.json").write_text("{oops", encoding="utf-8")
    (job.work_dir / "style_overrides.json").write_text("[not a dict]", encoding="utf-8")
    assert sidecars.load_overrides(job) == []
    assert sidecars.load_style_overrides(job) == {}
    assert sidecars.load_cue_styles(job) is None


def test_disk_schema_unchanged_for_existing_jobs(tmp_path):
    # a pre-refactor job dir (string keys, plain list) must load as before
    job = _job(tmp_path)
    (job.work_dir / "overrides.json").write_text(
        json.dumps([{"gain_db": 1.5, "mute": True}]), encoding="utf-8")
    (job.work_dir / "style_overrides.json").write_text(
        json.dumps({"2": {"bold": True}}), encoding="utf-8")
    assert sidecars.load_overrides(job)[0]["mute"] is True
    assert sidecars.load_cue_styles(job) == {2: {"bold": True}}


def test_backend_and_editor_loaders_agree(tmp_path):
    from app.pipeline.assemble import _load_cue_styles
    from app.pipeline.tts import load_overrides as tts_overrides
    from desktop import cuemodel

    job = _job(tmp_path)
    sidecars.save_overrides(job, [{"gain_db": -1.0, "mute": True}])
    sidecars.save_style_overrides(job, {"1": {"italic": True}})
    assert tts_overrides(job) == cuemodel.load_overrides(job) == sidecars.load_overrides(job)
    assert cuemodel.load_style_overrides(job) == sidecars.load_style_overrides(job)
    assert _load_cue_styles(job) == sidecars.load_cue_styles(job) == {1: {"italic": True}}
