"""Design tokens + global stylesheet for the v2 design (appdesign/v2, 2026-10-02).

Single source of truth for colors / fonts / sizes so every widget stays on-brand.
Token values come from the top of appdesign/v2/design.css and qt-handoff.md. The
legacy v1 keys (card2, line3, muted2, accent_lt …) are kept as ALIASES onto the v2
values, so widgets not yet ported keep working while they look v2.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

# --- v2 palette ----------------------------------------------------------------
V2 = {
    "bg": "#101116",          # app background
    "nav": "#14151b",         # top app bar + status bar
    "panel": "#17181f",       # panels / cards
    "raised": "#1e1f28",      # floating / raised controls
    "input": "#111218",       # text fields
    "line": "#2d2e3a",        # borders
    "line_soft": "#24252f",   # panel borders, dividers
    "field_line": "#3a3a48",  # input borders
    "text": "#eeeef5",
    "secondary": "#b4b4c4",
    "muted": "#9495aa",
    "dim": "#5d5e6e",         # disabled
    "purple": "#9868f4",
    "purple_hover": "#a979ff",
    "purple_bright": "#bb99ff",
    "purple_soft": "#292039",
    "purple_line": "#594377",
    "green": "#85d3ac",
    "green_soft": "#1c3029",
    "amber": "#e9bd78",
    "amber_soft": "#32281e",
    "red": "#ed939c",
    "red_soft": "#38242c",
    "red_line": "#68404a",
    "kbd_bg": "#22232b",
    "kbd_line": "#3b3b48",
    "kbd_text": "#c9c7d8",
    "scroll": "#41424f",
    "track": "#46424f",
}

# legacy keys (v1 names) → v2 values; remove once every widget is ported
C = {
    **V2,
    "bar": V2["nav"], "card": V2["panel"], "card2": V2["raised"], "deep": V2["input"],
    "line2": V2["line_soft"], "line3": V2["line"], "line4": V2["field_line"],
    "text_hi": V2["text"], "text2": V2["text"], "text3": V2["secondary"],
    "muted2": V2["muted"], "muted3": V2["muted"], "muted4": V2["dim"],
    "accent": V2["purple"], "accent2": V2["purple"], "accent_lt": V2["purple_bright"],
    "accent_br": V2["purple"], "coral": V2["red"], "douyin": "#ff2d55", "bili": "#00a1d6",
}

FONT_SANS = "Be Vietnam Pro"
FONT_MONO = "JetBrains Mono"
FONT_CJK = "Microsoft YaHei"            # Chinese reference text (Windows); Noto Sans SC elsewhere
FONT_SANS_FB = "Segoe UI"
FONT_MONO_FB = "Consolas"

_ASSETS = Path(__file__).parent / "assets" / "fonts"


def load_fonts(app: QApplication) -> None:
    """Register the bundled .ttf fonts (desktop/assets/fonts, OFL). Falls back to
    Segoe UI / Consolas when absent."""
    families: set[str] = set()
    if _ASSETS.exists():
        for ttf in _ASSETS.glob("*.ttf"):
            fid = QFontDatabase.addApplicationFont(str(ttf))
            for fam in QFontDatabase.applicationFontFamilies(fid):
                families.add(fam)
    installed = set(QFontDatabase.families())
    sans = FONT_SANS if (FONT_SANS in families or FONT_SANS in installed) else FONT_SANS_FB
    mono = FONT_MONO if (FONT_MONO in families or FONT_MONO in installed) else FONT_MONO_FB
    cjk = next((f for f in (FONT_CJK, "Noto Sans SC", "Microsoft JhengHei", "SimHei")
                if f in installed), sans)
    app.setProperty("vz_sans", sans)
    app.setProperty("vz_mono", mono)
    app.setProperty("vz_cjk", cjk)
    base = QFont(sans)
    base.setPixelSize(13)
    # Chinese glyphs missing from Be Vietnam Pro fall back to a CJK face, not tofu
    QFont.insertSubstitution(sans, cjk)
    app.setFont(base)


def fonts() -> tuple[str, str]:
    app = QApplication.instance()
    sans = (app and app.property("vz_sans")) or FONT_SANS_FB
    mono = (app and app.property("vz_mono")) or FONT_MONO_FB
    return sans, mono


def cjk_font() -> str:
    app = QApplication.instance()
    return (app and app.property("vz_cjk")) or FONT_CJK


def qss() -> str:
    sans, mono = fonts()
    v = V2
    return f"""
