"""N-17: cue-model integrity. The audit's reopen bug: load_pair paired vi[i] ↔
cn[i] by index, so after split/merge/delete every later row showed the WRONG
Chinese reference on reload — blinding the gate's translation check. zh_ref.json
now persists the editor's zh column across structural edits. Split/merge policy
is pure and locked here too. cuemodel is Qt-free — no display needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

from app.models import Job  # noqa: E402
from desktop import cuemodel  # noqa: E402
from desktop.cuemodel import EditorCue, merge_cues, split_cue  # noqa: E402

CN = ("1\n00:00:00,000 --> 00:00:01,000\n中文一\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\n中文二\n\n"
      "3\n00:00:02,000 --> 00:00:03,000\n中文三\n")
VI = ("1\n00:00:00,000 --> 00:00:01,000\nviệt một\n\n"
      "2\n00:00:01,000 --> 00:00:02,000\nviệt hai\n\n"
      "3\n00:00:02,000 --> 00:00:03,000\nviệt ba\n")


def _job(tmp_path):
    job = Job(id="t", work_dir=tmp_path / "job")
    job.cn_srt.write_text(CN, encoding="utf-8")
    job.vi_srt.write_text(VI, encoding="utf-8")
    return job


# --- zh_ref alignment across structural edits ---------------------------------

def test_zh_survives_delete_and_reopen(tmp_path):
    job = _job(tmp_path)
    cues = cuemodel.load_pair(job)
    assert [c.zh for c in cues] == ["中文一", "中文二", "中文三"]
    del cues[0]                                    # structural edit shifts rows
    cuemodel.save_vi(job, cues)
    back = cuemodel.load_pair(job)
    # before N-17 this read ["中文一", "中文二"] — every row's reference wrong
    assert [c.zh for c in back] == ["中文二", "中文三"]


def test_zh_survives_split_and_reopen(tmp_path):
    job = _job(tmp_path)
    cues = cuemodel.load_pair(job)
    cues[0:1] = list(split_cue(cues[0]))
    cuemodel.save_vi(job, cues)
    back = cuemodel.load_pair(job)
    assert [c.zh for c in back] == ["中文一", "", "中文二", "中文三"]


def test_no_structural_edits_leaves_no_sidecar(tmp_path):
    job = _job(tmp_path)
    cues = cuemodel.load_pair(job)
    cues[1].vi = "việt hai (sửa chữ thôi)"         # text edit ≠ structural edit
    cuemodel.save_vi(job, cues)
    assert not (job.work_dir / "zh_ref.json").exists()   # redundant → not written
    assert [c.zh for c in cuemodel.load_pair(job)] == ["中文一", "中文二", "中文三"]


def test_length_mismatched_zh_ref_falls_back_to_index_pairing(tmp_path):
    job = _job(tmp_path)
    (job.work_dir / "zh_ref.json").write_text(
        json.dumps(["chỉ một dòng"]), encoding="utf-8")   # stale: 1 entry, 3 rows
    assert [c.zh for c in cuemodel.load_pair(job)] == ["中文一", "中文二", "中文三"]


# --- pure split/merge policy -----------------------------------------------------

def _cue(**kw):
    base = dict(start=0.0, end=4.0, vi="một hai ba bốn", zh="中文", gain_db=0.0,
                mute=False, style=None)
    base.update(kw)
    return EditorCue(**base)


def test_split_halves_time_and_words_inherits_dub_and_style():
    st = {"primary": "#FFD400"}
    a, b = split_cue(_cue(gain_db=-3.0, mute=True, style=st))
    assert (a.start, a.end) == (0.0, 2.0) and (b.start, b.end) == (2.0, 4.0)
    assert a.vi == "một hai" and b.vi == "ba bốn"
    assert a.zh == "中文" and b.zh == ""            # reference stays on the first half
    for half in (a, b):                             # P0-2 / C-3 inheritance policy
        assert half.gain_db == -3.0 and half.mute is True and half.style is st


def test_merge_policy():
    st_a, st_b = {"bold": True}, {"italic": True}
    m = merge_cues(_cue(end=2.0, vi="đầu", zh="前", gain_db=1.0, style=st_a),
                   _cue(start=2.0, vi="sau", zh="后", gain_db=-5.0, mute=True, style=st_b))
    assert (m.start, m.end) == (0.0, 4.0)
    assert m.vi == "đầu sau"
    assert m.zh == "前 后"                           # space between non-empty parts
    assert m.gain_db == -5.0                        # larger |gain| wins
    assert m.mute is True                           # never silently un-mute
    assert m.style is st_a                          # first cue's style kept


def test_merge_zh_no_stray_space_when_one_empty():
    m = merge_cues(_cue(zh=""), _cue(zh="后"))
    assert m.zh == "后"


def test_split_then_merge_roundtrips_text():
    c = _cue()
    a, b = split_cue(c)
    m = merge_cues(a, b)
    assert m.vi == c.vi and (m.start, m.end) == (c.start, c.end)
