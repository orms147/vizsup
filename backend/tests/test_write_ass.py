"""Regression gate for srt.write_ass (P0-1).

Locks the ASS output so the Phase C multi-style rewrite can't silently change the
no-override path. Run from backend/:  pytest tests/test_write_ass.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.models import Cue
from app.pipeline.srt import write_ass

CUES = [Cue(1, 0.0, 2.0, "Xin chao"), Cue(2, 2.0, 4.0, "Cau hai"), Cue(3, 4.0, 6.0, "Cau ba")]


def _ass(**kw) -> str:
    p = Path(tempfile.mkdtemp()) / "t.ass"
    write_ass(CUES, p, **kw)
    return p.read_text(encoding="utf-8")


def _style_line(text: str) -> str:
    return next(ln for ln in text.splitlines() if ln.startswith("Style: Default,"))


def test_default_structure():
    t = _ass()
    assert t.count("\nStyle: Default,") == 1          # exactly one style
    assert t.count("\nDialogue:") == 3                # one per cue
    assert "&H00FFFFFF" in _style_line(t)             # white primary
    assert "\\pos(" not in t and "\\fsp" not in t     # no inline overrides by default


def test_style_colour_bold_pos_fsp():
    t = _ass(style={"primary": "#FFD400", "bold": True, "pos": [0.5, 0.7], "letter_spacing": 3})
    assert "&H0000D4FF" in _style_line(t)             # #FFD400 → &HAABBGGRR opaque
    assert ",-1,0," in _style_line(t)                 # bold=-1, italic=0
    assert "{\\an5\\pos(960,756)}" in t               # 0.5*1920, 0.7*1080
    assert "\\fsp3" in t


def test_cover_hardsubs_forces_opaque_box():
    sl = _style_line(_ass(cover_hardsubs=True))
    # BorderStyle field == 4 (opaque box) regardless of style
    fields = sl[len("Style: "):].split(",")
    # 0 Name,1 Font,2 Size,3 Primary,4 Secondary,5 Outline,6 Back,7 Bold,8 Italic,
    # 9 Underline,10 StrikeOut,11 ScaleX,12 ScaleY,13 Spacing,14 Angle,15 BorderStyle
    assert fields[15] == "4"


def test_margins_in_style_line():
    sl = _style_line(_ass(style={"margin_l": 120, "margin_r": 130, "margin_v": 200}))
    fields = sl[len("Style: "):].split(",")
    # ...,Alignment,MarginL,MarginR,MarginV,Encoding
    assert fields[19:22] == ["120", "130", "200"]
