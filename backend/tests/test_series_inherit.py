"""Bộ phim (b): what an episode inherits at the start of a run — glossary layers, the
series sheet in the translator prompt, pronounce rules, style and render settings."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.models import Cue, Job
from app.pipeline import series, speak, translate
from app.pipeline.srt import write_srt
from app.providers.translation._common import SYSTEM_PROMPT, system_prompt_for

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TITLE = "第4集 | 《末日校车》06 全员求生"


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    st = tmp_path / "storage"
    st.mkdir()
    monkeypatch.setattr(settings, "vizsup_storage_dir", st)
    return st


@pytest.fixture()
def job(storage):
    j = Job(id="ep6", work_dir=storage / "ep6", metadata={"title": TITLE, "platform": "douyin"})
    write_srt([Cue(1, 0, 2, "林默说话"), Cue(2, 2, 4, "丧尸来了")], j.cn_srt)
    return j


def profile(job, **kw) -> series.Profile:
    p = series.resolve(job)
    for k, v in kw.items():
        setattr(p, k, v)
    series.save(p)
    return p


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# --- glossary layers ----------------------------------------------------------------------

def test_glossary_precedence_suggested_series_global_job(job, storage):
    profile(job, glossary={"A": "series-A", "B": "series-B", "C": "series-C"},
            characters=[{"zh": "D", "vi": "series-D"}])
    write(job.work_dir / "glossary.suggested.json", {"A": "sug-A", "E": "sug-E", "D": "sug-D"})
    write(storage / "glossary.json", {"B": "global-B"})
    write(job.work_dir / "glossary.json", {"C": "job-C"})
    assert translate.load_glossary(job) == {
        "A": "series-A", "B": "global-B", "C": "job-C", "D": "series-D", "E": "sug-E"}


def test_glossary_without_a_series_is_unchanged(storage):
    j = Job(id="solo", work_dir=storage / "solo", metadata={"title": "plain"})
    write(j.work_dir / "glossary.suggested.json", {"A": "x"})
    assert translate.load_glossary(j) == {"A": "x"}


# --- the translator's prompt ---------------------------------------------------------------

def test_series_sheet_and_previous_lines_render_in_the_system_prompt():
    out = system_prompt_for(SYSTEM_PROMPT, {"_series": "Nhân vật:\n- 林默 → Lâm Mặc",
                                            "_prev": ["Anh ở lại nhé.", "  ", "Được."]})
    assert "ONE EPISODE OF A SERIES" in out and "林默 → Lâm Mặc" in out
    assert "PREVIOUS EPISODE ENDED" in out and "- Anh ở lại nhé.\n- Được." in out
    assert system_prompt_for(SYSTEM_PROMPT, {}) == SYSTEM_PROMPT          # absent → byte-identical
    assert system_prompt_for(SYSTEM_PROMPT, {"_series": "", "_prev": []}) == SYSTEM_PROMPT


def test_translate_passes_the_series_sheet_into_context(job, monkeypatch):
    profile(job, brief="Phim sinh tồn.", characters=[{"zh": "林默", "vi": "Lâm Mặc"}])
    seen = {}

    def fake_cues(cues, **kw):
        seen.update(kw["context"])
        seen["glossary"] = kw["glossary"]
        return [Cue(c.index, c.start, c.end, "vi") for c in cues]

    monkeypatch.setattr(translate, "translate_cues", fake_cues)
    logs: list[str] = []
    translate.translate(job, draft="passthrough", log=logs.append)
    assert "Phim sinh tồn." in seen["_series"] and "Lâm Mặc" in seen["_series"]
    assert seen["glossary"] == {"林默": "Lâm Mặc"}
    assert any("bộ phim" in m for m in logs)


def test_translate_without_a_series_adds_nothing(storage, monkeypatch):
    j = Job(id="solo", work_dir=storage / "solo", metadata={"title": "plain"})
    write_srt([Cue(1, 0, 2, "你好")], j.cn_srt)
    seen = {}

    def fake_cues(cues, **kw):
        seen.update(kw["context"])
        return [Cue(c.index, c.start, c.end, "vi") for c in cues]

    monkeypatch.setattr(translate, "translate_cues", fake_cues)
    translate.translate(j, draft="passthrough")
    assert "_series" not in seen and "_prev" not in seen


# --- pronounce rules ---------------------------------------------------------------------------

def test_pronounce_layers_global_series_project(job, storage):
    write(storage / "pronounce.json", {"zombie": "global", "xe": "xe-global"})
    profile(job, pronounce={"zombie": "series", "bus": "series-bus"})
    write(job.work_dir / "pronounce.json", {"bus": "project-bus"})
    rules = speak.load_rules(job)
    assert rules["zombie"] == "series" and rules["bus"] == "project-bus" and rules["xe"] == "xe-global"
    assert speak.spoken("zombie và bus", rules) == "series và project-bus"


# --- style / render ----------------------------------------------------------------------------

def test_inherit_copies_style_and_render_only_when_missing(job):
    p = profile(job, style={"size": 52, "font": "Be Vietnam Pro"},
                render={"voice": "vi-VN-NamMinhNeural", "watermark": "Tôi", "preview": True})
    logs: list[str] = []
    done = series.inherit(job, p, log_fn=logs.append)
    assert done == ["kiểu phụ đề", "cài đặt dựng"] and "dùng lại" in logs[0]
    assert json.loads((job.work_dir / "style.json").read_text(encoding="utf-8"))["size"] == 52
    opts = json.loads((job.work_dir / "render_opts.json").read_text(encoding="utf-8"))
    assert opts["watermark"] == "Tôi" and "preview" not in opts       # batch.save_opts rules
    (job.work_dir / "style.json").write_text('{"size": 40}', encoding="utf-8")
    (job.work_dir / "render_opts.json").write_text('{"voice": "mine"}', encoding="utf-8")
    assert series.inherit(job, p) == []                               # the project's own wins
    assert json.loads((job.work_dir / "style.json").read_text(encoding="utf-8"))["size"] == 40


# --- the worker hook ------------------------------------------------------------------------------

pytest.importorskip("PySide6.QtWidgets")


def _worker(job, opts=None):
    from desktop.worker import PipelineWorker

    w = PipelineWorker(job, opts or {})
    logs: list[str] = []
    w.log.connect(logs.append)
    return w, logs


def test_worker_links_the_episode_and_inherits(job, storage):
    p = series.create("末日校车")
    p.style = {"size": 48}
    series.save(p)
    w, logs = _worker(job)
    w._series(job, {})
    assert series.link_of(job.work_dir)["slug"] == p.slug
    assert logs[0] == "Bộ phim: 《末日校车》 · 0 thuật ngữ"
    assert (job.work_dir / "style.json").exists()


def test_worker_option_none_skips_the_series(job):
    w, logs = _worker(job)
    w._series(job, {"series": "none"})
    assert series.link_of(job.work_dir) is None and logs == []


def test_worker_picked_series_beats_the_title(job):
    other = series.create("别的剧")
    w, _ = _worker(job)
    w._series(job, {"series": other.slug})
    assert series.link_of(job.work_dir)["slug"] == other.slug


def test_worker_series_failure_is_logged_not_raised(job, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(series, "resolve", boom)
    w, logs = _worker(job)
    w._series(job, {})
    assert logs and logs[-1].startswith("⚠ Bỏ qua bước bộ phim")
