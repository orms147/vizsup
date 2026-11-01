"""Ảnh bìa tự động (pipeline/thumbnail.py): a 1280×720 cover for every finished video — the
best frame, the series' Vietnamese name big, the episode chip; never over the owner's own."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.ffmpegutil import ffmpeg_bin  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline import autopilot, thumbnail  # noqa: E402

Image = pytest.importorskip("PIL.Image")


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    return tmp_path


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    """A 4-s horizontal and a 4-s vertical test clip (made once)."""
    d = tmp_path_factory.mktemp("clips")
    out = {}
    for name, size in (("h", "320x180"), ("v", "180x320")):
        p = d / f"{name}.mp4"
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc=size={size}:rate=10:duration=4", "-f", "lavfi", "-i",
                        "sine=duration=4", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(p)], check=True)
        out[name] = p
    return out


def _project(storage: Path, clips, name="ep9", title="第7集 | 《末日校车》09 我一个普通大巴司机",
             kind="h") -> Path:
    d = storage / name
    d.mkdir()
    (d / "metadata.json").write_text(json.dumps({"title": title}, ensure_ascii=False),
                                     encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "末日校车"}), encoding="utf-8")
    shutil.copyfile(clips[kind], d / "output.mp4")
    return d


def _jpeg_size(p: Path) -> tuple[int, int]:
    assert p.read_bytes()[:3] == b"\xff\xd8\xff"
    with Image.open(p) as im:
        assert im.format == "JPEG"
        return im.size


# --- scoring (pure) -----------------------------------------------------------------------

def _checker(n=8, size=64, colors=((230, 40, 40), (30, 30, 200))) -> np.ndarray:
    y, x = np.mgrid[:size, :size]
    mask = ((y // n + x // n) % 2).astype(bool)
    img = np.zeros((size, size, 3), np.uint8)
    img[mask], img[~mask] = colors[0], colors[1]
    return img


def test_black_white_and_flat_frames_are_skipped():
    for value in (0, 8, 250):
        assert not thumbnail.usable(thumbnail.frame_stats(np.full((40, 60, 3), value, np.uint8)))
    flat = np.full((40, 60, 3), 120, np.uint8)
    assert not thumbnail.usable(thumbnail.frame_stats(flat))            # a blank title card
    assert thumbnail.usable(thumbnail.frame_stats(_checker()))


def test_a_crisp_frame_beats_a_blurred_one():
    sharp = _checker()
    soft = sharp.astype(np.float32)
    for _ in range(6):                                                   # box blur
        soft = (soft + np.roll(soft, 1, 0) + np.roll(soft, -1, 0)
                + np.roll(soft, 1, 1) + np.roll(soft, -1, 1)) / 5
    s1, s2 = thumbnail.frame_stats(sharp), thumbnail.frame_stats(soft.astype(np.uint8))
    assert s1.sharpness > s2.sharpness
    assert thumbnail.score(s1) > thumbnail.score(s2)
    grey = thumbnail.frame_stats(_checker(colors=((150, 150, 150), (90, 90, 90))))
    assert thumbnail.score(s1) > thumbnail.score(grey)                   # colour counts too


def test_rank_keeps_usable_frames_best_first_and_never_returns_nothing():
    good, black = thumbnail.frame_stats(_checker()), thumbnail.frame_stats(
        np.zeros((20, 20, 3), np.uint8))
    plain = thumbnail.frame_stats(_checker(colors=((150, 150, 150), (90, 90, 90))))
    r = thumbnail.rank([(1.0, black), (2.0, plain), (3.0, good)], k=6)
    assert [t for t, _ in r] == [3.0, 2.0]                               # black skipped
    assert [t for t, _ in thumbnail.rank([(1.0, black), (5.0, black)], k=1)] == [1.0]
    near = [(5.1, good), (5.2, good), (9.0, plain)]       # two samples in one pause
    assert [t for t, _ in thumbnail.rank(near, k=6, min_gap=1.0)] == [5.1, 9.0]


def test_samples_spread_over_the_middle_and_avoid_subtitles():
    ts = thumbnail.sample_times(100.0, 20)
    assert len(ts) == 20 and ts[0] == pytest.approx(8.0) and ts[-1] == pytest.approx(92.0)
    # a line on screen 40–45 s: a sample at 40.9 moves to the gap just before it
    ts = thumbnail.sample_times(100.0, 1, [(40.0, 45.0)], span=(0.409, 0.409))
    assert ts == [39.9]
    # no gap within reach (dialogue wall to wall): the sample stays where it was
    wall = [(i * 1.0, i * 1.0 + 1.0) for i in range(100)]
    assert thumbnail.sample_times(100.0, 1, wall, span=(0.5, 0.5)) == [50.0]
    assert thumbnail.sample_times(0, 20) == []


def test_title_words_chip_and_layout():
    assert thumbnail.clean_title("第7集 | 《末日校车》09 我一个普通大巴司机 #动画") == ""
    assert thumbnail.clean_title("Tổng hợp tập 1–9 Chuyến xe #vietsub") == "Tổng hợp Chuyến xe"
    assert thumbnail.clean_title("第3集 Tổng hợp 12 cảnh hay") == "Tổng hợp 12 cảnh hay"
    assert thumbnail.clean_title("12. Chuyến xe EP12") == "Chuyến xe"
    assert thumbnail.chip_text(9) == "TẬP 9"
    assert thumbnail.chip_text(1, 5) == "TẬP 1–5"
    assert thumbnail.chip_text(1, 9, joined=True) == "FULL 1–9"
    assert thumbnail.chip_text(None) == ""

    def measure(s: str, px: int) -> float:              # every character 0.5 em wide
        return len(s) * px * 0.5

    px, lines = thumbnail.layout_title("CHUYẾN XE TẬN THẾ", measure, max_w=1100)
    assert lines == ["CHUYẾN XE TẬN THẾ"] and px == 126   # one line, no lone "THẾ" below
    px, lines = thumbnail.layout_title("NGƯỜI ĐẸP LẤY CHỒNG GIÀU NHẤT THẾ GIỚI VÀ MÀN TRẢ THÙ",
                                       measure, max_w=1100)
    assert lines == ["NGƯỜI ĐẸP LẤY CHỒNG GIÀU", "NHẤT THẾ GIỚI VÀ MÀN TRẢ THÙ"]   # even
    px, lines = thumbnail.layout_title("MỘT " * 20, measure, max_w=1100)
    assert 2 <= len(lines) <= 3
    px, lines = thumbnail.layout_title("A" * 200, measure, max_w=1100)
    assert lines[-1].endswith("…") and all(measure(s, px) <= 1100 for s in lines)


@pytest.mark.parametrize("opts,made", [({}, True), ({"thumbnail": True}, True),
                                       ({"thumbnail": False}, False),
                                       ({"preview": True}, False)])   # the quick proof: never
def test_a_render_makes_one_unless_turned_off(opts, made):
    assert thumbnail.wanted(opts) is made


# --- words from the series / the join ------------------------------------------------------

def test_default_text_uses_the_series_vietnamese_name(storage, clips):
    d = _project(storage, clips)
    assert thumbnail.default_text(d) == ("", "TẬP 9")       # Chinese title, no name yet
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "series_name": "Chuyến Xe Tận Thế"})
    assert thumbnail.default_text(d) == ("Chuyến Xe Tận Thế", "TẬP 9")
    comp = storage / "c1"
    comp.mkdir()
    (comp / "compilation.json").write_text(json.dumps({
        "title": "Tổng hợp tập 1–9",
        "episodes": [{"id": "ep9", "number": 1, "last": 5}, {"id": "x", "number": 6,
                                                            "last": 9}]}), encoding="utf-8")
    assert thumbnail.default_text(comp) == ("Chuyến Xe Tận Thế", "FULL 1–9")


# --- the picture (real ffmpeg + Pillow) -------------------------------------------------------

def test_make_writes_a_1280x720_jpeg_and_records_it(storage, clips):
    d = _project(storage, clips)
    out = thumbnail.make(d, title="Chuyến Xe Tận Thế", chip="TẬP 9")
    assert out == d / "thumb.jpg" and _jpeg_size(out) == (1280, 720)
    assert out.stat().st_size < 2_000_000                     # YouTube's limit
    info = thumbnail.load_info(d)
    assert info["auto"] is True and info["source"] == "frame" and info["chip"] == "TẬP 9"
    assert 0.3 <= info["time"] <= 3.7 and info["candidates"][0] == info["time"]
    assert thumbnail.cover(d) == out


def test_a_vertical_video_sits_on_blurred_bars(storage, clips):
    d = _project(storage, clips, kind="v")
    out = thumbnail.make(d, title="", chip="")
    assert _jpeg_size(out) == (1280, 720)
    with Image.open(out) as im:
        px = np.asarray(im.convert("RGB"), dtype=np.float32)
    # the picture sits whole in the middle; the bars are a dimmed, blurred copy of it
    mid, bar = px[100:300, 600:680], px[100:300, 40:120]
    assert bar.std() < mid.std()


def test_compose_draws_the_vietnamese_title(tmp_path):
    frame = np.full((180, 320, 3), 90, np.uint8)
    plain = thumbnail.compose(frame, tmp_path / "a.jpg")
    titled = thumbnail.compose(frame, tmp_path / "b.jpg", title="Người đẹp lấy chồng",
                               chip="FULL 1–9")
    a = np.asarray(Image.open(plain).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(titled).convert("RGB"), dtype=np.int16)
    yellow = (b[..., 0] > 200) & (b[..., 1] > 180) & (b[..., 2] < 90)
    assert yellow[360:].sum() > 5000 and yellow[:300].sum() == 0    # big, at the bottom
    diff = np.abs(a - b)
    assert diff[420:, 56:500].mean() > 30 > diff[:300].mean()       # from the left margin, low
    assert diff[560:, 1170:].mean() < 3       # YouTube's duration badge corner stays clear
    assert thumbnail.title_font("NGƯỜI ĐẸP") == "Bangers-Regular.ttf"
    assert thumbnail.title_font("中文") == "BeVietnamPro-Bold.ttf"   # no glyph: the fallback


# --- whose it is ------------------------------------------------------------------------------

def test_the_automatic_pass_never_replaces_the_owners_choice(storage, clips, tmp_path):
    d = _project(storage, clips)
    logs: list[str] = []
    assert thumbnail.make_auto(d, log=logs.append) == d / "thumb.jpg"
    assert any("Ảnh bìa" in m for m in logs)
    own = tmp_path / "mine.png"
    Image.new("RGB", (600, 800), (10, 200, 30)).save(own)
    thumbnail.use_image(d, own)
    assert _jpeg_size(d / "thumb.jpg") == (1280, 720)
    before = (d / "thumb.jpg").read_bytes()
    assert thumbnail.user_chosen(d)
    assert thumbnail.make_auto(d, log=logs.append) is None
    assert (d / "thumb.jpg").read_bytes() == before and "Giữ ảnh bìa" in logs[-1]
    # a picked frame / edited title counts as the owner's too
    thumbnail.make(d, t=1.0, title="Khác", chip="TẬP 9", auto=False)
    assert thumbnail.user_chosen(d) and thumbnail.make_auto(d) is None


def test_the_librarys_old_small_frame_is_not_a_cover(storage, clips):
    d = _project(storage, clips)
    Image.new("RGB", (120, 68), (0, 0, 0)).save(d / "thumb.jpg")       # the old list cache
    assert thumbnail.cover(d) is None and not thumbnail.user_chosen(d)
    assert thumbnail.make_auto(d) == d / "thumb.jpg"
    assert _jpeg_size(d / "thumb.jpg") == (1280, 720)
    big = storage / "hand"
    big.mkdir()
    Image.new("RGB", (1280, 720), (0, 0, 0)).save(big / "thumb.jpg")   # put there by hand
    assert thumbnail.user_chosen(big) and thumbnail.cover(big) == big / "thumb.jpg"


def test_a_failure_only_warns_and_a_cancel_skips_it(storage, clips):
    d = storage / "empty"
    d.mkdir()
    logs: list[str] = []
    assert thumbnail.make_auto(d, log=logs.append) is None
    assert logs and logs[-1].startswith("⚠ Chưa tạo được ảnh bìa")
    v = _project(storage, clips)
    assert thumbnail.make_auto(v, log=logs.append, cancel=lambda: True) is None
    assert "đã hủy" in logs[-1] and not (v / "thumb.jpg").exists()


def test_the_post_sends_only_a_real_cover(storage, clips):
    d = _project(storage, clips)
    job = Job(id="ep9", work_dir=d, metadata={"title": "第7集 | 《末日校车》09"})
    Image.new("RGB", (120, 68)).save(d / "thumb.jpg")
    assert autopilot.post_meta(job, "long", dict(autopilot.DEFAULT_PLAN)).thumbnail is None
    thumbnail.make(d)
    assert autopilot.post_meta(job, "long", dict(autopilot.DEFAULT_PLAN)).thumbnail == \
        d / "thumb.jpg"


# --- the render hook ----------------------------------------------------------------------

def _approved(storage: Path, clips) -> Job:
    from app.pipeline import approval

    d = _project(storage, clips)
    job = Job(id="ep9", work_dir=d, metadata={"title": "第7集 | 《末日校车》09"})
    (d / "output.mp4").unlink()
    job.vi_srt.write_text("1\n00:00:00,500 --> 00:00:01,500\nxin chào\n", encoding="utf-8")
    approval.approve(job)
    return job


@pytest.fixture()
def fake_render(monkeypatch, clips):
    """render_job with the heavy steps faked: assemble 'writes' output.mp4 from the clip."""
    from app.pipeline import assemble, review

    def assemble_(job, segments, **kw):
        shutil.copyfile(clips["h"], job.work_dir / "output.mp4")
        return job.work_dir / "output.mp4"

    monkeypatch.setattr(assemble, "assemble", assemble_)
    monkeypatch.setattr(review, "stamp_fit_report", lambda *a, **k: None)


def test_the_render_makes_the_cover_only_when_wanted(storage, clips, fake_render):
    from app.pipeline import render

    job = _approved(storage, clips)
    render.render_job(job, {"audio_mode": "original", "thumbnail": False})
    assert not (job.work_dir / "thumb.jpg").exists()
    render.render_job(job, {"audio_mode": "original", "preview": True})
    assert not (job.work_dir / "thumb.jpg").exists()
    logs: list[str] = []
    render.render_job(job, {"audio_mode": "original"}, log=logs.append)   # default: on
    assert _jpeg_size(job.work_dir / "thumb.jpg") == (1280, 720)
    assert thumbnail.load_info(job.work_dir)["auto"] is True
    assert any(m.startswith("✓ Ảnh bìa") for m in logs)


def test_a_rerender_keeps_the_owners_cover_and_a_broken_one_never_fails_it(
        storage, clips, fake_render, monkeypatch, tmp_path):
    from app.pipeline import render

    job = _approved(storage, clips)
    own = tmp_path / "mine.jpg"
    Image.new("RGB", (1280, 720), (200, 0, 0)).save(own)
    thumbnail.use_image(job.work_dir, own)
    before = (job.work_dir / "thumb.jpg").read_bytes()
    render.render_job(job, {"audio_mode": "original"})
    assert (job.work_dir / "thumb.jpg").read_bytes() == before
    (job.work_dir / "thumb.json").unlink()
    (job.work_dir / "thumb.jpg").unlink()

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(thumbnail, "candidates", boom)
    logs: list[str] = []
    out = render.render_job(job, {"audio_mode": "original"}, log=logs.append)
    assert out.exists() and any("ffmpeg exploded" in m for m in logs)


# --- the dialog (offscreen) --------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait(cond, n: int = 1500) -> None:
    import time

    from PySide6.QtWidgets import QApplication

    for _ in range(n):
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_the_dialog_picks_a_frame_edits_the_words_and_takes_an_own_picture(
        qapp, storage, clips, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from desktop.widgets.thumb_dialog import ThumbDialog

    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "series_name": "Chuyến Xe Tận Thế"})
    d = _project(storage, clips)
    thumbnail.make_auto(d)
    dlg = ThumbDialog(d)
    assert not dlg.preview.pixmap().isNull() and "tự tạo" in dlg.state.text()
    assert (dlg.title_edit.text(), dlg.chip_edit.text()) == ("Chuyến Xe Tận Thế", "TẬP 9")
    _wait(lambda: bool(dlg._cands))
    buttons = list(dlg._buttons_by_t.values())
    assert 1 < len(buttons) <= thumbnail.CHOICES
    assert buttons[0].isChecked() and not any(b.isChecked() for b in buttons[1:])
    # another frame: drawn at once, now the owner's
    other = dlg._cands[1].t
    buttons[1].click()
    _wait(lambda: not dlg._busy)
    info = thumbnail.load_info(d)
    assert info["time"] == pytest.approx(other) and info["auto"] is False
    assert buttons[1].isChecked() and "đã chọn" in dlg.state.text()
    # the best frame and the default words again = automatic again
    dlg.title_edit.setText("Chuyến Xe Tận Thế")
    buttons[0].click()
    _wait(lambda: not dlg._busy)
    assert thumbnail.load_info(d)["auto"] is True
    # an edited title
    dlg.title_edit.setText("Tài xế xe buýt")
    dlg.redo()
    _wait(lambda: not dlg._busy)
    assert thumbnail.load_info(d)["title"] == "Tài xế xe buýt" and thumbnail.user_chosen(d)
    # the owner's own picture
    own = tmp_path / "mine.png"
    Image.new("RGB", (900, 900), (20, 20, 220)).save(own)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(own), ""))
    dlg.own_btn.click()
    _wait(lambda: not dlg._busy)
    assert thumbnail.load_info(d)["source"] == "image" and "của bạn" in dlg.state.text()
    assert _jpeg_size(d / "thumb.jpg") == (1280, 720)
    dlg.close()


def test_the_dialog_without_a_video_says_so(qapp, storage):
    from desktop.widgets.thumb_dialog import ThumbDialog

    d = storage / "none"
    d.mkdir()
    dlg = ThumbDialog(d)
    assert "Chưa có video" in dlg.finding.text() and not dlg.redo_btn.isEnabled()
    assert "Chưa có ảnh bìa" in dlg.preview.text()
    dlg.close()


def test_the_render_page_offers_it_and_saves_the_cover_with_the_video(
        qapp, storage, clips, monkeypatch, tmp_path):
    from desktop import store
    from desktop.views.render_view import RenderView

    v = RenderView()
    assert v.thumb_cb.isChecked() and v.collect_opts()["thumbnail"] is True
    v.thumb_cb.setChecked(False)
    assert store.load_ui_state()["render_thumbnail"] is False
    assert v.collect_opts()["thumbnail"] is False and not thumbnail.wanted(v.collect_opts())
    v.thumb_cb.setChecked(True)
    assert v.thumb_btn.parent() is v.save_btn.parent()           # the result pane's row
    d = _project(storage, clips)
    thumbnail.make(d)
    shutil.copyfile(clips["v"], d / "output.vertical.mp4")      # "Dựng thêm bản dọc 9:16"
    v._out, v._showing_quick = str(d / "output.mp4"), False
    dest = tmp_path / "out" / "Tập 9.mp4"
    dest.parent.mkdir()
    monkeypatch.setattr(v, "_ask_dest", lambda *a: dest)
    v._save_video()
    _wait(lambda: "Đã lưu" in v.res_status.text())
    assert dest.exists() and _jpeg_size(dest.with_suffix(".jpg")) == (1280, 720)
    assert (dest.parent / "Tập 9 (dọc 9x16).mp4").exists()      # the 9:16 copy still goes too
    assert "bản dọc 9:16" in v.res_status.text() and "ảnh bìa" in v.res_status.text()
    v._show_result_kind(quick=True)
    assert not v.thumb_btn.isEnabled()
    v._tick.stop()
