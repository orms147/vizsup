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


def test_long_lines_wrap_like_the_editor_preview():
    """The editor word-wraps inside the subtitle box; WrapStyle 2 (no wrapping) made
    long lines run off both edges of vertical videos in the burned MP4 (2026-10-13)."""
    assert "\nWrapStyle: 0\n" in _ass()


def test_dialogue_fields_match_the_format_line():
    """Parsed the way libass does (split by the Format fields, Text = the rest): the
    Format line once lacked Name, so every burned line started with a stray comma."""
    t = _ass()
    fmt = next(ln for ln in t.splitlines() if ln.startswith("Format: Layer"))
    n = len(fmt.split(":", 1)[1].split(","))
    texts = [ln.split(":", 1)[1].split(",", n - 1)[-1]
             for ln in t.splitlines() if ln.startswith("Dialogue:")]
    assert texts == [c.text for c in CUES]


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


# --- C-1: multi-style (cue_styles) ------------------------------------------

def test_cue_styles_none_byte_identical():
    assert _ass(style={"primary": "#FF0000"}) == _ass(style={"primary": "#FF0000"}, cue_styles=None)


def test_cue_styles_routes_selected_line():
    t = _ass(cue_styles={2: {"primary": "#FFD400", "bold": True}})
    assert t.count("\nStyle: ") == 2 and "Style: Sub1," in t   # Default + Sub1
    dia = [ln for ln in t.splitlines() if ln.startswith("Dialogue:")]
    assert ",Default,," in dia[0] and ",Sub1,," in dia[1] and ",Default,," in dia[2]
    sub1 = next(ln for ln in t.splitlines() if ln.startswith("Style: Sub1,"))
    assert "&H0000D4FF" in sub1 and ",-1,0," in sub1            # Sub1 = yellow + bold
    assert "&H00FFFFFF" in _style_line(t)                       # Default stays white


def test_cue_styles_dedup_identical_overrides():
    t = _ass(cue_styles={1: {"bold": True}, 3: {"bold": True}})
    assert t.count("\nStyle: ") == 2                            # Default + one deduped Sub
    dia = [ln for ln in t.splitlines() if ln.startswith("Dialogue:")]
    assert ",Sub1,," in dia[0] and ",Default,," in dia[1] and ",Sub1,," in dia[2]