* {{ outline: none; }}
QWidget {{
    background: {v['bg']}; color: {v['text']};
    font-family: "{sans}"; font-size: 13px;
}}
QToolTip {{ background:{v['raised']}; color:{v['text']}; border:1px solid {v['field_line']};
    padding:5px 8px; border-radius:6px; }}

/* surfaces */
QFrame#appnav, QFrame#statusbar {{ background:{v['nav']}; }}
QFrame#appnav {{ border-bottom:1px solid {v['line']}; }}
QFrame#statusbar {{ border-top:1px solid {v['line_soft']}; }}
QFrame#panel {{ background:{v['panel']}; border:1px solid {v['line_soft']}; border-radius:10px; }}
QFrame#panelHead {{ background:transparent; border:none; border-bottom:1px solid {v['line_soft']};
    border-radius:0; }}
QFrame#raised {{ background:{v['raised']}; border:1px solid {v['line']}; border-radius:8px; }}
QFrame#inset {{ background:{v['input']}; border:1px solid {v['line_soft']}; border-radius:7px; }}
QFrame#warnCard {{ background:{v['amber_soft']}; border:1px solid #5a4630; border-radius:10px; }}
QFrame#errorCard {{ background:{v['red_soft']}; border:1px solid {v['red_line']}; border-radius:10px; }}
QFrame#successCard {{ background:{v['green_soft']}; border:1px solid #2e5244; border-radius:10px; }}
QFrame#divider {{ background:{v['line_soft']}; border:none; max-height:1px; min-height:1px; }}
QFrame#vdivider {{ background:{v['line']}; border:none; max-width:1px; min-width:1px; }}
/* v1 surfaces still used by not-yet-ported widgets */
QFrame#toolbar, QFrame#dock, QFrame#rail {{ background:{v['panel']}; border:none; }}
QFrame#card {{ background:{v['panel']}; border:1px solid {v['line_soft']}; border-radius:10px; }}

/* text roles */
QLabel {{ background:transparent; }}
QLabel#display {{ font-size:34px; font-weight:600; color:{v['text']}; }}
QLabel#h1 {{ font-size:22px; font-weight:600; color:{v['text']}; }}
QLabel#h2 {{ font-size:17px; font-weight:600; color:{v['text']}; }}
QLabel#h3 {{ font-size:14px; font-weight:600; color:{v['text']}; }}
QLabel#secondary {{ color:{v['secondary']}; font-size:12px; }}
QLabel#muted {{ color:{v['muted']}; font-size:12px; }}
QLabel#small {{ color:{v['muted']}; font-size:11px; }}
QLabel#eyebrow {{ color:{v['muted']}; font-size:10px; font-weight:600; letter-spacing:1.6px; }}
QLabel#section {{ color:{v['muted']}; font-size:11px; font-weight:600; }}
QLabel#mono {{ font-family:"{mono}"; color:{v['secondary']}; font-size:11px; }}
QLabel#monoText {{ font-family:"{mono}"; color:{v['text']}; font-size:12px; }}
QLabel#kbd {{ font-family:"{mono}"; font-size:10px; color:{v['kbd_text']}; background:{v['kbd_bg']};
    border:1px solid {v['kbd_line']}; border-radius:4px; padding:1px 5px; }}
QLabel#badge {{ font-size:10px; padding:3px 7px; border-radius:5px; background:{v['raised']};
    color:{v['secondary']}; }}
QLabel#badgePurple {{ font-size:10px; padding:3px 7px; border-radius:5px;
    background:{v['purple_soft']}; color:{v['purple_bright']}; }}
QLabel#badgeWarn {{ font-size:10px; padding:3px 7px; border-radius:5px;
    background:{v['amber_soft']}; color:{v['amber']}; }}
QLabel#badgeOk {{ font-size:10px; padding:3px 7px; border-radius:5px;
    background:{v['green_soft']}; color:{v['green']}; }}
QLabel#badgeErr {{ font-size:10px; padding:3px 7px; border-radius:5px;
    background:{v['red_soft']}; color:{v['red']}; }}
QLabel#warnText {{ color:{v['amber']}; }}
QLabel#okText {{ color:{v['green']}; }}
QLabel#errText {{ color:{v['red']}; }}
QLabel#purpleText {{ color:{v['purple_bright']}; }}

