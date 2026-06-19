"""Design tokens + global stylesheet, ported from the "Dark Editor Design"
(Claude Design) mockups. Single source of truth for colors/fonts so every
widget stays on-brand.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

# --- palette (hex from the .dc.html design) --------------------------------
C = {
    "bg": "#0b0c10",          # window
    "panel": "#0c0e12",       # side rails / docks
    "bar": "#0d0f14",         # toolbar / status bar
    "card": "#0f1117",        # cards / inputs
    "card2": "#13161c",       # raised buttons
    "deep": "#08090c",        # log / waveform background
    "input": "#0a0b0e",       # focused input bg
    # borders
    "line": "#1c2029",
    "line2": "#20242d",
    "line3": "#262c36",
    "line4": "#2c313b",
    # text
    "text": "#e9eaee",
    "text_hi": "#eef0f3",
    "text2": "#d6d8df",
    "text3": "#c6c9d2",
    "muted": "#9aa0ac",
    "muted2": "#868b96",
    "muted3": "#727783",
    "muted4": "#5b606b",
    # accent
    "accent": "#8b5cf6",
    "accent2": "#7c3aed",
    "accent_lt": "#a78bfa",
    "accent_br": "#9466f0",
    # status
    "green": "#41c97a",
    "amber": "#f5b545",
    "red": "#f0594f",
    "coral": "#ef7a64",
    "douyin": "#ff2d55",
    "bili": "#00a1d6",
}

FONT_SANS = "Be Vietnam Pro"
FONT_MONO = "JetBrains Mono"
# Windows fallbacks that render Vietnamese diacritics well.
FONT_SANS_FB = "Segoe UI"
FONT_MONO_FB = "Consolas"

_ASSETS = Path(__file__).parent / "assets" / "fonts"


def load_fonts(app: QApplication) -> None:
    """Register bundled .ttf fonts if present (desktop/assets/fonts/*.ttf).
    Falls back silently to system fonts (Segoe UI / Consolas) when absent."""
    families: set[str] = set()
    if _ASSETS.exists():
        for ttf in _ASSETS.glob("*.ttf"):
            fid = QFontDatabase.addApplicationFont(str(ttf))
            for fam in QFontDatabase.applicationFontFamilies(fid):
                families.add(fam)
    installed = set(QFontDatabase.families())
    sans = FONT_SANS if (FONT_SANS in families or FONT_SANS in installed) else FONT_SANS_FB
    mono = FONT_MONO if (FONT_MONO in families or FONT_MONO in installed) else FONT_MONO_FB
    app.setProperty("vz_sans", sans)
    app.setProperty("vz_mono", mono)
    base = QFont(sans)
    base.setPointSize(10)  # explicit positive size; the default app font is pointSize -1 (warns)
    app.setFont(base)


def fonts() -> tuple[str, str]:
    app = QApplication.instance()
    sans = (app and app.property("vz_sans")) or FONT_SANS_FB
    mono = (app and app.property("vz_mono")) or FONT_MONO_FB
    return sans, mono


def qss() -> str:
    sans, mono = fonts()
    return f"""
* {{ outline: none; }}
QWidget {{
    background: {C['bg']};
    color: {C['text']};
    font-family: "{sans}";
    font-size: 13px;
}}
QToolTip {{ background:{C['card2']}; color:{C['text2']}; border:1px solid {C['line4']}; padding:4px 7px; }}

/* surfaces */
QFrame#toolbar, QFrame#statusbar {{ background:{C['bar']}; border:none; }}
QFrame#rail, QFrame#dock {{ background:{C['panel']}; }}
QFrame#card {{ background:{C['card']}; border:1px solid {C['line2']}; border-radius:11px; }}

/* headings / muted text via objectName */
QLabel#h1 {{ font-size:19px; font-weight:600; color:{C['text_hi']}; }}
QLabel#h2 {{ font-size:16px; font-weight:600; color:{C['text_hi']}; }}
QLabel#muted {{ color:{C['muted']}; }}
QLabel#section {{ color:{C['muted2']}; font-size:11px; font-weight:600; }}
QLabel#mono {{ font-family:"{mono}"; color:{C['text2']}; }}

/* buttons */
QPushButton {{
    background:{C['card2']}; color:{C['text2']};
    border:1px solid {C['line3']}; border-radius:8px;
    padding:7px 13px; font-weight:500;
}}
QPushButton:hover {{ background:#1a1f29; border-color:{C['line4']}; color:{C['text']}; }}
QPushButton:disabled {{ color:{C['muted4']}; background:#1c1f27; border-color:{C['line4']}; }}
QPushButton#primary {{
    background:{C['accent']}; color:#ffffff; border:1px solid {C['accent_br']}; font-weight:600;
}}
QPushButton#primary:hover {{ background:#9870f8; }}
QPushButton#primary:disabled {{ background:#1c1f27; color:{C['muted4']}; border-color:{C['line4']}; }}
QPushButton#ghost {{ background:transparent; border:1px solid {C['line4']}; }}
QPushButton#ghost:hover {{ background:#191c22; }}
QPushButton#danger:hover {{ background:rgba(240,89,79,0.14); border-color:rgba(240,89,79,0.4); color:{C['red']}; }}
QPushButton#step {{ background:transparent; border:none; color:{C['muted']}; padding:6px 10px; }}
QPushButton#stepActive {{ background:rgba(139,92,246,0.13); border:1px solid rgba(139,92,246,0.34);
    border-radius:8px; color:{C['text_hi']}; font-weight:600; padding:6px 12px; }}

/* inputs */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background:{C['card']}; color:{C['text']};
    border:1px solid {C['line3']}; border-radius:9px; padding:8px 11px;
    selection-background-color: rgba(139,92,246,0.35);
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color:{C['accent']}; }}
QLineEdit#mono, QPlainTextEdit#mono {{ font-family:"{mono}"; }}

