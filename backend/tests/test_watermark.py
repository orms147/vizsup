"""The translator's own watermark (2026-10-28): any text, faint, the whole video, in a
corner or hopping corners every 30 s; set on the render page, remembered for every
video, shown on the preview and burned by the render (real ffmpeg + libass)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import Cue  # noqa: E402
from app.pipeline import disclosure  # noqa: E402
from app.pipeline.srt import write_ass  # noqa: E402

WM = {"on": True, "text": "Vietsub: Kênh Của Tôi", "pos": "tr", "opacity": 40, "size": 14}


def test_events_fixed_corner_and_hopping():
    assert disclosure.watermark_events({**WM, "on": False}, 60) == []
    assert disclosure.watermark_events({**WM, "text": "  "}, 60) == []
    (one,) = disclosure.watermark_events(WM, 75.0)
    assert one == {"start": 0.0, "end": 75.0, "text": "Vietsub: Kênh Của Tôi", "align": 9}
    hop = disclosure.watermark_events({**WM, "pos": "move"}, 75.0)
    assert [(e["start"], e["end"], e["align"]) for e in hop] == [
        (0.0, 30.0, 9), (30.0, 60.0, 3), (60.0, 75.0, 1)]


def test_ass_has_a_faint_mark_style_below_the_corner_notes(tmp_path):
    out = tmp_path / "s.ass"
    events = disclosure.watermark_events(WM, 10.0)
    write_ass([Cue(1, 0.0, 1.0, "xin chào")], out, mark=events, mark_look=WM,
              extra=[{"start": 0.0, "end": 5.0, "text": "Lồng tiếng AI", "align": 9}])
    text = out.read_text(encoding="utf-8")
    style = next(ln for ln in text.splitlines() if ln.startswith("Style: Mark,"))
    alpha = round(255 * 0.6)                              # 40 % visible → 60 % clear
    assert style.split(",")[3] == f"&H{alpha:02X}FFFFFF"
    mark = next(ln for ln in text.splitlines() if ",Mark,," in ln)
    assert "{\\an9}{\\pos(1880,170)}Vietsub: Kênh Của Tôi" in mark
    assert mark.startswith("Dialogue: 2,0:00:00.00,0:00:10.00")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_render_page_remembers_it_and_sends_it(qapp, tmp_path, monkeypatch):
    from app.config import settings
    from desktop import store
    from desktop.views.render_view import RenderView

    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    v = RenderView()
    assert v.collect_opts()["watermark"] is None           # off by default
    v.wm_cb.setChecked(True)
    v.wm_text.setText("Vietsub: Kênh Của Tôi")
    v.wm_pos.setCurrentIndex(v.wm_pos.findData("move"))
    v.wm_opacity.setValue(30)
    sent = v.collect_opts()["watermark"]
    assert sent == {"on": True, "text": "Vietsub: Kênh Của Tôi", "pos": "move", "opacity": 30,
                    "size": 14}
    assert store.load_ui_state()["watermark"] == sent
    assert v.preview._canvas._wm == sent                   # shown on the preview
    v.shutdown()
    again = RenderView()                                   # the next video starts with it
    assert again.watermark() == sent
    again.shutdown()


def _ffmpeg():
    try:
        from app.ffmpegutil import ffmpeg_bin

        return ffmpeg_bin()
    except Exception:  # noqa: BLE001
        return None


@pytest.mark.skipif(_ffmpeg() is None or shutil.which("ffprobe") is None, reason="ffmpeg")
def test_the_render_burns_it_faintly_in_the_corner(tmp_path):
    from app.pipeline.assemble import _ass_fonts

    ass = tmp_path / "s.ass"
    write_ass([], ass, mark=disclosure.watermark_events({**WM, "opacity": 100, "size": 24}, 2.0),
              mark_look={**WM, "opacity": 100, "size": 24})
    fonts = _ass_fonts(tmp_path, "Be Vietnam Pro")

    def frame(name, vf):
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x404040:s=1920x1080:d=1", "-vf", vf, "-frames:v", "1",
                        name], cwd=tmp_path, check=True)
        return tmp_path / name

    plain = frame("a.png", "null")
    burned = frame("b.png", f"ass=s.ass{fonts}")
    from PySide6.QtGui import QImage

    a, b = QImage(str(plain)), QImage(str(burned))

    def bright(img, x0, y0, x1, y1):
        return max(img.pixelColor(x, y).lightness() for x in range(x0, x1, 4)
                   for y in range(y0, y1, 4))

    assert bright(b, 1300, 170, 1880, 260) > bright(a, 1300, 170, 1880, 260) + 100  # top right
    assert bright(b, 40, 170, 600, 260) == bright(a, 40, 170, 600, 260)             # not left
