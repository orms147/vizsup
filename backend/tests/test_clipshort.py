"""«Cắt Short» (S3), backend: proposals (heuristic + one AI call), the derived project (cut video,
shifted approved lines, reused TTS, NO approval), the burned hook title + "Xem full tập N"."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import Cue, Job  # noqa: E402
from app.pipeline import approval, assemble, clipshort  # noqa: E402
from app.pipeline.srt import parse_srt, write_srt  # noqa: E402


def talk(n=120, step=2.5, dur=2.0):
    """A conversation: every 7th line is a question, every 11th an exclamation."""
    out = []
    for i in range(n):
        txt = f"Câu thoại số {i} nói về chuyện nhà"
        if i % 7 == 0:
            txt = f"Tại sao cô ấy lại làm vậy {i}?"
        elif i % 11 == 0:
            txt = f"Không thể nào {i}!"
        out.append(Cue(i + 1, i * step, i * step + dur, txt))
    return out


# --- proposals -------------------------------------------------------------------------------------

def test_hook_scoring_and_text():
    assert clipshort.hook_score("Tại sao cô ấy lại làm vậy?") > clipshort.hook_score("Anh đi ăn cơm.")
    assert clipshort.hook_score("Không thể nào!") > clipshort.hook_score("ừ")
    assert clipshort.hook_score("") < 0
    assert clipshort.hook_text("- Anh đi đâu thế?") == "Anh đi đâu thế?"
    long = clipshort.hook_text("x" * 200)
    assert len(long) == clipshort.HOOK_MAX and long.endswith("…")


def test_heuristic_windows_are_30_to_60_s_non_overlapping_and_start_on_a_hook():
    cs = talk()
    got = clipshort.heuristic(cs, n=3)
    assert 1 <= len(got) <= 3
    for c in got:
        assert clipshort.SHORT_MIN <= c.length <= clipshort.SHORT_MAX, c
        assert c.hook and c.reason and c.hook == clipshort.hook_text(
            next(x.text for x in cs if abs(x.start - c.start) < 1e-6))
    for a, b in zip(got, got[1:], strict=False):
        assert b.start >= a.end + 5.0 - 1e-6                 # a gap between shorts
    assert [c.start for c in got] == sorted(c.start for c in got)
    # they open on a question / exclamation, not on a flat line
    assert all(clipshort.hook_score(next(x.text for x in cs if abs(x.start - c.start) < 1e-6)) >= 2
               for c in got)
    assert clipshort.heuristic([]) == []
    assert clipshort.heuristic(talk(8)) == []                  # 20 s of talk: no 30 s window


def test_fit_range_keeps_whole_lines():
    cs = talk(20)
    a, b = clipshort.fit_range(cs, 10.0, 40.0)
    inside = [c for c in cs if 10.0 <= (c.start + c.end) / 2 < 40.0]
    assert a == pytest.approx(inside[0].start - clipshort.PAD_BEFORE)
    assert b == pytest.approx(inside[-1].end + clipshort.PAD_AFTER)
    assert clipshort.fit_range(cs, 1000.0, 1010.0) is None


REPLY = '''Đây là gợi ý:
```json
{"shorts": [
 {"start": 12.6, "end": 55.0, "hook": "Tại sao cô ấy lại làm vậy?", "why": "mở bằng câu hỏi"},
 {"start": 20.0, "end": 60.0, "hook": "chồng lấn", "why": "x"},
 {"start": 100.0, "end": 110.0, "hook": "quá ngắn", "why": "x"},
 {"start": "abc", "end": 3, "hook": "hỏng"},
 {"start": 150.0, "end": 195.0, "hook": "", "why": "không có tiêu đề"}
]}
```'''


def test_parse_reply_snaps_to_lines_and_drops_the_unusable():
    cs = talk(120)
    got = clipshort.parse_reply(REPLY, cs)
    assert [c.hook[:10] for c in got][0] == "Tại sao cô"
    assert len(got) == 2                                      # overlap, too short, broken: dropped
    first, second = got
    assert any(abs(first.start - (c.start - clipshort.PAD_BEFORE)) < 1e-6 for c in cs)
    assert 20 <= first.length <= 75
    assert second.hook                                        # no hook given → the opening line
    assert clipshort.parse_reply("không phải json", cs) == []
    assert clipshort.parse_reply('{"shorts": "x"}', cs) == []


class _FakeProv:
    def __init__(self, reply="", ok=True):
        self.reply, self.ok, self.calls = reply, ok, []

    def available(self):
        return self.ok

    def rewrite(self, text, instruction, *, max_tokens, role):
        self.calls.append((text, instruction, max_tokens, role))
        return self.reply


def test_ask_ai_is_one_shorten_call_and_fails_cleanly(monkeypatch):
    cs = talk(120)
    prov = _FakeProv(REPLY)
    monkeypatch.setattr("app.providers.registry.get_translator", lambda _n: prov)
    got = clipshort.ask_ai(cs, "第8集 | 《末日校车》08")
    assert len(prov.calls) == 1 and prov.calls[0][3] == "shorten" and prov.calls[0][2] <= 700
    assert "[0.0–2.0]" in prov.calls[0][0] and "末日校车" in prov.calls[0][0]
    assert got and got[0].score > 0
    monkeypatch.setattr("app.providers.registry.get_translator", lambda _n: _FakeProv(ok=False))
    with pytest.raises(RuntimeError, match="OpenRouter"):
        clipshort.ask_ai(cs)
    monkeypatch.setattr("app.providers.registry.get_translator", lambda _n: _FakeProv("{}"))
    with pytest.raises(RuntimeError, match="không trả về"):
        clipshort.ask_ai(cs)


# --- the derived project ------------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _parent(base: Path, *, approved=True, source=True, title="第8集 | 《末日校车》08 开头") -> Job:
    j = Job(id="parent000001", work_dir=base / "parent000001", url="https://v.douyin.com/x/")
    j.metadata = {"title": title, "platform": "douyin", "uploader": "某人", "duration": 100.0}
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                              encoding="utf-8")
    cs = talk(40)
    write_srt(cs, j.vi_srt)
    write_srt([Cue(c.index, c.start, c.end, f"中文{c.index}") for c in cs], j.cn_srt)
    (j.work_dir / "style.json").write_text('{"font": "Be Vietnam Pro"}', encoding="utf-8")
    (j.work_dir / "hardsub_region.json").write_text('{"x": 0, "y": 0.8, "w": 1, "h": 0.15}', encoding="utf-8")
    (j.work_dir / "context_brief.json").write_text('{"brief": "x"}', encoding="utf-8")
    if source:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=blue:s=320x180:r=5:d=100", "-f", "lavfi", "-i",
                        "sine=f=220:r=48000:d=100", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(j.source_video)], check=True)
    for idx in (5, 6, 7):                                   # a rendered parent kept its TTS
        (j.tts_dir / f"seg_{idx:04d}.mp3").write_bytes(b"ID3" + bytes([idx]) * 40)
    (j.tts_dir / "manifest.json").write_text(json.dumps(
        {"5": "h5", "6": "h6", "7": "h7", "30": "h30"}), encoding="utf-8")
    if approved:
        approval.approve(j)
    return j


def _tree_stamp(d: Path) -> dict:
    return {str(p.relative_to(d)): p.stat().st_size for p in sorted(d.rglob("*")) if p.is_file()}


@needs_ffmpeg
def test_make_cuts_the_video_shifts_the_lines_and_reuses_tts(tmp_path):
    parent = _parent(tmp_path)
    before = _tree_stamp(parent.work_dir)
    child = clipshort.make(parent, 12.0, 52.0, "Tại sao cô ấy lại làm vậy?", tmp_path)
    assert _tree_stamp(parent.work_dir) == before             # the parent is only read
    # the lines: only those inside, shifted to 0, renumbered, zh paired 1:1
    pv = parse_srt(parent.vi_srt)
    kept = [c for c in pv if 12.0 <= (c.start + c.end) / 2 < 52.0]
    cv, cz = parse_srt(child.vi_srt), parse_srt(child.cn_srt)
    assert [c.text for c in cv] == [c.text for c in kept]
    assert [c.index for c in cv] == list(range(1, len(kept) + 1))
    assert [c.text for c in cz] == [f"中文{c.index}" for c in kept]
    off = cv[0].start - kept[0].start
    assert all(abs((c.start - k.start) - off) < 0.002 for c, k in zip(cv, kept, strict=True))
    assert cv[0].start >= 0 and cv[0].start <= clipshort.PAD_BEFORE + 1e-6
    # the video is the cut (length of the range plus air), re-encoded
    a, b = clipshort.fit_range(pv, 12.0, 52.0)
    dur = assemble.probe_duration(child.source_video)
    assert abs(dur - (b - a)) < 0.3, (dur, b - a)
    # metadata + project files
    md = json.loads((child.work_dir / "metadata.json").read_text(encoding="utf-8"))
    s = md["short"]
    assert s["parent"] == parent.id and s["episode"] == 8 and s["hook"].startswith("Tại sao")
    assert md["title"].endswith("· Short 1") and "末日校车" in md["title"] and "group" not in md
    assert md["uploader"] == "某人"
    assert json.loads((child.work_dir / "job.json").read_text(encoding="utf-8"))["id"] == child.id
    for name in ("style.json", "hardsub_region.json", "context_brief.json"):   # the cover needs the band
        assert (child.work_dir / name).exists(), name
    # TTS reuse: parent lines 5, 6, 7 that fall inside → new indexes, same hash
    new_idx = {k: v for k, v in json.loads(
        (child.tts_dir / "manifest.json").read_text(encoding="utf-8")).items()}
    inside = [i + 1 for i, c in enumerate(pv) if 12.0 <= (c.start + c.end) / 2 < 52.0]
    want = {str(j): f"h{pi}" for j, pi in enumerate(inside, 1) if pi in (5, 6, 7)}
    assert new_idx == want and want
    for j in want:
        assert (child.tts_dir / f"seg_{int(j):04d}.mp3").stat().st_size > 0
    # the human gate: no approval carried over
    assert not approval.is_approved(child) and not (child.work_dir / "approved.json").exists()
    # a second short of the same episode is "Short 2"
    again = clipshort.make(parent, 60.0, 100.0, "", tmp_path)
    assert json.loads((again.work_dir / "metadata.json").read_text(encoding="utf-8"))[
        "title"].endswith("· Short 2")


@needs_ffmpeg
def test_make_refuses_what_it_must(tmp_path):
    with pytest.raises(RuntimeError, match="duyệt phụ đề"):
        clipshort.make(_parent(tmp_path / "a", approved=False), 12, 52, "x", tmp_path / "a")
    nosrc = _parent(tmp_path / "b", source=False)
    with pytest.raises(RuntimeError, match="video gốc"):
        clipshort.make(nosrc, 12, 52, "x", tmp_path / "b")
    ok = _parent(tmp_path / "c")
    with pytest.raises(RuntimeError, match="không có câu thoại"):
        clipshort.make(ok, 500, 540, "x", tmp_path / "c")
    with pytest.raises(RuntimeError, match="dài"):
        clipshort.make(ok, 0, 3, "x", tmp_path / "c")                  # a single short line
    # nothing was left behind by the refusals
    assert [d.name for d in (tmp_path / "c").iterdir()] == ["parent000001"]


# --- the burned notes ---------------------------------------------------------------------------------------

def _short_job(tmp_path: Path, **short) -> Job:
    j = Job(id="child0000001", work_dir=tmp_path / "child0000001")
    j.metadata = {"title": "x · Short 1", "short": {"parent": "p", "hook": "Anh đi đâu?",
                                                       "episode": 8, **short}}
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                              encoding="utf-8")
    return j


def test_events_hook_first_end_card_last(tmp_path):
    j = _short_job(tmp_path)
    hook, end = clipshort.events(j, 44.0)
    assert (hook["start"], hook["end"], hook["align"]) == (0.0, clipshort.HOOK_SECONDS, 8)
    assert hook["margin_v"] == clipshort.HOOK_MARGIN_V and hook["text"].endswith("Anh đi đâu?")
    assert (end["start"], end["end"], end["align"]) == (41.0, 44.0, 5)
    assert end["text"].endswith("Xem full tập 8")
    no_ep = _short_job(tmp_path / "n", episode=None)
    assert clipshort.events(no_ep, 44.0)[1]["text"].endswith("Xem full video")
    assert len(clipshort.events(j, 5.0)) == 1                     # too short for an end card
    safe = _short_job(tmp_path / "s", hook="{\\b1}Gì {đó}")
    assert clipshort.events(safe, 44.0)[0]["text"].endswith("b1Gì đó")   # no ASS override gets in
    plain = Job(id="p", work_dir=tmp_path / "plain")
    assert clipshort.events(plain, 44.0) == [] and clipshort.info(plain) is None


def test_assemble_burns_them_and_drops_the_long_form_end_card(tmp_path, monkeypatch):
    seen = {}

    def fake_write_ass(cues, path, **kw):
        seen["extra"] = kw.get("extra")
        raise RuntimeError("stop after the subtitles")

    j = _short_job(tmp_path)
    j.metadata["title"] = "《末日校车》08 x · Short 1"
    (j.work_dir / "metadata.json").write_text(json.dumps(j.metadata, ensure_ascii=False),
                                              encoding="utf-8")
    (j.work_dir / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
    monkeypatch.setattr(assemble, "write_ass", fake_write_ass)
    monkeypatch.setattr(assemble, "probe_duration", lambda _p: 44.0)
    (j.work_dir / "source.mp4").write_bytes(b"x")
    with pytest.raises(RuntimeError):
        assemble.assemble(j, [], episode_card={"on": True, "chip": "Tập {n}",
                                               "end": "Còn tiếp · Tập {next}"})
    texts = [e["text"].split("}")[-1] for e in seen["extra"]]
    assert "Tập 8" in texts and "Anh đi đâu?" in texts and "Xem full tập 8" in texts
    assert not any("Còn tiếp" in t for t in texts)