/* buttons — default = secondary */
QPushButton, QToolButton {{
    background:{v['raised']}; color:{v['text']};
    border:1px solid {v['line']}; border-radius:7px;
    padding:0 13px; min-height:34px; font-size:12px; font-weight:500;
}}
QPushButton:hover, QToolButton:hover {{ background:#292a35; border-color:#525161; }}
QPushButton:disabled, QToolButton:disabled {{ color:{v['dim']}; background:{v['panel']};
    border-color:{v['line_soft']}; }}
QPushButton#primary, QToolButton#primary {{ background:{v['purple']}; border-color:{v['purple']};
    color:#ffffff; font-weight:600; }}
QPushButton#primary:hover, QToolButton#primary:hover {{ background:{v['purple_hover']};
    border-color:{v['purple_hover']}; }}
QPushButton#primary:disabled {{ background:{v['raised']}; border-color:{v['line']}; color:{v['dim']}; }}
QPushButton#ghost, QToolButton#ghost {{ background:transparent; border-color:transparent;
    color:{v['secondary']}; }}
QPushButton#ghost:hover, QToolButton#ghost:hover {{ background:{v['raised']}; color:{v['text']}; }}
QPushButton#ghost:checked, QToolButton#ghost:checked {{ color:{v['purple_bright']};
    background:{v['purple_soft']}; border-color:{v['purple_line']}; }}
QPushButton#danger {{ background:{v['red_soft']}; border-color:{v['red_line']}; color:{v['red']}; }}
QPushButton#active, QToolButton#active, QPushButton:checked {{ color:{v['purple_bright']};
    background:{v['purple_soft']}; border-color:{v['purple_line']}; }}
QPushButton#small, QToolButton#small {{ min-height:27px; padding:0 9px; font-size:11px; }}
QPushButton#large {{ min-height:47px; font-size:14px; padding:0 20px; font-weight:600; }}
QPushButton#nudge {{ min-height:25px; max-height:27px; padding:0 4px; font-size:11px;
    font-family:"{mono}"; color:{v['secondary']}; }}
QToolButton#icon {{ min-height:29px; min-width:29px; padding:0; background:transparent;
    border-color:transparent; }}
QToolButton#icon:hover {{ background:{v['raised']}; }}
QToolButton#icon:checked {{ background:{v['purple_soft']}; border-color:{v['purple_line']}; }}
QPushButton#link {{ background:transparent; border:none; color:{v['secondary']}; padding:0 4px;
    min-height:24px; }}
QPushButton#link:hover {{ color:{v['text']}; }}
/* segmented control: a QFrame#segmented holding checkable QPushButton#seg */
QFrame#segmented {{ background:{v['input']}; border:1px solid {v['line']}; border-radius:7px; }}
QPushButton#seg {{ background:transparent; border:none; color:{v['muted']}; min-height:24px;
    padding:0 10px; font-size:11px; border-radius:4px; }}
QPushButton#seg:checked {{ background:{v['raised']}; color:{v['text']}; }}
/* step rail */
QPushButton#step {{ background:transparent; border:none; border-radius:0; color:{v['muted']};
    min-height:58px; padding:0 14px; font-size:13px; font-weight:500; }}
QPushButton#step:hover {{ color:{v['text']}; }}
QPushButton#stepActive {{ background:transparent; border:none; border-radius:0;
    border-bottom:2px solid {v['purple']}; color:{v['text']}; min-height:58px; padding:0 14px;
    font-size:13px; font-weight:600; }}
QPushButton#stepDone {{ background:transparent; border:none; border-radius:0; color:{v['secondary']};
    min-height:58px; padding:0 14px; font-size:13px; font-weight:500; }}
/* v1 names */
QPushButton#danger:hover {{ background:#45293a; }}

/* inputs */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background:{v['input']}; color:{v['text']};
    border:1px solid {v['field_line']}; border-radius:7px; padding:0 11px; min-height:35px;
    selection-background-color:{v['purple_soft']}; selection-color:{v['text']};
}}
QTextEdit, QPlainTextEdit {{ padding:9px 11px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border-color:{v['purple']}; }}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color:{v['dim']}; border-color:{v['line_soft']}; }}
QLineEdit#mono, QPlainTextEdit#mono {{ font-family:"{mono}"; font-size:12px; }}
QLineEdit#hero {{ min-height:47px; font-size:14px; padding:0 14px; }}

/* combo */
QComboBox {{ background:{v['input']}; border:1px solid {v['field_line']}; border-radius:7px;
    padding:0 11px; min-height:35px; color:{v['text']}; }}