/* combo */
QComboBox {{ background:{C['card']}; border:1px solid {C['line3']}; border-radius:10px; padding:7px 12px; min-height:22px; }}
QComboBox:hover {{ border-color:{C['line4']}; }}
QComboBox:focus {{ border-color:{C['accent']}; }}
QComboBox::drop-down {{ border:none; width:26px; }}
QComboBox QAbstractItemView {{
    background:{C['card2']}; border:1px solid {C['line4']}; border-radius:8px;
    selection-background-color: rgba(139,92,246,0.25); outline:none;
}}
QComboBox QAbstractItemView::item {{ min-height:26px; padding:5px 10px; border:none; color:{C['text']}; }}
QComboBox QAbstractItemView::item:disabled {{ color:{C['muted4']}; }}
QComboBox QAbstractItemView::item:selected {{ background: rgba(139,92,246,0.25); color:#ffffff; }}

/* table */
QTableView {{
    background:{C['bg']}; border:none; gridline-color:transparent;
    selection-background-color: rgba(139,92,246,0.10);
    alternate-background-color: {C['bg']};
}}
QTableView::item {{ padding:6px 8px; border-bottom:1px solid #14171d; }}
QTableView::item:selected {{ background: rgba(139,92,246,0.10); color:{C['text']}; }}
QHeaderView::section {{
    background:{C['deep']}; color:{C['muted2']};
    border:none; border-bottom:1px solid {C['line']};
    padding:8px 10px; font-size:11px; font-weight:600;
}}

/* slider */
QSlider::groove:horizontal {{ height:4px; background:{C['line3']}; border-radius:3px; }}
QSlider::sub-page:horizontal {{ background:{C['accent_lt']}; border-radius:3px; }}
QSlider::handle:horizontal {{ width:15px; height:15px; margin:-6px 0; border-radius:8px;
    background:{C['accent_lt']}; border:2px solid {C['card']}; }}

/* progress */
QProgressBar {{ background:#15181f; border:none; border-radius:4px; height:6px; text-align:center; color:transparent; }}
QProgressBar::chunk {{ background:{C['accent_lt']}; border-radius:4px; }}

/* scrollbars */
QScrollBar:vertical {{ background:transparent; width:11px; margin:0; }}
QScrollBar::handle:vertical {{ background:{C['line4']}; border-radius:5px; min-height:30px; }}
QScrollBar::handle:vertical:hover {{ background:#3c424e; }}
QScrollBar:horizontal {{ background:transparent; height:11px; margin:0; }}
QScrollBar::handle:horizontal {{ background:{C['line4']}; border-radius:5px; min-width:30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}

/* checkbox / radio */
QCheckBox::indicator, QRadioButton::indicator {{ width:18px; height:18px; }}
QCheckBox::indicator {{ border:1px solid {C['line4']}; border-radius:6px; background:transparent; }}
QCheckBox::indicator:checked {{ background:{C['accent']}; border-color:{C['accent']}; }}
QRadioButton::indicator {{ border:1px solid #3a4150; border-radius:9px; background:transparent; }}
QRadioButton::indicator:checked {{ background:{C['accent']}; border-color:{C['accent']}; }}
"""
