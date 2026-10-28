"""Ghép tập (app/pipeline/compilation.py): episode numbers and order, warnings, chapter
marks, where each project's video comes from, and real ffmpeg joins — stream copy when
every file matches, a per-file conversion when one differs, and a compilation re-used as
a source with its chapters kept."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from app.models import Cue, Job
from app.pipeline import compilation as comp
from app.pipeline.compilation import Clip, Source


# --- pure ---------------------------------------------------------------------------

@pytest.mark.parametrize("title, n", [
    ("第173集 | 《你和那个女孩怎么样了》", 173),
    ("第十二话 少年归来", 12),
    ("第二十三集", 23),
    ("第一百零五集", 105),
    ("EP12 Kiếm khách", 12),
    ("ep.7", 7),
    ("Tập 5 - Hồi kết", 5),
    ("P3 下集", 3),
    ("12. Ngày mưa", 12),
    ("07", 7),
    ("Ngày mưa (8)", 8),
    ("第２集", 2),                       # full-width digit
    ("Video 1080P hay nhất", None),      # a resolution is not an episode
    ("Không có số", None),
    ("", None),
])
def test_episode_number(title, n):
    assert comp.episode_number(title) == n


@pytest.mark.parametrize("title, rng", [
    # real Douyin posts: 第N集 counts the POSTS, the number after 《series》 is the episode
    ("第3集 | 《末日校车》1-5 我一个普通大巴司机#ai动漫", (1, 5)),
    ("第4集 | 《末日校车》06 我一个普通大巴司机", (6, 6)),
    ("《末日校车》 7～8 下集", (7, 8)),
    ("第173集 | 《你和那个女孩怎么样了》", (173, 173)),
    ("Không có số", None),
])
def test_episode_range(title, rng):
    assert comp.episode_range(title) == rng


def test_ranges_count_in_titles_and_gaps():
    a = Source("a", "job", "p3", [Clip(comp.Path("a.mp4"))], [(0.0, "Tập 1–5")],
               [{"id": "a", "number": 1, "last": 5, "title": ""}])
    b = Source("b", "job", "p4", [Clip(comp.Path("b.mp4"))], [(0.0, "Tập 6")],
               [{"id": "b", "number": 6, "last": 6, "title": ""}])
    d = Source("d", "job", "p6", [Clip(comp.Path("d.mp4"))], [(0.0, "Tập 8")],
               [{"id": "d", "number": 8, "last": 8, "title": ""}])
    assert comp.default_title([a, b, d]) == "Tổng hợp tập 1–8"
    assert comp.warnings([a, b, d]) == ["Thiếu tập 7"]           # 2–4 are inside 1–5
    assert comp.chapter_name("", 1, 5) == "Tập 1–5"


def _src(sid: str, number: int | None, *, kind: str = "job", state: str = "ok",
         eps: list[dict] | None = None, dur_chapters=None) -> Source:
    eps = eps if eps is not None else [{"id": sid, "number": number, "title": f"t{sid}"}]
    return Source(sid, kind, f"Tiêu đề {sid}", [Clip(comp.Path(f"{sid}.mp4"))],
                  dur_chapters or [(0.0, comp.chapter_name("", number) if number else sid)],
                  eps, state, "chưa dựng video" if state == "missing" else "")


def test_order_numbers_first_then_the_rest_in_place():
    s = [_src("x", None), _src("b", 12), _src("c", 3), _src("y", None),
         _src("all", None, kind="compilation",
              eps=[{"id": f"e{i}", "number": i, "title": ""} for i in (1, 2)])]
    assert [x.id for x in comp.order(s)] == ["all", "c", "b", "x", "y"]


def test_default_title_and_warnings():
    first = _src("c50", None, kind="compilation",
                 eps=[{"id": f"e{i}", "number": i, "title": ""} for i in range(1, 51)])
    srcs = [first, _src("e51", 51), _src("e53", 53), _src("e30", 30),
            _src("bad", 54, state="missing")]
    srcs[3].episodes[0]["id"] = "e30"                       # episode 30 is in the 1–50 file too
    assert comp.default_title(srcs) == "Tổng hợp tập 1–54"
    w = " | ".join(comp.warnings(srcs))
    assert "chưa dựng video" in w
    assert "Trùng số tập: 30" in w and "Tập 30 có hai lần" in w
    assert "Thiếu tập 52" in w
    assert comp.default_title([_src("a", None), _src("b", None)]) == "Video ghép 2 tập"


def test_chapter_text_and_ffmetadata():
    ch = [(0.0, "Tập 1"), (95.4, "Tập 2"), (3725.0, "Tập 3; = #")]
    assert comp.chapters_text(ch) == "0:00 Tập 1\n1:35 Tập 2\n1:02:05 Tập 3; = #\n"
    meta = comp.ffmetadata("Tổng hợp", ch, 4000.0)
    assert meta.startswith(";FFMETADATA1\ntitle=Tổng hợp\n")
    assert meta.count("[CHAPTER]") == 3
    assert "START=95400\nEND=3725000" in meta and "END=4000000" in meta
    assert "title=Tập 3\\; \\= \\#" in meta


def test_place_moves_chapters_and_nested_compilations():
    a = _src("a", 1)
    nested = Source("c", "compilation", "1–2", [Clip(comp.Path("c.mp4"))],
                    [(0.0, "Tập 2"), (10.0, "Tập 3")], [])
    b = Source("g", "group", "dài", [Clip(comp.Path("p1.mp4")), Clip(comp.Path("p2.mp4"))],
               [(0.0, "Tập 4")], [])
    placed, chapters, starts = comp.place([a, nested, b], [5.0, 20.0, 7.0, 8.0])
    assert [round(t, 3) for t, _c in placed] == [0.0, 5.0, 25.0, 32.0]
    assert chapters == [(0.0, "Tập 1"), (5.0, "Tập 2"), (15.0, "Tập 3"), (25.0, "Tập 4")]
    assert starts == [0.0, 5.0, 25.0]


def test_merge_cues_clips_each_part_to_its_length():
    a = [Cue(1, 0.5, 1.5, "một"), Cue(2, 4.2, 5.5, "tràn sang tập sau"),
         Cue(3, 4.9, 7.0, "gần như hết ở sau")]
    b = [Cue(1, 0.0, 1.0, "hai")]
    out = comp.merge_cues([(a, 0.0, 5.0), (b, 5.0, 3.0)])
    assert [(c.index, c.start, c.end, c.text) for c in out] == [
        (1, 0.5, 1.5, "một"), (2, 4.2, 5.0, "tràn sang tập sau"), (3, 5.0, 6.0, "hai")]


def test_pick_target_prefers_the_most_playing_time():
    small = {"v": {"width": 1}, "a": None, "duration": 10.0}
    big = {"v": {"width": 2}, "a": None, "duration": 100.0}
    assert comp.pick_target([small, small, big]) == 2
    assert comp.pick_target([small, big, small, small, {**small}]) == 1
    assert comp.pick_target([small, {**small}]) == 0


# --- sources from folders -------------------------------------------------------------

SRT = "1\n00:00:00,500 --> 00:00:01,500\nxin chào\n"


def _job(storage, jid, title, *, output=True, approve=True, stale=False) -> Job:
    from app.pipeline import approval

    j = Job(id=jid, work_dir=storage / jid)
    (j.work_dir / "metadata.json").write_text(json.dumps({"title": title}, ensure_ascii=False),
                                              encoding="utf-8")
    j.vi_srt.write_text(SRT, encoding="utf-8")
    if approve:
        approval.approve(j)
    if output:
        j.output_video.write_bytes(b"x")
        stamp = (j.work_dir / "approved.json").stat().st_mtime if approve else 0
        os.utime(j.output_video, (stamp + 5, stamp + 5))
    if stale:
        j.vi_srt.write_text(SRT.replace("xin chào", "đã sửa"), encoding="utf-8")
    return j


def test_source_states_from_project_folders(tmp_path):
    st = tmp_path / "storage"
    _job(st, "aaaaaaaaaaa1", "第3集 cảnh")
    _job(st, "aaaaaaaaaaa2", "Tập 4", stale=True)
    _job(st, "aaaaaaaaaaa3", "EP5", output=False)
    ok = comp.source_from_dir(st, "aaaaaaaaaaa1")
    assert ok.state == "ok" and ok.number == 3 and ok.chapters == [(0.0, "Tập 3")]
    assert ok.clips[0].srt.name == "vi.srt"
    assert comp.source_from_dir(st, "aaaaaaaaaaa2").state == "stale"
    miss = comp.source_from_dir(st, "aaaaaaaaaaa3")
    assert miss.state == "missing" and miss.clips == [] and "chưa dựng" in miss.note
    assert comp.source_from_dir(st, "nope") is None
    assert not (st / "nope").exists()                      # never creates folders


def test_a_split_video_uses_its_rendered_parts_when_not_joined(tmp_path):
    st = tmp_path / "storage"
    (st / "g").mkdir(parents=True)
    _job(st, "p1", "Dài · Phần 1/2")
    _job(st, "p2", "Dài · Phần 2/2", output=False)
    parts = [{"id": "p1", "index": 1}, {"id": "p2", "index": 2}]
    (st / "g" / "group.json").write_text(json.dumps({"id": "g", "title": "Dài 第9集",
                                                      "parts": parts}), encoding="utf-8")
    s = comp.source_from_dir(st, "g")
    assert s.kind == "group" and s.state == "missing" and "1/2" in s.note
    _job(st, "p2", "Dài · Phần 2/2")
    s = comp.source_from_dir(st, "g")
    assert s.state == "ok" and [c.video.parent.name for c in s.clips] == ["p1", "p2"]
    assert s.number == 9


# --- real ffmpeg ------------------------------------------------------------------------

def _ffmpeg():
    try:
        from app.ffmpegutil import ffmpeg_bin

        return ffmpeg_bin()
    except Exception:  # noqa: BLE001
        return None


needs_ffmpeg = pytest.mark.skipif(_ffmpeg() is None or shutil.which("ffprobe") is None,
                                  reason="ffmpeg not available")


def _render(path, dur: float, *, size="160x120", crf=20, rate=10) -> None:
    subprocess.run([_ffmpeg(), "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                    "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                    "-shortest", str(path)], check=True)


def _episode(st, jid, title, dur, **kw) -> Job:
    j = _job(st, jid, title)
    _render(j.output_video, dur, **kw)
    stamp = (j.work_dir / "approved.json").stat().st_mtime
    os.utime(j.output_video, (stamp + 5, stamp + 5))
    (j.work_dir / "render.json").write_text(json.dumps({"encoder": "libx264",
                                                        "preset": "sharp"}), encoding="utf-8")
    (j.work_dir / "output.srt").write_text(SRT.replace("xin chào", f"câu {title}"),
                                           encoding="utf-8")
    return j


def _chapters(path) -> list[tuple[float, str]]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
                       capture_output=True, text=True, encoding="utf-8")
    return [(round(float(c["start_time"]), 1), c["tags"]["title"])
            for c in json.loads(r.stdout)["chapters"]]


@needs_ffmpeg
def test_join_copies_matching_files_and_keeps_chapters_and_subtitles(tmp_path):
    from app.pipeline.assemble import probe_duration

    st = tmp_path / "storage"
    for k, dur in ((2, 3.0), (1, 2.0), (3, 2.0)):
        _episode(st, f"ep{k}", f"第{k}集", dur)
    srcs = comp.order([comp.source_from_dir(st, f"ep{k}") for k in (2, 1, 3)])
    out = comp.compile_videos(st / "c1", srcs, title=comp.default_title(srcs))
    assert abs(probe_duration(out) - 7.0) < 0.3
    assert _chapters(out) == [(0.0, "Tập 1"), (2.0, "Tập 2"), (5.0, "Tập 3")]
    srt = (st / "c1" / "vi.srt").read_text(encoding="utf-8")
    assert "câu 第2集" in srt and "00:00:02,500" in srt and "00:00:05,500" in srt
    assert (st / "c1" / "chapters.txt").read_text(encoding="utf-8").splitlines() == [
        "0:00 Tập 1", "0:02 Tập 2", "0:05 Tập 3"]
    info = json.loads((st / "c1" / comp.COMP_FILE).read_text(encoding="utf-8"))
    assert info["title"] == "Tổng hợp tập 1–3" and info["reencoded"] == 0
    assert [e["number"] for e in info["episodes"]] == [1, 2, 3]
    assert not (st / "c1" / comp.NORM_DIR).exists() and not (st / "c1" / comp.TMP_OUT).exists()

    # the compilation is a source for the next one: its three chapters come along
    _episode(st, "ep4", "Tập 4", 2.0)
    again = comp.order([comp.source_from_dir(st, "ep4"), comp.source_from_dir(st, "c1")])
    assert [s.id for s in again] == ["c1", "ep4"]
    out2 = comp.compile_videos(st / "c2", again, title="Tổng hợp tập 1–4")
    assert [n for _t, n in _chapters(out2)] == ["Tập 1", "Tập 2", "Tập 3", "Tập 4"]
    assert abs(_chapters(out2)[3][0] - 7.0) < 0.3
    info2 = json.loads((st / "c2" / comp.COMP_FILE).read_text(encoding="utf-8"))
    assert [e["number"] for e in info2["episodes"]] == [1, 2, 3, 4]


@needs_ffmpeg
def test_a_file_with_other_parameters_is_converted_alone(tmp_path):
    from app.pipeline.assemble import probe_duration

    st = tmp_path / "storage"
    _episode(st, "ep1", "Tập 1", 3.0)
    _episode(st, "ep2", "Tập 2", 2.0, size="320x180", crf=27)      # other size AND preset
    _episode(st, "ep3", "Tập 3", 3.0)
    srcs = [comp.source_from_dir(st, f"ep{k}") for k in (1, 2, 3)]
    out = comp.compile_videos(st / "c", srcs, title="x")
    assert abs(probe_duration(out) - 8.0) < 0.3
    info = json.loads((st / "c" / comp.COMP_FILE).read_text(encoding="utf-8"))
    assert 1 <= info["reencoded"] <= 3
    v = comp.probe(out)["v"]
    assert (v["width"], v["height"]) == (160, 120)                    # the majority's size
    # every frame decodes
    r = subprocess.run([_ffmpeg(), "-v", "error", "-i", str(out), "-f", "null", "-"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and not r.stderr.strip()


def test_missing_sources_are_refused(tmp_path):
    with pytest.raises(RuntimeError, match="chưa có video"):
        comp.compile_videos(tmp_path / "c", [_src("a", 1, state="missing")], title="x")