QComboBox:hover {{ border-color:#525161; }}
QComboBox:focus {{ border-color:{v['purple']}; }}
QComboBox::drop-down {{ border:none; width:24px; }}
QComboBox QAbstractItemView {{ background:{v['raised']}; border:1px solid {v['line']};
    border-radius:7px; selection-background-color:{v['purple_soft']}; outline:none; padding:4px; }}
QComboBox QAbstractItemView::item {{ min-height:28px; padding:4px 10px; border:none;
    color:{v['text']}; }}
QComboBox QAbstractItemView::item:disabled {{ color:{v['dim']}; }}
QComboBox QAbstractItemView::item:selected {{ background:{v['purple_soft']};
    color:{v['purple_bright']}; }}

/* tabs — underline style */
QTabWidget::pane {{ border:none; background:transparent; }}
QTabBar {{ background:transparent; }}
QTabBar::tab {{ background:transparent; color:{v['muted']}; border:none;
    border-bottom:2px solid transparent; padding:12px 2px 10px 2px; margin-right:18px;
    font-size:12px; font-weight:500; }}
QTabBar::tab:hover {{ color:{v['text']}; }}
QTabBar::tab:selected {{ color:{v['purple_bright']}; border-bottom-color:{v['purple']}; }}

/* lists / tables */
QListView, QListWidget, QTableView, QTreeView {{ background:transparent; border:none;
    selection-background-color:transparent; }}
QTableView {{ gridline-color:transparent; }}
QTableView::item {{ padding:6px 8px; border-bottom:1px solid {v['line_soft']}; }}
QTableView::item:selected {{ background:{v['purple_soft']}; color:{v['text']}; }}
QHeaderView::section {{ background:{v['panel']}; color:{v['muted']}; border:none;
    border-bottom:1px solid {v['line_soft']}; padding:8px 10px; font-size:11px; font-weight:600; }}
QListWidget::item {{ border:none; padding:0; }}

/* slider */
QSlider::groove:horizontal {{ height:4px; background:{v['track']}; border-radius:2px; }}
QSlider::sub-page:horizontal {{ background:{v['purple']}; border-radius:2px; }}
QSlider::handle:horizontal {{ width:12px; height:12px; margin:-4px 0; border-radius:6px;
    background:{v['purple']}; }}
QSlider::handle:horizontal:hover {{ background:{v['purple_hover']}; }}
QSlider::groove:horizontal:disabled {{ background:{v['line']}; }}
QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled {{
    background:{v['dim']}; }}

/* progress */
QProgressBar {{ background:{v['raised']}; border:none; border-radius:3px; max-height:6px;
    min-height:6px; text-align:center; color:transparent; }}
QProgressBar::chunk {{ background:{v['purple']}; border-radius:3px; }}

/* scrollbars — thin */
QScrollBar:vertical {{ background:transparent; width:9px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{v['scroll']}; border-radius:3px; min-height:30px; }}
QScrollBar:horizontal {{ background:transparent; height:9px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:{v['scroll']}; border-radius:3px; min-width:30px; }}
QScrollBar::handle:hover {{ background:#55566a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QScrollArea {{ background:transparent; border:none; }}
QScrollArea > QWidget > QWidget {{ background:transparent; }}

/* checkbox / radio */
QCheckBox, QRadioButton {{ background:transparent; spacing:8px; font-size:12px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width:14px; height:14px; }}
QCheckBox::indicator {{ border:1px solid {v['field_line']}; border-radius:4px; background:{v['input']}; }}
QCheckBox::indicator:checked {{ background:{v['purple']}; border-color:{v['purple']}; }}
QRadioButton::indicator {{ border:1px solid {v['field_line']}; border-radius:7px; background:{v['input']}; }}
QRadioButton::indicator:checked {{ background:{v['purple']}; border-color:{v['purple']}; }}

/* splitter */
QSplitter::handle {{ background:transparent; }}
QSplitter::handle:hover {{ background:{v['line']}; }}

/* dialogs / menus */
QDialog {{ background:{v['panel']}; }}
QMenu {{ background:{v['raised']}; border:1px solid {v['line']}; border-radius:8px; padding:4px; }}
QMenu::item {{ padding:6px 14px; border-radius:5px; color:{v['text']}; }}
QMenu::item:selected {{ background:{v['purple_soft']}; color:{v['purple_bright']}; }}
"""
