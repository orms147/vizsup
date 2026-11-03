"""«Video từ kịch bản»: split the script, cycle the clips, and a real (tiny) end-to-end build
with a fake voice — ffmpeg only, no network, no model."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.ffmpegutil import ffmpeg_bin
from app.pipeline import assemble, scriptvideo as sv


def test_split_script_sentences_paragraphs_and_comments():
    text = "# ghi chú\nXin chào các bạn. Hôm nay mình kể chuyện!\n\nCâu hỏi là gì? Ừ.\nKết thúc…"
    lines = sv.split_script(text)
    assert lines[0] == "Xin chào các bạn."
    assert lines[1] == "Hôm nay mình kể chuyện!"
    assert lines[2] == "Câu hỏi là gì? Ừ."                        # "Ừ." joined, not alone
    assert lines[-1] == "Kết thúc…" and len(lines) == 4           # …but never across a paragraph


def test_a_long_sentence_is_cut_at_commas_then_spaces():
    long = ", ".join(f"đoạn số {i} dài dài dài" for i in range(30))
    parts = sv.split_script(long, max_chars=80)
    assert len(parts) > 4 and all(len(p) <= 80 for p in parts)
    assert " ".join(parts).replace(",", "").split() == long.replace(",", "").split()
    nospace = sv.split_script("a" * 250, max_chars=100)
    assert [len(p) for p in nospace] == [100, 100, 50]


def test_kind_of_wants_all_pictures_or_all_videos():
    assert sv.kind_of([Path("a.JPG")]) == "image"
    assert sv.kind_of([Path("a.mp4"), Path("b.mkv")]) == "video"
    for bad in ([], [Path("a.mp4"), Path("b.png")], [Path("a.txt")]):
        with pytest.raises(ValueError):
            sv.kind_of(bad)


def test_cycle_plays_in_order_and_goes_round_until_long_enough():
    assert sv.cycle([4.0, 6.0], 15.0) == [0, 1, 0, 1]              # 4+6+4 = 14 < 15 → one more
    assert sum([4.0, 6.0][i] for i in sv.cycle([4.0, 6.0], 15.0)) >= 15.0
    assert sv.cycle([5.0], 5.0) == [0]
    assert sv.cycle([0.0, 3.0], 7.0) == [1, 1, 1]                  # an unreadable clip is skipped
    assert sv.cycle([], 5.0) == [] and sv.cycle([3.0], 0) == []


class ToneVoice:
    name = "tone"
    parallel = 1

    def available(self):
        return True

    def voices(self):
        return ["v"]

    def synthesize(self, text, out_path, *, voice=None, rate=None):
        secs = 0.5 + 0.02 * len(text)
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={secs:.2f}", str(out_path)],
                       check=True, capture_output=True)
        return out_path


def _clip(path: Path, color: str, secs: float, size="320x240") -> Path:
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c={color}:s={size}:d={secs}:r=25", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)
    return path


@pytest.fixture()
def voice(monkeypatch):
    from app.providers import registry

    monkeypatch.setitem(registry.TTS_PROVIDERS, "tone", ToneVoice())
    monkeypatch.setenv("VIZSUP_VIDEO_ENCODER", "cpu")


def test_picture_background_lasts_as_long_as_the_voice(tmp_path, voice):
    pic = tmp_path / "bia.png"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360",
                    "-frames:v", "1", str(pic)], check=True, capture_output=True)
    out = sv.build(tmp_path / "w", "Câu thứ nhất của kịch bản. Câu thứ hai dài hơn một chút nữa!",
                   [pic], provider="tone", also_vertical=False)
    nar = assemble.probe_duration(tmp_path / "w" / "narration.wav")
    assert out.exists() and abs(assemble.probe_duration(out) - (nar + sv.TAIL)) < 0.25
    assert (tmp_path / "w" / "script.srt").read_text(encoding="utf-8").count("-->") == 2
    assert assemble.probe_size(out) == (1920, 1080)


def test_clips_are_cycled_to_the_voice_and_the_voice_is_reused(tmp_path, voice, monkeypatch):
    a, b = _clip(tmp_path / "a.mp4", "blue", 1.0), _clip(tmp_path / "b.mp4", "green", 1.5)
    script = "Một câu khá dài để đọc lâu hơn một chút. Câu nữa cũng dài như vậy đó. Và câu ba."
    w = tmp_path / "w"
    out = sv.build(w, script, [a, b], provider="tone", also_vertical=False)
    nar = assemble.probe_duration(w / "narration.wav")
    assert nar > 2.5                                               # longer than a+b: it went round
    assert abs(assemble.probe_duration(out) - (nar + sv.TAIL)) < 0.3
    cached = sorted(p.name for p in (w / "tts").glob("*.wav"))
    def never(*_a, **_k):
        raise AssertionError("spoken twice")

    monkeypatch.setattr(ToneVoice, "synthesize", never)
    sv.build(w, script, [a, b], provider="tone", also_vertical=False)
    assert sorted(p.name for p in (w / "tts").glob("*.wav")) == cached


def test_empty_script_and_mixed_backgrounds_are_refused(tmp_path, voice):
    pic = tmp_path / "x.png"
    pic.write_bytes(b"x")
    with pytest.raises(ValueError):
        sv.build(tmp_path / "w", "  \n# nothing", [pic], provider="tone")
    with pytest.raises(ValueError):
        sv.build(tmp_path / "w", "Có chữ.", [pic, tmp_path / "a.mp4"], provider="tone")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait_until(cond, qapp, secs=60):
    import time

    end = time.time() + secs
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.05)
    return cond()


def test_make_audio_writes_one_mp3_and_the_line_timings(tmp_path, voice):
    out = sv.make_audio(tmp_path / "w", "Câu thứ nhất của kịch bản. Câu thứ hai dài hơn một chút nữa!",
                        provider="tone")
    assert out.name == "narration.mp3" and out.stat().st_size > 1000
    assert assemble.probe_duration(out) > 1.5
    assert (tmp_path / "w" / "script.srt").read_text(encoding="utf-8").count("-->") == 2
    assert not (tmp_path / "w" / "output.mp4").exists()                 # no picture at all


def test_an_existing_audio_file_and_a_watermark_over_clips(tmp_path, voice):
    mp3 = sv.make_audio(tmp_path / "a", "Một câu đủ dài để có tiếng đọc lâu hơn một chút.", provider="tone")
    a, b = _clip(tmp_path / "a.mp4", "blue", 1.0), _clip(tmp_path / "b.mp4", "green", 1.0)
    plain = sv.build(tmp_path / "p", None, [a, b], audio=mp3)            # stream copy
    marked = sv.build(tmp_path / "m", None, [a, b], audio=mp3,
                      watermark={"on": True, "text": "KÊNH", "pos": "tr", "opacity": 60, "size": 20})
    want = assemble.probe_duration(mp3)
    assert abs(assemble.probe_duration(plain) - want) < 0.3
    assert abs(assemble.probe_duration(marked) - want) < 0.3
    assert (tmp_path / "m" / "mark.ass").exists() and not (tmp_path / "p" / "mark.ass").exists()
    assert assemble.probe_size(plain) == assemble.probe_size(marked) == (1920, 1080)


def test_the_first_frame_shows_the_watermark(tmp_path):
    pic = tmp_path / "bia.png"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=640x360",
                    "-frames:v", "1", str(pic)], check=True, capture_output=True)
    off = sv.still(tmp_path / "s", pic, None)
    assert assemble.probe_size(off) == (960, 540)
    off_bytes = off.read_bytes()
    on = sv.still(tmp_path / "s", pic, {"on": True, "text": "KÊNH", "pos": "tr", "opacity": 90, "size": 30})
    assert on.read_bytes() != off_bytes


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait_until(cond, qapp, secs=60):
    import time

    end = time.time() + secs
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.05)
    return cond()


def test_the_audio_page_makes_an_mp3_and_the_video_page_can_use_it(qapp, tmp_path, voice, monkeypatch):
    from app.config import settings
    from desktop import store
    from desktop.views.script_pages import AudioPage, ScriptVideoPage

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "storage")
    monkeypatch.setattr(store, "update_ui_state", lambda **k: None)
    page = AudioPage(tts=lambda: "tone")
    assert not page.go_btn.isEnabled()
    page.script.setPlainText("Đây là câu thứ nhất. Đây là câu thứ hai!")
    assert "2 câu" in page.count.text() and page.go_btn.isEnabled()
    seen = []
    page.busyChanged.connect(seen.append)
    page.go_btn.click()
    assert page.is_busy() and page.go_btn.text() == "Hủy"
    assert _wait_until(lambda: not page.is_busy(), qapp), page.status.text()
    assert page.result_path and page.result_path.suffix == ".mp3" and seen == [True, False]
    pic = tmp_path / "bia.png"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360",
                    "-frames:v", "1", str(pic)], check=True, capture_output=True)
    video = ScriptVideoPage(tts=lambda: "tone")
    assert not video.go_btn.isEnabled()
    video.set_audio(str(page.result_path))
    assert video._from_audio() and not video.voice_box.isVisibleTo(video) and not video.go_btn.isEnabled()
    video._set_backgrounds([pic])
    assert video.go_btn.isEnabled() and "Ảnh" in video.bg_hint.text()
    video.wm_on.setChecked(True)
    video.wm_text.setText("KÊNH")
    assert video.watermark()["on"] and video.watermark()["text"] == "KÊNH"
    video.go_btn.click()
    assert _wait_until(lambda: not video.is_busy(), qapp), video.status.text()
    assert video.result_path and video.result_path.exists() and "Xong" in video.status.text()
    assert _wait_until(lambda: video.frame_lbl.pixmap() is not None and not video.frame_lbl.pixmap().isNull(),
                       qapp, 20)                                   # the first frame was cut too


def test_both_pages_can_run_at_the_same_time(qapp, tmp_path, voice, monkeypatch):
    from app.config import settings
    from desktop import store
    from desktop.views.script_pages import AudioPage

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path / "storage")
    monkeypatch.setattr(store, "update_ui_state", lambda **k: None)
    one, two = AudioPage(tts=lambda: "tone"), AudioPage(tts=lambda: "tone")
    for p in (one, two):
        p.script.setPlainText("Một câu để đọc thử cho đủ dài. Và một câu nữa cũng vậy đó.")
        p.go_btn.click()
    assert one.is_busy() and two.is_busy()
    assert _wait_until(lambda: not one.is_busy() and not two.is_busy(), qapp), (one.status.text(), two.status.text())
    assert one.result_path.exists() and two.result_path.exists() and one.result_path != two.result_path
