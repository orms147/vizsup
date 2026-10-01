"""v2 editor data: review flags, approval revisions, waveform peaks, and the cue
model's lock / "Đã xem" / timing edits. All headless (no Qt, no ffmpeg)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → `desktop`

from app.models import Cue, Job  # noqa: E402
from app.pipeline import approval, review, waveform  # noqa: E402
from app.pipeline.srt import write_srt  # noqa: E402
from desktop import cuemodel  # noqa: E402
from desktop.cuemodel import EditorCue  # noqa: E402


@pytest.fixture()
def job(tmp_path):
    j = Job(id="v2", work_dir=tmp_path / "job")
    write_srt([Cue(1, 0.0, 2.0, "xin chào mọi người"), Cue(2, 2.5, 4.0, "hôm nay")], j.vi_srt)
    return j


# --- review flags ----------------------------------------------------------------

def ec(start, end, vi="ok"):
    return EditorCue(start=start, end=end, vi=vi)


def test_short_overlap_and_long_are_flagged():
    cues = [ec(0.0, 3.0), ec(2.5, 2.8, "rất nhiều chữ ở đây không thể nói kịp trong khe này"),
            ec(3.0, 8.0)]                       # slot = next start − start = 0.5 s
    iss = review.cue_issues(cues)
    assert iss[0] == []
    assert iss[1] == ["short", "overlap", "long"]
    assert iss[2] == []


def test_ocr_and_term_flags_map_by_time_not_index():
    """Reports number cues by cn.srt position, which drifts from vi.srt once lines
    are deduped / split / merged — time overlap is what stays correct."""
    cues = [ec(0.0, 2.0), ec(2.0, 4.0), ec(4.0, 6.0)]
    iss = review.cue_issues(cues, ocr_spans=[review.Span(2.2, 3.0)],
                            term_spans=[review.Span(4.5, 4.9)])
    assert iss == [[], ["ocr"], ["term"]]


def test_flag_spans_load_from_the_reports(job):
    write_srt([Cue(1, 0.0, 1.0, "一"), Cue(2, 2.6, 3.5, "二")], job.cn_srt)
    (job.work_dir / "ocr_report.json").write_text(json.dumps(
        [{"index": 1, "suspect": False}, {"index": 2, "suspect": True}]), encoding="utf-8")
    (job.work_dir / "glossary_report.json").write_text(json.dumps(
        [{"index": 1, "term": "小王", "expected": "Tiểu Vương"}]), encoding="utf-8")
    ocr, term = review.load_flag_spans(job)
    assert ocr == [review.Span(2.6, 3.5)] and term == [review.Span(0.0, 1.0)]


def test_flag_spans_tolerate_missing_or_corrupt_reports(job):
    (job.work_dir / "ocr_report.json").write_text("{bad", encoding="utf-8")
    assert review.load_flag_spans(job) == ([], [])


# --- approval revisions ---------------------------------------------------------

def test_approve_then_edit_revokes(job):
    assert not approval.is_approved(job)
    approval.approve(job, cues=2)
    assert approval.is_approved(job)
    write_srt([Cue(1, 0.0, 2.0, "đã sửa sau khi duyệt")], job.vi_srt)
    assert not approval.is_approved(job)


def test_style_changes_revoke_too(job):
    approval.approve(job)
    (job.work_dir / "style.json").write_text('{"size": 30}', encoding="utf-8")
    assert not approval.is_approved(job)


def test_review_bookkeeping_does_not_revoke(job):
    """Marking a line 'Đã xem' or locking it changes nothing that is voiced."""
    cues = cuemodel.load_pair(job)
    cuemodel.save_vi(job, cues)
    approval.approve(job)
    cues[0].locked = True
    cuemodel.mark_reviewed(cues[1])
    cuemodel.save_vi(job, cues)
    assert approval.is_approved(job)


def test_dub_overrides_do_revoke(job):
    cues = cuemodel.load_pair(job)
    approval.approve(job)
    cues[1].mute = True
    cuemodel.save_vi(job, cues)
    assert not approval.is_approved(job)


# --- cue model: lock / review / timing ------------------------------------------

def test_lock_and_review_round_trip_through_the_sidecar(job):
    cues = cuemodel.load_pair(job)
    cues[0].locked = True
    cuemodel.mark_reviewed(cues[1])
    cuemodel.save_vi(job, cues)
    back = cuemodel.load_pair(job)
    assert back[0].locked and not back[1].locked
    assert cuemodel.is_reviewed(back[1]) and not cuemodel.is_reviewed(back[0])


def test_editing_a_reviewed_cue_makes_it_unreviewed():
    c = ec(1.0, 2.0, "xin chào")
    cuemodel.mark_reviewed(c)
    assert cuemodel.is_reviewed(c)
    c.vi = "xin chào bạn"
    assert not cuemodel.is_reviewed(c)
    cuemodel.mark_reviewed(c)
    c.end = 2.05
    assert not cuemodel.is_reviewed(c)


@pytest.mark.parametrize("which, delta, expect", [
    ("start", 50, (1.05, 2.0)), ("start", -200, (0.8, 2.0)),
    ("end", 50, (1.0, 2.05)), ("end", -200, (1.0, 1.8)),
])
def test_nudge(which, delta, expect):
    c = ec(1.0, 2.0)
    assert cuemodel.nudge(c, which, delta, duration=10.0)
    assert (c.start, c.end) == expect


def test_nudge_clamps_to_the_video_and_keeps_start_before_end():
    c = ec(0.03, 0.4)
    cuemodel.nudge(c, "start", -200, duration=10.0)
    assert c.start == 0.0
    cuemodel.nudge(c, "start", 200, duration=10.0)
    assert c.start == pytest.approx(0.2)              # stops MIN_CUE before the end
    d = ec(9.0, 9.95)
    cuemodel.nudge(d, "end", 200, duration=10.0)
    assert d.end == 10.0


def test_a_locked_cue_does_not_move():
    c = ec(1.0, 2.0)
    c.locked = True
    assert not cuemodel.nudge(c, "start", 50, duration=10.0)
    assert not cuemodel.clamp_edge(c, "end", 5.0)
    assert (c.start, c.end) == (1.0, 2.0)


def test_split_at_playhead_divides_words_by_time():
    c = ec(0.0, 4.0, "một hai ba bốn")
    c.zh = "一二三四"
    a, b = cuemodel.split_at(c, 1.0)
    assert (a.start, a.end, b.start, b.end) == (0.0, 1.0, 1.0, 4.0)
    assert a.vi == "một" and b.vi == "hai ba bốn"
    assert a.zh == "一二三四" and b.zh == ""


def test_split_outside_the_cue_is_refused():
    c = ec(1.0, 2.0, "một hai")
    assert cuemodel.split_at(c, 0.5) is None
    assert cuemodel.split_at(c, 1.05) is None          # too close to an edge


def test_copy_cue_does_not_share_the_style_dict():
    c = ec(0.0, 1.0)
    c.style = {"size": 40}
    d = cuemodel.copy_cue(c)
    d.style["size"] = 10
    assert c.style == {"size": 40}


# --- waveform -------------------------------------------------------------------

def test_waveform_peaks_are_normalised_and_cached(job, monkeypatch):
    from array import array

    job.source_video.write_bytes(b"fake")
    calls = []
    loud = array("h", [0] * 40 + [10000] * 40 + [5000] * 40)   # 3 windows of 40 samples

    class R:
        stdout = loud.tobytes()

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return R()

    monkeypatch.setattr(waveform, "RATE", 2000)
    monkeypatch.setattr(waveform, "PER_SEC", 50)                # 40-sample windows
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("app.ffmpegutil.ffmpeg_bin", lambda: "ffmpeg")
    p = waveform.peaks(job)
    assert p == [0.0, 1.0, 0.5]
    assert waveform.peaks(job) == p and len(calls) == 1          # second call hits the cache
