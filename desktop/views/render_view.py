"""Screen 3 — Dựng video (v2, appdesign/v2 "v2 — Dựng video"). Left: voice card,
speaking speed, four audio-mode cards, volumes, Chinese-hardsub cover, and the
sticky Dựng video button. Right: the source preview → a progress card while
rendering → the finished Vietnamese video with Mở thư mục / Xuất .srt / .ass.

Rendering is locked until the CURRENT subtitle revision is approved (the worker
re-checks; this only makes the state visible). Emits renderRequested(opts); the
subtitle style/font/size are injected by MainWindow from the editor.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.audio import follow_default_output
from desktop.cuemodel import fmt_time
from desktop.icons import icon, pixmap
from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import (
    Collapsible,
    ProjectBar,
    badge,
    button,
    compact_combo,
    icon_label,
    label,
)
from desktop.widgets.pointer import glow
from desktop.widgets.video_panel import VideoPanel
from app.fsutil import atomic_write_text

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    _HAVE_MM = True
except Exception:  # noqa: BLE001
    _HAVE_MM = False

# What each card KEEPS of the original sound (user report 2026-10-27: the old default
# "replace" silently dropped the music and effects). "mix" stays a backend/CLI mode but
# is no card any more — "duck" does the same job with a voice-aware level.
MODES = (("keep_music", "Music2", "Giữ nhạc nền & hiệu ứng",
          "Bỏ giọng Trung (AI tách giọng), lồng tiếng Việt"),
         ("duck", "SlidersHorizontal", "Giữ nguyên âm gốc + lồng tiếng",
          "Giọng Việt đè lên; nhạc, hiệu ứng, giọng Trung nhỏ lại"),
         ("replace", "Mic", "Chỉ giọng Việt", "Bỏ hết âm gốc — cả nhạc và hiệu ứng"),
         ("original", "Captions", "Không lồng tiếng — chỉ phụ đề",
          "Giữ nguyên âm gốc, không tạo giọng Việt (không tốn TTS)"))
MODE_TIPS = {
    "keep_music": "AI tách giọng nói khỏi nhạc nền: giọng Trung bị bỏ, nhạc nền + hiệu ứng "
                  "giữ nguyên, lồng tiếng Việt đặt lên trên. Tách một lần mỗi video (vài phút), "
                  "lần dựng sau dùng lại.",
    "duck": "Giữ toàn bộ âm gốc (nhạc, hiệu ứng, cả giọng Trung) ở mức nhỏ hơn một chút, và nhỏ "
            "thêm khi giọng Việt nói — kiểu thuyết minh. Không cần cài thêm gì.",
    "replace": "Chỉ còn giọng Việt: nhạc nền, hiệu ứng, tiếng động của video gốc đều mất. Thêm "
               "nhạc của bạn ở tab Nhạc nền nếu muốn.",
    "original": "Không tạo giọng Việt — giữ nguyên âm thanh gốc, chỉ đốt phụ đề.",
}
SEP_MISSING = ("“Giữ nhạc nền & hiệu ứng” cần bộ tách giọng AI — máy này chưa cài. Đang dùng "
               "“Giữ nguyên âm gốc + lồng tiếng”: nhạc và hiệu ứng vẫn còn.")
SEP_FALLBACK = ("Chưa có bộ tách giọng — khi dựng sẽ tự dùng “Giữ nguyên âm gốc + lồng tiếng” "
                "(nhạc, hiệu ứng vẫn còn).")


def default_audio_mode(separation: bool) -> str:
    """The audio card a project starts on: keep the original music + effects either way —
    without the Chinese voice when a separation engine is installed, else the whole
    original sound ducked under the dub. Pure."""
    return "keep_music" if separation else "duck"


def _separation_available() -> bool:
    try:
        from app.pipeline import separate

        return bool(separate.available())
    except Exception:  # noqa: BLE001 - a broken engine install is just "not available"
        return False
FRAMES = (("Giữ nguyên khung hình gốc", "source"),
          ("Dọc 9:16 · TikTok / Reels / Shorts (1080×1920)", "tiktok"))
COVERS = (("Hộp đen · dễ đọc, che kín chữ gốc", "box"),
          ("Làm mờ dải dưới video · nhẹ nhàng", "blur"),
          ("Không che", "off"),
          ("Kính mờ trắng · theo Kiểu chữ", "glass"))
# "Chất lượng": output lines (short side, never above the source) × size/sharpness preset
RESOLUTIONS = (("Gốc (theo video nguồn)", "source"), ("1080p", "1080"), ("720p", "720"),
               ("480p", "480"))
PRESETS = (("Nhẹ · file nhỏ", "light"), ("Cân bằng", "balanced"), ("Nét · file lớn", "sharp"))
DEFAULT_PRESET = "sharp"          # text sharpness: the subtitles are the point of the video
SAMPLE = "Xin chào, đây là giọng đọc tiếng Việt cho video của bạn."


def _voice_name(v: str) -> str:
    """vi-VN-HoaiMyNeural → Hoài My, fr-FR-VivienneMultilingualNeural → Vivienne (đa ngôn
    ngữ), vieneu:Trúc Ly → Trúc Ly · trên máy; anything else unchanged."""
    known = {"HoaiMy": "Hoài My", "NamMinh": "Nam Minh"}
    if v.startswith("vieneu:"):
        return f"{v.split(':', 1)[1]} · trên máy"
    if "MultilingualNeural" in v:
        return f"{v.split('-')[2].replace('MultilingualNeural', '')} (đa ngôn ngữ)"
    core = v.replace("vi-VN-", "").replace("Neural", "")
    return known.get(core, core or v)


RESULT_MIN_PICTURE = 140   # the preview / result picture may shrink this far on a small window
CARD_FLOOR = 40            # two lines of text + padding: never less, even before any layout


class ModeCard(QPushButton):
    """A checkable card: icon · title · one-line description."""

    def __init__(self, ic: str, title: str, desc: str) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("modeCard")
        self._min_h = 0
        self._apply_css()
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 8, 10, 8)
        h.setSpacing(10)
        self._ic = QLabel()
        self._ic.setStyleSheet("background:transparent;")
        self._icon_name = ic
        h.addWidget(self._ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        self._title = QLabel(title)
        self._desc = QLabel(desc)
        for w in (self._title, self._desc):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            w.setWordWrap(True)              # the names say what is kept — don't clip them
        col.addWidget(self._title)
        col.addWidget(self._desc)
        h.addLayout(col, 1)
        self._ic.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.toggled.connect(self._paint_state)
        self._paint_state(False)
        glow(self, corner=7)
        self._fit_height()          # a card nobody has resized yet must still have its two lines

    def _apply_css(self) -> None:
        # the app stylesheet gives every QPushButton min-height 36 (it beats setMinimumHeight),
        # and a button's own size hint ignores the labels inside it: the card was 36 px
        # tall while its two lines need ~45 → title and description drew on top of each
        # other. So the card carries its own min-height, recomputed from its text.
        self.setStyleSheet(
            f"QPushButton#modeCard{{text-align:left;background:{V2['panel']};"
            f"border:1px solid {V2['line']};border-radius:7px;padding:0;"
            f"min-height:{self._min_h}px;}}"
            f"QPushButton#modeCard:hover{{border-color:#525161;}}"
            f"QPushButton#modeCard:checked{{background:{V2['purple_soft']};"
            f"border-color:{V2['purple_line']};}}")

    def _fit_height(self) -> None:
        lay = self.layout()
        need = lay.totalHeightForWidth(self.width()) if lay is not None and self.width() else 0
        need = max(need, lay.totalMinimumSize().height() if lay is not None else 0, CARD_FLOOR)
        if need != self._min_h:
            self._min_h = need
            self._apply_css()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._fit_height()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self._fit_height()

    def set_desc(self, text: str) -> None:
        self._desc.setText(text)
        lay = self.layout()
        if lay is not None:          # measure the NEW wrapped text, not the cached layout
            lay.invalidate()
            lay.activate()
        self._fit_height()

    def desc(self) -> str:
        return self._desc.text()

    def _paint_state(self, on: bool) -> None:
        c = V2["purple_bright"] if on else V2["secondary"]
        self._ic.setPixmap(pixmap(self._icon_name, c, 16))
        self._title.setStyleSheet(f"background:transparent;color:{c if on else V2['text']};"
                                  f"font-size:12px;")
        self._desc.setStyleSheet(f"background:transparent;color:{V2['muted']};font-size:10px;")


class _StageRow(QWidget):
    def __init__(self, num: int, name: str) -> None:
        super().__init__()
        self._num = num
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(12)
        self.dot = QLabel(str(num))
        self.dot.setFixedSize(26, 26)
        self.dot.setAlignment(Qt.AlignCenter)
        h.addWidget(self.dot, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = label(name)
        self.detail = label("Chờ đến lượt", "small")
        col.addWidget(self.name)
        col.addWidget(self.detail)
        h.addLayout(col, 1)
        self.set_state("wait")

    def set_state(self, state: str, detail: str | None = None) -> None:
        self.state = state
        bg, line = {"done": (V2["green_soft"], V2["green"]),
                    "active": (V2["purple_soft"], V2["purple"]),
                    "error": (V2["red_soft"], V2["red"])}.get(state, (V2["raised"], V2["line"]))
        self.dot.setStyleSheet(f"background:{bg};border:1px solid {line};border-radius:13px;"
                               f"color:{V2['muted']};font-size:11px;")
        ic = {"done": ("Check", V2["green"]), "active": ("LoaderCircle", V2["purple_bright"]),
              "error": ("X", V2["red"])}.get(state)
        if ic:
            self.dot.setPixmap(pixmap(ic[0], ic[1], 13))
        else:
            self.dot.clear()
            self.dot.setText(str(self._num))
        self.name.setStyleSheet(f"color:{V2['muted'] if state == 'wait' else V2['text']};")
        if detail is not None:
            self.detail.setText(detail)


class RenderView(QWidget):
    renderRequested = Signal(dict)
    renderLaterRequested = Signal(dict)   # "Dựng sau": queue this approved project (batch)
    backRequested = Signal()
    cancelRequested = Signal()
    approveRequested = Signal()        # locked state → re-approve
    joinRequested = Signal(str)        # a split video's group id → join the rendered parts
    showLinesRequested = Signal(list, str)   # "Xem N câu": 0-based cue rows + why → the editor
    # "Giữ nhạc nền & hiệu ứng" needs a separation engine: the main window may wire this
    # to an installer, then call set_separation_available(True)
    installSeparationRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._sep_ok = False           # a voice/music separation engine is installed
        self._mode_saved = False       # this project has the user's own audio choice
        self._out: str | None = None
        self._out_frame = "source"     # frame of the output.mp4 rendered this session
        self._showing_quick = False    # the result pane holds preview.mp4, not output.mp4
        self._title = ""
        self._saved: str | None = None  # last copy saved with "Lưu video…"
        self._group: dict | None = None  # this job is part k/n of a split video
        self._joined: Path | None = None  # the result pane shows a joined full video
        self._job = None
        self._has_real_voices = False
        self._tts = "edge"             # provider key (registry)
        self._approved = True          # MainWindow sets the real state; worker re-checks
        self._rendering = False
        self._batch = False                # a batch render runs: the primary button queues
        self._suggested = False        # "Giữ giọng gốc" already proposed for this job
        self._cues = 0
        self._t0 = 0.0
        self._bg = BgRunner(self)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._player = self._audio = None
        if _HAVE_MM:
            self._player = QMediaPlayer()
            self._audio = QAudioOutput()
            self._player.setAudioOutput(self._audio)
            follow_default_output(self._audio)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.project = ProjectBar()
        self.approved_badge = badge("Phụ đề đã duyệt", "ok")
        self.project.add_action(self.approved_badge)
        self.back_btn = button("Quay lại sửa", "ArrowLeft", variant="ghost")
        self.back_btn.clicked.connect(self.backRequested)
        self.project.add_action(self.back_btn)
        root.addWidget(self.project)

        # options | preview: the user drags the seam; the width is remembered (ui_state)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        saved = store.load_ui_state().get("render_split")
        ok = isinstance(saved, list) and len(saved) == 2 and all(isinstance(v, int) for v in saved)
        split.setSizes(saved if ok else [440, 900])
        scroll = QScrollArea()                # narrower window: scroll, never grow past the screen
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(split)
        split.splitterMoved.connect(lambda *_: store.update_ui_state(render_split=split.sizes()))
        self._split = split
        # ⤢ on either video: the options step aside and the picture takes the whole width
        self.preview.expandRequested.connect(self._toggle_focus)
        self.result.expandRequested.connect(self._toggle_focus)
        body = QVBoxLayout()
        body.setContentsMargins(18, 4, 18, 14)
        body.addWidget(scroll)
        root.addLayout(body, 1)
        self._update_lock()

    # --- left: options ---------------------------------------------------------
    def _left(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumWidth(360)          # the splitter sets the width; this is the floor
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        inner = QWidget()
        ll = QVBoxLayout(inner)
        ll.setContentsMargins(16, 14, 16, 14)
        ll.setSpacing(10)

        ll.addWidget(label("Giọng lồng tiếng", "h3"))
        card = QFrame()
        card.setObjectName("inset")
        ch = QHBoxLayout(card)
        ch.setContentsMargins(10, 10, 10, 10)
        ch.setSpacing(10)
        mic = QLabel()
        mic.setFixedSize(34, 34)
        mic.setAlignment(Qt.AlignCenter)
        mic.setPixmap(pixmap("Mic", V2["purple_bright"], 16))
        mic.setStyleSheet(f"background:{V2['purple_soft']};border-radius:17px;")
        ch.addWidget(mic)
        vc = QVBoxLayout()
        vc.setSpacing(3)
        self.voice = QComboBox()
        self.voice.currentIndexChanged.connect(self._remember_voice)
        self.voice_sub = label("Tiếng Việt", "small")
        vc.addWidget(self.voice)
        vc.addWidget(self.voice_sub)
        self.by_gender_cb = QCheckBox("Giọng theo nhân vật (nam/nữ)")
        self.by_gender_cb.setChecked(bool(store.load_ui_state().get("voice_by_gender", False)))
        self.by_gender_cb.setToolTip(
            "Câu của nam đọc bằng giọng nam, câu của nữ bằng giọng nữ (đoán theo cao độ giọng gốc; "
            "sửa từng câu trong màn Sửa phụ đề). Câu không rõ dùng giọng đã chọn ở trên.")
        self.by_gender_cb.toggled.connect(lambda on: store.update_ui_state(voice_by_gender=on))
        vc.addWidget(self.by_gender_cb)
        ch.addLayout(vc, 1)
        self.listen_btn = QPushButton()
        self.listen_btn.setObjectName("icon")
        self.listen_btn.setIcon(icon("Play", V2["secondary"], 16))
        self.listen_btn.setIconSize(QSize(16, 16))
        self.listen_btn.setFixedSize(34, 34)
        self.listen_btn.setToolTip("Nghe thử giọng này (một câu mẫu)")
        self.listen_btn.clicked.connect(self._listen)
        ch.addWidget(self.listen_btn)
        ll.addWidget(card)
        self.speed, self.speed_lbl = self._slider(ll, "Tốc độ nói", 70, 140, 100,
                                                  lambda v: f"{v / 100:.2f}×")
        # fast talker → most lines late at 1.0×: say so BEFORE ~15 min of TTS
        fit_row = QHBoxLayout()
        self.fit_hint = label("", "small", wrap=True)
        self.fit_btn = button("", variant="ghost", small=True)
        self.fit_btn.clicked.connect(lambda: self.speed.setValue(int(round(self._fit_rate * 100))))
        self.fit_lines_btn = button("", variant="ghost", small=True,
                                    tip="Mở màn Sửa phụ đề, chỉ hiện các câu này để rút gọn")
        self.fit_lines_btn.clicked.connect(self._on_fit_lines)
        fit_row.addWidget(self.fit_hint, 1)
        fit_row.addWidget(self.fit_lines_btn)
        fit_row.addWidget(self.fit_btn)
        ll.addLayout(fit_row)
        self._fit_rows: list[int] = []
        self._fit_cues: list = []
        self._fit_rate = 1.0
        self.fit_hint.setVisible(False)
        self.fit_btn.setVisible(False)
        self.fit_lines_btn.setVisible(False)
        self.speed.valueChanged.connect(lambda _v: self._update_fit_hint())
        self.speed.valueChanged.connect(self._remember_voice)   # slider, keys, "Dùng 1.30×"

        ll.addWidget(_hline())
        ll.addWidget(label("Âm thanh bạn muốn nghe", "h3"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.audio_group = QButtonGroup(self)
        self.audio_group.setExclusive(True)
        self.mode_cards: dict[str, ModeCard] = {}
        for i, (key, ic, title, desc) in enumerate(MODES):
            mc = ModeCard(ic, title, desc)
            mc.setToolTip(MODE_TIPS.get(key, ""))
            self.audio_group.addButton(mc)
            self.mode_cards[key] = mc
            grid.addWidget(mc, i, 0)
        self.mode_cards[default_audio_mode(False)].setChecked(True)
        self.audio_group.buttonToggled.connect(lambda *_: self._sync_audio_enabled())
        self.audio_group.buttonClicked.connect(self._on_mode_clicked)   # the user's own choice
        ll.addLayout(grid)
        # no separation engine: say so next to the cards, offer the install hook
        self.sep_box = QFrame()
        sb = QHBoxLayout(self.sep_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(8)
        self.sep_note = label(SEP_MISSING, "small", wrap=True)
        sb.addWidget(self.sep_note, 1)
        self.sep_btn = button("Cài bộ tách giọng…", "Download", variant="ghost", small=True,
                              tip="Cài công cụ AI tách giọng nói khỏi nhạc nền (một lần)")
        self.sep_btn.clicked.connect(self._install_separation)
        sb.addWidget(self.sep_btn, 0, Qt.AlignTop)
        ll.addWidget(self.sep_box)
        self._show_separation()
        self.orig_vol, self.orig_vol_lbl = self._slider(ll, "Âm lượng gốc", 0, 150, 100,
                                                        lambda v: f"{v}%")
        self.dub_vol, self.dub_vol_lbl = self._slider(ll, "Giọng tiếng Việt", 50, 150, 100,
                                                      lambda v: f"{v}%")
        self.music_lbl = label("", "small", wrap=True)          # the user's music clips
        ll.addWidget(self.music_lbl)
        self._sync_audio_enabled()

        ll.addWidget(_hline())
        ll.addWidget(label("Che phụ đề Trung trên video gốc", "h3"))
        self.cover = QComboBox()
        for text, key in COVERS:
            self.cover.addItem(text, key)
        self._glass_style = False
        self._sync_cover()
        ll.addWidget(self.cover)
        ll.addWidget(label("Khung hình", "h3"))
        self.frame = QComboBox()
        for text, key in FRAMES:
            self.frame.addItem(text, key)
        self.frame.setToolTip("Dọc 9:16: video được thu vừa khung 1080×1920, hai bên là nền "
                              "(mờ hoặc đen) — đăng thẳng lên TikTok/Reels. Video Douyin vốn đã "
                              "dọc thì gần như không có nền.")
        i = self.frame.findData(store.load_ui_state().get("render_frame"))
        self.frame.setCurrentIndex(max(i, 0))
        self.frame.currentIndexChanged.connect(
            lambda _i: store.update_ui_state(render_frame=self.frame.currentData()))
        ll.addWidget(self.frame)
        # 9:16 bars: the picture itself, blurred (CapCut-style) or black
        self.fill = QComboBox()
        self.fill.addItem("Nền hai bên: hình mờ", "blur")
        self.fill.addItem("Nền hai bên: đen", "black")
        self.fill.setToolTip("Phần trống trên/dưới khi đưa video ngang vào khung dọc 9:16")
        self.fill.setCurrentIndex(max(self.fill.findData(
            store.load_ui_state().get("render_fill") or "blur"), 0))
        self.fill.currentIndexChanged.connect(
            lambda _i: store.update_ui_state(render_fill=self.fill.currentData()))
        self.frame.currentIndexChanged.connect(
            lambda _i: self.fill.setVisible(self.frame.currentData() == "tiktok"))
        self.fill.setVisible(self.frame.currentData() == "tiktok")
        ll.addWidget(self.fill)
        ll.addWidget(label("Chất lượng", "h3"))
        st = store.load_ui_state()
        qrow = QHBoxLayout()
        qrow.setSpacing(8)
        self.res = QComboBox()
        for text, key in RESOLUTIONS:
            self.res.addItem(text, key)
        self.res.setToolTip("Độ phân giải video xuất (cạnh ngắn). Không bao giờ lớn hơn video "
                            "nguồn. Khung dọc 9:16: 1080p = 1080×1920, 720p = 720×1280.")
        self.res.setCurrentIndex(max(self.res.findData(st.get("render_res")), 0))
        self.preset = QComboBox()
        for text, key in PRESETS:
            self.preset.addItem(text, key)
        self.preset.setToolTip("Nhẹ: file nhỏ, hơi mềm · Cân bằng: gần như không thấy khác · "
                               "Nét: giữ chi tiết tối đa, file lớn nhất")
        k = self.preset.findData(st.get("render_quality") or DEFAULT_PRESET)
        self.preset.setCurrentIndex(max(k, 0))
        self.res.currentIndexChanged.connect(self._on_quality)
        self.preset.currentIndexChanged.connect(self._on_quality)
        self.frame.currentIndexChanged.connect(lambda _i: self._sync_quality())
        qrow.addWidget(self.res, 1)
        qrow.addWidget(self.preset, 1)
        ll.addLayout(qrow)
        self.size_lbl = label("", "small", wrap=True)
        ll.addWidget(self.size_lbl)
        self._src_size: tuple[int, int] | None = None
        self._src_dur = 0.0
        ll.addWidget(label("Kiểu chữ lấy từ màn Sửa phụ đề.", "small", wrap=True))

        # F-1/F-2/F-3: credit, AI-voice label, right to use (docs/plan/F-roadmap.md) — folded
        # into one section with a one-line summary: it is set once per channel, not per episode
        ll.addWidget(_hline())
        st = store.load_ui_state()
        self.post_section = Collapsible("Đăng tải & thương hiệu",
                                        expanded=bool(st.get("render_post_open", False)))
        self.post_section.toggled.connect(lambda on: store.update_ui_state(render_post_open=on))
        pb = self.post_section.body
        ll.addWidget(self.post_section)
        self.credit_cb = QCheckBox("Ghi nguồn trên video (5 giây đầu)")
        self.credit_cb.setChecked(bool(st.get("credit_overlay", True)))
        self.credit_cb.toggled.connect(lambda on: store.update_ui_state(credit_overlay=on))
        self.ai_label_cb = QCheckBox("Nhãn “Lồng tiếng AI” (5 giây đầu)")
        self.ai_label_cb.setChecked(bool(st.get("ai_label", True)))
        self.ai_label_cb.setToolTip(
            "Một nhãn nhỏ ở góc trên. TikTok yêu cầu gắn nhãn nội dung AI chân thực; Luật AI "
            "134/2025 yêu cầu khi giọng AI mô phỏng người thật. Không ảnh hưởng chất lượng.")
        self.ai_label_cb.toggled.connect(lambda on: store.update_ui_state(ai_label=on))
        pb.addWidget(self.credit_cb)
        pb.addWidget(self.ai_label_cb)
        pb.addWidget(self._build_watermark(st))
        pb.addWidget(self._build_episode_card(st))
        rrow = QHBoxLayout()
        rrow.setSpacing(8)
        cap = label("Quyền sử dụng", "secondary")
        cap.setFixedWidth(116)
        rrow.addWidget(cap)
        from app.pipeline import disclosure

        self.rights = QComboBox()
        for key, text in disclosure.RIGHTS.items():
            self.rights.addItem(text, key)
        self.rights.currentIndexChanged.connect(self._on_rights)
        # long choices elide instead of widening the fixed-width panel past its scroll area
        for combo in (self.voice, self.cover, self.frame, self.fill):
            compact_combo(combo, 10)
        for combo in (self.res, self.preset):     # two per row
            compact_combo(combo, 6)
        compact_combo(self.rights, 6)            # shares its row with a caption + button
        rrow.addWidget(self.rights, 1)
        self.proof_btn = button("Bằng chứng…", "Upload", variant="ghost", small=True,
                                tip="Đính kèm ảnh chụp / email cho phép — được chép vào thư mục dự án")
        self.proof_btn.clicked.connect(self._attach_proof)
        rrow.addWidget(self.proof_btn)
        pb.addLayout(rrow)
        self.rights_note = label("", "small", wrap=True)
        pb.addWidget(self.rights_note)
        for cb in (self.credit_cb, self.ai_label_cb):
            cb.toggled.connect(lambda _on: self._update_post_summary())
        self._update_post_summary()
        self.comment_lbl = label("", "small", wrap=True)          # F-4
        ll.addWidget(self.comment_lbl)
        ll.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)

        foot = QFrame()
        foot.setObjectName("panelHead")
        foot.setStyleSheet(f"QFrame#panelHead{{border-bottom:none;border-top:1px solid "
                           f"{V2['line_soft']};}}")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(8)
        srow = QHBoxLayout()
        self.gate_icon = icon_label("ShieldCheck", V2["secondary"], 15)
        srow.addWidget(self.gate_icon)
        self.gate_lbl = label("", "small")
        self.gate_lbl.setWordWrap(True)
        srow.addWidget(self.gate_lbl, 1)
        fl.addLayout(srow)
        # what "Dựng video" will do, in one line — the options above scroll, this does not
        self.summary_lbl = label("", "small", wrap=True)
        fl.addWidget(self.summary_lbl)
        self.render_btn = button("Dựng video", "MonitorPlay", variant="primary")
        self.render_btn.setStyleSheet("QPushButton{min-height:40px;}")
        self.render_btn.clicked.connect(self._on_primary)
        self.quick_btn = button("Xem nhanh", "Eye", variant="ghost",
                                tip="Dựng nhanh bản chất lượng thấp (640p) để nghe thử toàn bộ lồng "
                                    "tiếng trước. Giọng đã tạo được giữ — dựng thật sau nhanh hơn.")
        self.quick_btn.setStyleSheet("QPushButton{min-height:40px;}")
        self._quick_tip = self.quick_btn.toolTip()
        self.quick_btn.clicked.connect(self._on_quick)
        self.later_btn = button("Dựng sau", "Clock3", variant="ghost",
                                tip="Thêm vào hàng dựng: dựng nền với cài đặt này, trong lúc bạn "
                                    "sửa tập khác (Thư viện → Dựng hàng loạt)")
        self.later_btn.setStyleSheet("QPushButton{min-height:40px;}")
        self.later_btn.clicked.connect(self._on_later)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        prow.addWidget(self.quick_btn)
        prow.addWidget(self.later_btn)
        prow.addWidget(self.render_btn, 1)
        fl.addLayout(prow)
        self.again_lbl = label("Lần dựng sau chỉ tạo lại giọng cho câu đã sửa.", "small")
        self.again_lbl.setVisible(False)
        fl.addWidget(self.again_lbl)
        self.group_box = QFrame()
        gb = QHBoxLayout(self.group_box)
        gb.setContentsMargins(0, 4, 0, 0)
        gb.setSpacing(8)
        self.group_lbl = label("", "small")
        self.group_lbl.setWordWrap(True)
        gb.addWidget(self.group_lbl, 1)
        self.join_btn = button("Ghép video đầy đủ", "Merge", variant="ghost", small=True,
                               tip="Nối mọi phần đã dựng thành một video, phụ đề .srt nối theo")
        self.join_btn.clicked.connect(
            lambda: self._group and self.joinRequested.emit(str(self._group["id"])))
        gb.addWidget(self.join_btn)
        self.group_box.setVisible(False)
        fl.addWidget(self.group_box)
        v.addWidget(foot)
        for sig in (self.voice.currentIndexChanged, self.speed.valueChanged,
                    self.audio_group.buttonToggled, self.preset.currentIndexChanged,
                    self.res.currentIndexChanged, self.frame.currentIndexChanged):
            sig.connect(lambda *_a: self._update_summary())
        self._update_summary()
        return panel

    def _slider(self, parent: QVBoxLayout, title: str, lo: int, hi: int, val: int, fmt):
        row = QHBoxLayout()
        row.setSpacing(12)
        cap = label(title, "secondary")
        cap.setFixedWidth(116)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        lbl = label(fmt(val), "mono")
        lbl.setFixedWidth(46)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        s.valueChanged.connect(lambda v: lbl.setText(fmt(v)))
        row.addWidget(cap)
        row.addWidget(s, 1)
        row.addWidget(lbl)
        parent.addLayout(row)
        return s, lbl

    # --- right: preview | progress | result ------------------------------------------
    def _right(self) -> QWidget:
        w = QWidget()
        self._right_stack = QStackedLayout(w)

        # 0 — preview of the source with the export-styled subtitle
        prev = QWidget()
        pv = QVBoxLayout(prev)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        self.preview = VideoPanel("Video nguồn")
        self.preview.set_min_picture_height(RESULT_MIN_PICTURE)
        if hasattr(self, "wm_cb"):
            self._on_watermark(save=False)
        self.preview.durationChanged.connect(lambda _d: self._show_commentary())
        self.frame.currentIndexChanged.connect(self._sync_frame_aspect)
        self._sync_frame_aspect()
        pv.addWidget(self.preview, 1)
        self._right_stack.addWidget(prev)

        # 1 — progress card
        prog = QFrame()
        prog.setObjectName("panel")
        pl = QVBoxLayout(prog)
        pl.setContentsMargins(0, 0, 0, 0)
        head = QFrame()
        head.setObjectName("panelHead")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(14, 9, 10, 9)
        hh.addWidget(label("Tiến độ dựng video", "h3"))
        hh.addStretch(1)
        hh.addWidget(badge("Có thể hủy", "purple"))
        pl.addWidget(head)
        center = QHBoxLayout()
        center.addStretch(1)
        card = QFrame()
        card.setObjectName("raised")
        card.setFixedWidth(420)
        card.setStyleSheet(f"QFrame#raised{{background:{V2['panel']};border:1px solid "
                           f"{V2['line']};border-radius:10px;}}")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(22, 20, 22, 18)
        cv.setSpacing(10)
        th = QHBoxLayout()
        self.p_title = label("Đang chuẩn bị…", "h2")
        self.p_badge = badge("0%", "purple")
        th.addWidget(self.p_title, 1)
        th.addWidget(self.p_badge, 0, Qt.AlignTop)
        cv.addLayout(th)
        self.p_detail = label("", "small")
        self.p_detail.setWordWrap(True)
        cv.addWidget(self.p_detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        cv.addWidget(self.bar)
        self.stage_rows = {"separate": _StageRow(1, "Tách nhạc nền"),
                           "tts": _StageRow(2, "Lồng tiếng"),
                           "assemble": _StageRow(3, "Dựng MP4")}
        for r in self.stage_rows.values():
            cv.addWidget(r)
        self.log = QPlainTextEdit()
        self.log.setObjectName("mono")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(110)
        self.log.setVisible(False)
        cv.addWidget(self.log)
        fr = QHBoxLayout()
        self.elapsed = label("Đã chạy 00:00", "mono")
        fr.addWidget(self.elapsed)
        log_btn = button("Chi tiết", "ChevronDown", variant="ghost", small=True, checkable=True)
        log_btn.toggled.connect(self.log.setVisible)
        fr.addWidget(log_btn)
        fr.addStretch(1)
        # cancel is only visible while a render runs (see set_rendering); TTS stops
        # between cues, the encode is killed (assemble._run_ffmpeg)
        self.cancel_btn = button("Hủy", "X")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancelRequested)
        fr.addWidget(self.cancel_btn)
        cv.addLayout(fr)
        center.addWidget(card)
        center.addStretch(1)
        mid = QVBoxLayout()
        mid.addStretch(1)
        mid.addLayout(center)
        mid.addStretch(1)
        pl.addLayout(mid, 1)
        self.status = label("Kết quả sẽ được lưu vào thư mục của dự án.", "small")
        st = QFrame()
        st.setObjectName("panelHead")
        st.setStyleSheet(f"QFrame#panelHead{{border-bottom:none;border-top:1px solid "
                         f"{V2['line_soft']};}}")
        sl = QHBoxLayout(st)
        sl.setContentsMargins(14, 10, 14, 10)
        sl.addWidget(self.status, 1)
        pl.addWidget(st)
        self._right_stack.addWidget(prog)

        # 2 — result
        res = QWidget()
        rv = QVBoxLayout(res)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        ok = QFrame()
        ok.setObjectName("successCard")
        ok.setStyleSheet(f"QFrame#successCard{{background:{V2['green_soft']};border:1px solid "
                         f"#2e5244;border-bottom-left-radius:0;border-bottom-right-radius:0;}}")
        oh = QHBoxLayout(ok)
        oh.setContentsMargins(14, 10, 14, 10)
        oh.setSpacing(10)
        oh.addWidget(icon_label("CircleCheck", V2["green"], 18))
        oc = QVBoxLayout()
        oc.setSpacing(1)
        t = label("Video tiếng Việt đã sẵn sàng", "h3")
        self._result_title = t
        t.setStyleSheet(f"color:{V2['green']};background:transparent;")
        self.out_meta = label("", "mono")
        oc.addWidget(t)
        oc.addWidget(self.out_meta)
        oh.addLayout(oc, 1)
        saved = label("Đã lưu", "small")
        saved.setStyleSheet(f"color:{V2['green']};background:transparent;")
        oh.addWidget(saved)
        rv.addWidget(ok)
        self.result = VideoPanel("Bản tiếng Việt")
        self.result.set_min_picture_height(RESULT_MIN_PICTURE)
        rv.addWidget(self.result, 1)
        acts = QFrame()
        acts.setObjectName("panel")
        av = QVBoxLayout(acts)
        av.setContentsMargins(14, 10, 14, 10)
        av.setSpacing(6)
        al = QHBoxLayout()
        al.setSpacing(8)
        av.addLayout(al)
        self.save_btn = button("Lưu video…", "FileVideo", variant="primary",
                               tip="Lưu một bản video, đặt tên theo tiêu đề dự án")
        self.save_btn.clicked.connect(self._save_video)
        self.open_btn = button("Mở thư mục", "FolderOpen")
        self.open_btn.clicked.connect(self._open_folder)
        self.export_btn = button("Xuất", "Download")
        menu = QMenu(self.export_btn)
        menu.addAction("Phụ đề .srt", lambda: self._export("srt"))
        menu.addAction("Phụ đề .ass (giữ kiểu chữ)", lambda: self._export("ass"))
        self.mkv_action = menu.addAction("Video .mkv · phụ đề rời (bật/tắt được)", self._export_mkv)
        self.mkv_action.setToolTip("Hình gốc không đốt chữ + giọng đã dựng + phụ đề kèm font — "
                                   "để sửa tiếp trong phần mềm khác. Thường vài giây; nếu đã che bằng làm mờ "
                                   "thì hình được làm mờ y như bản dựng (lâu hơn).")
        menu.setToolTipsVisible(True)
        self.export_btn.setMenu(menu)
        self.post_btn = button("Mô tả đăng bài", "Copy",
                               tip="Mô tả có ghi nguồn + nhãn AI, và danh sách kiểm tra trước khi đăng")
        self.post_btn.clicked.connect(self._show_post)
        back = button("Quay lại sửa", "ArrowLeft", variant="ghost")
        back.clicked.connect(self.backRequested)
        for b in (self.save_btn, self.open_btn, self.export_btn, self.post_btn):
            al.addWidget(b)
        al.addStretch(1)
        # a long "N câu lồng tiếng vào trễ…" line used to sit in the button row and made the
        # whole pane (and the page) wider than a laptop window: it wraps on its own row now
        sr = QHBoxLayout()
        sr.setSpacing(8)
        self.res_status = label("", "small", wrap=True)
        sr.addWidget(self.res_status, 1)
        sr.addWidget(back, 0, Qt.AlignTop)
        av.addLayout(sr)
        rv.addSpacing(8)
        rv.addWidget(acts)
        self._right_stack.addWidget(res)
        return w

    # --- state ---------------------------------------------------------------------
    def set_project(self, title: str, meta: str) -> None:
        self._title = title
        self.project.set_project(title, meta)

    def set_job(self, job, *, cues: int, tts: str = "") -> None:
        changed = job is not self._job
        if changed:
            self._suggested = False
            self._joined = None
            self._out = None
            self._out_frame = "source"
            self._saved = None
            self._show_result_kind(quick=False)
            out = getattr(job, "output_video", None)
            if out is not None and Path(out).exists():
                self._out = str(out)
                self.result.load(Path(out))
            self.again_lbl.setVisible(bool(self._out))
            self._right_stack.setCurrentIndex(2 if self._out else 0)
        self._job = job
        self._cues = cues
        self._sep_ok = _separation_available()
        self._restore_audio_mode()
        self._show_music()
        if changed:
            self._probe_source(job)
            self._on_episode_card(save=False)      # this video's "Tập N" in the hint
        self._load_posting()
        if tts:
            self._tts = tts
            self.voice_sub.setText(f"Tiếng Việt · {tts}")
        self._update_lock()

    def set_approved(self, on: bool) -> None:
        self._approved = on
        self._update_lock()

    def set_group(self, status: dict | None) -> None:
        """Part k/n of a split video: progress of the whole video + the join button."""
        self._group = status
        self.group_box.setVisible(bool(status))
        if not status:
            return
        n, done = int(status.get("total") or 0), int(status.get("rendered") or 0)
        self.group_lbl.setText(f"Phần {status.get('index')}/{n} của video dài · {done}/{n} phần "
                               f"đã dựng · {int(status.get('approved') or 0)}/{n} đã duyệt")
        self.join_btn.setEnabled(n > 0 and done == n)
        self.join_btn.setToolTip("Nối mọi phần đã dựng thành một video, phụ đề .srt nối theo"
                                 if done == n else f"Còn {n - done} phần chưa dựng (hoặc đã sửa sau "
                                 f"khi dựng)")

    def start_join(self, n: int) -> None:
        self._start_progress({})
        self.stage_rows["tts"].setVisible(False)
        self.stage_rows["assemble"]._num = 1
        self.stage_rows["assemble"].set_state("active", f"Nối {n} phần thành một video")
        self.p_title.setText("Đang ghép video đầy đủ")
        self.status.setText("Video đầy đủ được lưu trong thư mục của video gốc.")

    def show_joined(self, path: str, title: str) -> None:
        """The joined full video: its folder (the original's) holds output.mp4, vi.srt,
        subs.ass and source.mp4, so every export below works on it like on one job."""
        from app.models import Job

        self.stage_rows["tts"].setVisible(True)
        gdir = Path(path).parent
        self._job = Job(id=gdir.name, work_dir=gdir)
        self.show_done(path)
        self._joined = Path(path)
        self._title = title
        self._result_title.setText("Video đầy đủ đã sẵn sàng (đã ghép các phần)")

    def _update_lock(self) -> None:
        ok = self._approved
        self.approved_badge.setText("Phụ đề đã duyệt" if ok else "Chưa duyệt bản mới nhất")
        self.approved_badge.setObjectName("badgeOk" if ok else "badgeWarn")
        self.approved_badge.style().unpolish(self.approved_badge)
        self.approved_badge.style().polish(self.approved_badge)
        n = f"{self._cues} câu" if self._cues else "phụ đề"
        self.gate_lbl.setText(f"Đã duyệt {n} · giọng được lưu để dùng lại" if ok else
                              "Phụ đề đã sửa sau khi duyệt — duyệt lại trước khi tạo giọng.")
        self.gate_icon.setPixmap(pixmap("ShieldCheck" if ok else "LockKeyhole",
                                        V2["secondary"] if ok else V2["amber"], 15))
        if not self._rendering:
            queue = ok and self._batch
            self.render_btn.setText("Thêm vào hàng dựng" if queue else
                                    ("Dựng lại" if self._out else "Dựng video") if ok else
                                    "Duyệt phụ đề")
            self.render_btn.setIcon(icon("Plus" if queue else "MonitorPlay" if ok else "ShieldCheck",
                                         "#ffffff", 16))
            self.render_btn.setToolTip("Đang dựng hàng loạt — video này được dựng khi tới lượt"
                                       if queue else "")
            self.later_btn.setVisible(not self._batch)
            self.later_btn.setEnabled(ok)
            self.quick_btn.setEnabled(not self._batch)
            self.quick_btn.setToolTip("Đang dựng hàng loạt — xem nhanh khi hàng dựng xong"
                                      if self._batch else self._quick_tip)
        self.listen_btn.setEnabled(ok)
        self.listen_btn.setToolTip("Nghe thử giọng này (một câu mẫu)" if ok else
                                   "Duyệt phụ đề trước khi tạo giọng")

    def _load_posting(self) -> None:
        """Credit preview + the recorded right to use, for the current job."""
        from app.pipeline import disclosure

        if self._job is None:
            return
        line = disclosure.credit_line(self._job)
        self.credit_cb.setEnabled(bool(line))
        self.credit_cb.setToolTip(f"Sẽ hiện: “{line}”" if line else
                                  "Không có thông tin người đăng (video từ máy) — ghi nguồn trong "
                                  "mô tả bài đăng.")
        self._show_commentary()
        basis = disclosure.load_rights(self._job).get("basis", "unknown")
        self.rights.blockSignals(True)
        self.rights.setCurrentIndex(max(0, self.rights.findData(basis)))
        self.rights.blockSignals(False)
        self._show_rights_note(basis)
        self._update_post_summary()

    def _update_summary(self, *_a) -> None:
        """The footer's line: "Hoài My 1.00× · Giữ nhạc nền & hiệu ứng · 540×960 · Nét · ước tính
        ~223 MB" — the choices that decide the output, visible without scrolling the options."""
        if not hasattr(self, "summary_lbl") or not hasattr(self, "size_lbl"):
            return
        mode = self._audio_mode()
        title = next((t for k, _i, t, _d in MODES if k == mode), "")
        bits = []
        if mode != "original" and self.voice.currentText():
            bits.append(f"{self.voice.currentText()} {self.speed.value() / 100:.2f}×")
        bits.append(title)
        size = [b for b in self.size_lbl.text().split(" · ") if b]
        if size:
            bits.append(size[0])                                     # 540×960
        bits.append(self.preset.currentText().split(" · ")[0])
        bits += size[1:]                                              # ước tính ~223 MB
        self.summary_lbl.setText(" · ".join(b for b in bits if b))

    def _update_post_summary(self, *_a) -> None:
        """What the folded "Đăng tải & thương hiệu" section holds, in one line. A missing
        right to use is shown in amber — folding it away must not hide the warning."""
        if not hasattr(self, "rights") or not hasattr(self, "card_cb"):
            return                                   # still being built
        bits = []
        if self.credit_cb.isChecked():
            bits.append("ghi nguồn")
        if self.ai_label_cb.isChecked():
            bits.append("nhãn AI")
        if self.wm_cb.isChecked() and self.wm_text.text().strip():
            bits.append(f"watermark “{self.wm_text.text().strip()[:18]}”")
        if self.card_cb.isChecked():
            bits.append("thẻ tập")
        basis = self.rights.currentData() or "unknown"
        unknown = basis == "unknown"
        rights = "quyền: chưa ghi" if unknown else f"quyền: {self.rights.currentText().lower()}"
        self.post_section.set_summary(" · ".join((bits or ["không bật mục nào"]) + [rights]),
                                      warn=unknown)

    def _show_rights_note(self, basis: str) -> None:
        from app.pipeline import disclosure

        proof = disclosure.load_rights(self._job).get("proof") if self._job else ""
        if basis == "unknown":
            self.rights_note.setText(
                "Chưa ghi quyền sử dụng. Đăng lại video của người khác khi chưa được phép có thể bị "
                "gỡ hoặc tắt kiếm tiền — Douyin cấm đăng lại nội dung của họ ở nơi khác.")
            self.rights_note.setStyleSheet(f"color:{V2['amber']};")
        else:
            self.rights_note.setText("Đã ghi" + (f" · bằng chứng: {Path(proof).name}" if proof else
                                                 " — nên đính kèm bằng chứng nếu được cho phép."))
            self.rights_note.setStyleSheet(f"color:{V2['muted']};")

    def _on_rights(self, _i: int) -> None:
        from app.pipeline import disclosure

        self._update_post_summary()
        if self._job is None:
            return
        disclosure.save_rights(self._job, self.rights.currentData() or "unknown")
        self._show_rights_note(self.rights.currentData() or "unknown")
        self._update_post_summary()

    def _attach_proof(self) -> None:
        from app.pipeline import disclosure

        if self._job is None:
            return
        fn, _ = QFileDialog.getOpenFileName(self, "Bằng chứng được phép dùng video", "",
                                            "Ảnh / tài liệu (*.png *.jpg *.jpeg *.pdf *.eml *.txt);;"
                                            "Tất cả (*.*)")
        if not fn:
            return
        basis = self.rights.currentData() or "unknown"
        if basis == "unknown":
            basis = "permission"
            self.rights.blockSignals(True)
            self.rights.setCurrentIndex(self.rights.findData(basis))
            self.rights.blockSignals(False)
        try:
            disclosure.save_rights(self._job, basis, proof=Path(fn))
        except OSError as e:
            self.rights_note.setText(f"Không chép được tệp: {e}")
            return
        self._show_rights_note(basis)

    def _show_late(self) -> None:
        """F-10: how many dub lines started noticeably after their subtitle."""
        from app.pipeline import review

        late = review.load_late(self._job) if self._job is not None else []
        if late:
            worst = max(float(r.get("carry_s", 0)) for r in late)
            self.res_status.setText(f"{len(late)} câu lồng tiếng vào trễ hơn 0,2 s (chậm nhất "
                                    f"{worst:.1f} s) — xem cờ “Lồng tiếng trễ” ở màn Sửa phụ đề.")
            self.res_status.setStyleSheet(f"color:{V2['amber']};")
        else:
            self.res_status.setStyleSheet("")

    def _commentary_stats(self) -> tuple[int, float]:
        """(notes, share of the runtime): measured by the last render when it had them,
        otherwise estimated from the text."""
        from app.pipeline import assemble, commentary

        if self._job is None:
            return 0, 0.0
        notes = commentary.load(self._job)
        info = assemble.load_render_info(self._job)
        if notes and info.get("commentary_s"):
            return len(notes), float(info.get("commentary_share") or 0.0)
        dur = self.preview.duration() or float(info.get("duration") or 0.0)
        if not dur and notes and self._job.source_video.exists():
            # the player hasn't reported a length yet: 0 here made 9 s read as "94.7%"
            dur = assemble.probe_duration(self._job.source_video)
        return len(notes), commentary.estimate(notes, dur)[1] if notes and dur else 0.0

    def _show_commentary(self) -> None:
        n, share = self._commentary_stats()
        if n:
            self.comment_lbl.setText(f"Bình luận của bạn: {n} đoạn · ~{share * 100:.1f}% thời "
                                     "lượng — giọng khác giọng lồng tiếng.")
            self.comment_lbl.setStyleSheet(f"color:{V2['secondary']};")
        else:
            self.comment_lbl.setText("Chưa có bình luận của bạn. Video đăng lại cần phần của "
                                     "riêng bạn để được phân phối / kiếm tiền — thêm ở tab Bình "
                                     "luận (màn Sửa phụ đề).")
            self.comment_lbl.setStyleSheet(f"color:{V2['amber']};")

    def _show_post(self) -> None:
        from app.pipeline import disclosure, postcopy
        from desktop.widgets.dialogs import PostDialog

        if self._job is None:
            return
        job = self._job
        ai = self._audio_mode() != "original"
        text = disclosure.post_description(job, ai_voice=ai)
        rights = disclosure.load_rights(job).get("basis", "unknown")
        credit = self.credit_cb.isChecked() and self.credit_cb.isEnabled()
        PostDialog(self, text=text, checklist=disclosure.checklist(
            ai_voice=ai, credit=credit or bool(disclosure.credit_line(job)),
            rights=rights, commentary=self._commentary_stats()),
            suggest=lambda: postcopy.suggest(job)).exec()

    def _sync_audio_enabled(self) -> None:
        mode = self._audio_mode()
        keep = mode not in ("replace", "original")
        self.orig_vol.setEnabled(keep)
        self.orig_vol_lbl.setEnabled(keep)
        dub = mode != "original"
        for w in (self.dub_vol, self.dub_vol_lbl, self.voice, self.speed, self.speed_lbl,
                  self.by_gender_cb):
            w.setEnabled(dub)
        if hasattr(self, "ai_label_cb"):           # built after the audio cards
            self.ai_label_cb.setEnabled(dub)
        self._update_fit_hint()                    # no dub → no "late lines" warning

    def suggest_original(self, singing: str | None) -> None:
        """E-8: a mostly-sung video starts on "Giữ giọng gốc" (once per job; the user's
        own choice — this session or saved with the project — is kept)."""
        card = self.mode_cards["original"]
        card.setToolTip("AI nghe thấy video chủ yếu là hát — nên giữ giọng hát gốc."
                        if singing == "yes" else MODE_TIPS["original"])
        if singing == "yes" and not self._suggested and not self._mode_saved:
            self._suggested = True
            card.setChecked(True)

    # --- audio mode: default, per-project memory, separation engine ------------------
    def _install_separation(self) -> None:
        """One click installs PyTorch + Demucs into the app's venv (asks first: big)."""
        from PySide6.QtWidgets import QMessageBox

        from app import tools

        gpu = tools.has_nvidia_gpu()
        size = "~2,5–3 GB (PyTorch có CUDA cho card NVIDIA)" if gpu else "~1 GB (PyTorch bản CPU)"
        if QMessageBox.question(
                self, "Cài bộ tách giọng",
                f"Tải và cài PyTorch + Demucs vào app: {size}, mất vài phút. Chạy trên máy "
                "bạn, không upload âm thanh. Tiếp tục?") != QMessageBox.StandardButton.Yes:
            return
        self.installSeparationRequested.emit()
        self.sep_btn.setEnabled(False)
        self.sep_btn.setText("Đang cài… (vài phút)")

        def done(res):
            ok, msg = res
            from app.pipeline import separate

            self.sep_btn.setEnabled(True)
            self.sep_btn.setText("Cài bộ tách giọng…")
            self.set_separation_available(ok and separate.available())
            self.status.setText(msg)
            if not ok:
                QMessageBox.warning(self, "Cài bộ tách giọng", msg)

        self._bg.run(lambda: tools.install_separation(gpu=gpu), done,
                     lambda m: QMessageBox.warning(self, "Cài bộ tách giọng", m))

    def set_separation_available(self, on: bool) -> None:
        """After an engine install (or removal): update the note, and move a project
        that never chose to the new best default."""
        self._sep_ok = bool(on)
        if not self._mode_saved and self._audio_mode() != "original":
            self.mode_cards[default_audio_mode(self._sep_ok)].setChecked(True)
        self._show_separation()

    def _restore_audio_mode(self) -> None:
        """The project's saved choice (voice.json), else the default for this machine."""
        saved = saved_audio_mode(self._job) if self._job is not None else None
        self._mode_saved = saved in self.mode_cards
        key = saved if self._mode_saved else default_audio_mode(self._sep_ok)
        if not (self._suggested and not self._mode_saved):     # keep E-8's "original"
            self.mode_cards[key].setChecked(True)
        self._show_separation()

    def _on_mode_clicked(self, _btn) -> None:
        self._mode_saved = True
        if self._job is not None:
            _update_project_file(self._job, audio_mode=self._audio_mode())
        self._show_separation()

    def _show_separation(self) -> None:
        """Without an engine, "Giữ nhạc nền" stays visible but says what it needs."""
        card = self.mode_cards["keep_music"]
        desc = MODES[0][3]
        if self._sep_ok:
            card.set_desc(desc)
            self.sep_box.setVisible(False)
            return
        card.set_desc("Cần bộ tách giọng AI — chưa cài")
        self.sep_box.setVisible(True)
        fallback = self._audio_mode() == "keep_music"
        self.sep_note.setText(SEP_FALLBACK if fallback else SEP_MISSING)
        self.sep_note.setStyleSheet(f"color:{V2['amber'] if fallback else V2['muted']};")

    def _show_music(self) -> None:
        from app.pipeline import music

        clips = music.load(self._job) if self._job is not None else []
        if clips:
            self.music_lbl.setText(f"{music.summary(clips)} — thêm / sửa ở tab Nhạc nền "
                                   "(màn Sửa phụ đề).")
            self.music_lbl.setStyleSheet(f"color:{V2['secondary']};")
        else:
            self.music_lbl.setText("Chưa thêm nhạc nền của bạn — thêm ở tab Nhạc nền (màn Sửa "
                                   "phụ đề), cho cả video hoặc từng đoạn.")
            self.music_lbl.setStyleSheet(f"color:{V2['muted']};")

    def _audio_mode(self) -> str:
        for key, card in self.mode_cards.items():
            if card.isChecked():
                return key
        return default_audio_mode(self._sep_ok)

    def set_voices(self, voices: list[str]) -> None:
        self.voice.blockSignals(True)
        self.voice.clear()
        self._has_real_voices = bool(voices)
        for v in voices or ["(mặc định)"]:
            self.voice.addItem(_voice_name(v) if voices else v, v)
        self.voice.blockSignals(False)
        self._update_summary()

    def select_voice(self, voice: str | None) -> None:
        i = self.voice.findData(voice) if voice else -1
        if i >= 0:
            self.voice.blockSignals(True)
            self.voice.setCurrentIndex(i)
            self.voice.blockSignals(False)
        rate = saved_rate(self._job) if self._job is not None else None
        self.speed.blockSignals(True)                 # restoring is not a new choice
        self.speed.setValue(int(round((rate or 1.0) * 100)))
        self.speed.blockSignals(False)
        self.speed_lbl.setText(f"{self.speed.value() / 100:.2f}×")
        self._update_fit_hint()
        self._update_summary()

    def _remember_voice(self, *_a) -> None:
        """The user's voice and speed stick to this project (voice.json) across restarts."""
        if self._job is not None and self._has_real_voices:
            _update_project_file(self._job, voice=self.voice.currentData(),
                                 rate=self.speed.value() / 100)

    def _update_fit_hint(self) -> None:
        from app.pipeline import fit_estimate

        cur = self.speed.value() / 100
        share = fit_estimate.late_share(self._fit_cues, cur) if self._fit_cues else 0.0
        show = share > 0.15 and self._audio_mode() != "original"
        self.fit_hint.setVisible(show)
        self.fit_btn.setVisible(False)
        self._fit_rows = fit_estimate.late_indexes(self._fit_cues, cur) if show else []
        self.fit_lines_btn.setVisible(show and bool(self._fit_rows))
        self.fit_lines_btn.setText(f"Xem {len(self._fit_rows)} câu")
        if not show:
            return
        rate, then = fit_estimate.suggest_rate(self._fit_cues)
        self._fit_rate = rate
        msg = f"Người nói nhanh: ở {cur:.2f}× khoảng {share:.0%} câu sẽ trễ hoặc bị cắt."
        if rate > cur + 0.001:
            msg += f" Ở {rate:.2f}× còn khoảng {then:.0%}."
            self.fit_btn.setText(f"Dùng {rate:.2f}×")
            self.fit_btn.setVisible(True)
        else:
            msg += " Nên rút gọn thêm ở màn Sửa phụ đề."
        self.fit_hint.setText(msg)
        self.fit_hint.setStyleSheet(f"color:{V2['amber']};")

    def _on_fit_lines(self) -> None:
        if self._fit_rows:
            self.showLinesRequested.emit(list(self._fit_rows),
                                         f"Không kịp ở {self.speed.value() / 100:.2f}×")

    def set_rendering(self, on: bool) -> None:
        """Lock/unlock this screen around a render run. Every exit path (done,
        failed, cancelled) unlocks via MainWindow._on_finished — one point, so
        the app can't soft-lock."""
        self._rendering = on
        self.render_btn.setEnabled(not on)
        self.back_btn.setEnabled(not on)
        self.quick_btn.setEnabled(not on)
        self.later_btn.setEnabled(not on)
        self.cancel_btn.setVisible(on)
        self.cancel_btn.setEnabled(on)
        if on:
            self._right_stack.setCurrentIndex(1)
            self.render_btn.setText("Đang xử lý…")
            self.render_btn.setIcon(icon("LoaderCircle", "#ffffff", 16))
        else:
            self._tick.stop()
            self._update_lock()

    def _sync_frame_aspect(self, *_a) -> None:
        """"Dọc 9:16": the preview (and its platform safe zones) shows the 9:16 composition."""
        self.preview.set_frame_aspect(9 / 16 if self.frame.currentData() == "tiktok" else None)

    def set_preview(self, video_path: Path, cues: list | None = None) -> None:
        self.preview.load(video_path)
        if cues is not None:
            self.preview.set_cues(cues)
            self._fit_cues = list(cues)
            self._update_fit_hint()
        if self._right_stack.currentIndex() == 1:
            return
        self._right_stack.setCurrentIndex(2 if self._out and Path(self._out).exists() else 0)

    def set_style(self, style: dict, size_px: int, font: str) -> None:
        self.preview.set_style(style, size_px, font)
        self._glass_style = bool((style or {}).get("glass"))
        self._sync_cover()

    # --- the translator's own watermark (disclosure.watermark_events) -------------------
    WM_POS = (("tr", "Góc trên phải"), ("tl", "Góc trên trái"), ("br", "Góc dưới phải"),
              ("bl", "Góc dưới trái"), ("move", "Đổi góc mỗi 30 giây"))

    def _build_watermark(self, st: dict) -> QWidget:
        from app.pipeline import disclosure

        wm = {**disclosure.WM_DEFAULT, **(st.get("watermark") or {})}
        box = QWidget()
        box.setObjectName("bare")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(6)
        self.wm_cb = QCheckBox("Watermark của bạn (cả video)")
        self.wm_cb.setToolTip("Chữ của bạn (vd. tên kênh) in mờ ở góc video, suốt video. "
                              "Giữ nguyên dấu của tác giả gốc.")
        self.wm_cb.setChecked(bool(wm["on"]))
        v.addWidget(self.wm_cb)
        self.wm_body = QWidget()
        self.wm_body.setObjectName("bare")
        g = QGridLayout(self.wm_body)
        g.setContentsMargins(24, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.wm_text = QLineEdit(str(wm["text"]))
        self.wm_text.setPlaceholderText("Vd. Vietsub: Tên kênh của bạn")
        self.wm_text.setMaxLength(60)
        g.addWidget(label("Chữ", "secondary"), 0, 0)
        g.addWidget(self.wm_text, 0, 1, 1, 2)
        self.wm_pos = QComboBox()
        for key, text in self.WM_POS:
            self.wm_pos.addItem(text, key)
        self.wm_pos.setCurrentIndex(max(0, self.wm_pos.findData(wm["pos"])))
        self.wm_pos.setToolTip("“Đổi góc mỗi 30 giây”: khó bị cắt khỏi video hơn")
        g.addWidget(label("Vị trí", "secondary"), 1, 0)
        g.addWidget(self.wm_pos, 1, 1, 1, 2)
        self.wm_opacity = QSlider(Qt.Horizontal)
        self.wm_opacity.setRange(10, 100)
        self.wm_opacity.setValue(int(wm["opacity"]))
        self.wm_opacity_lbl = label("", "mono")
        g.addWidget(label("Độ rõ", "secondary"), 2, 0)
        g.addWidget(self.wm_opacity, 2, 1)
        g.addWidget(self.wm_opacity_lbl, 2, 2)
        self.wm_size = QSlider(Qt.Horizontal)
        self.wm_size.setRange(8, 30)
        self.wm_size.setValue(int(wm["size"]))
        self.wm_size_lbl = label("", "mono")
        g.addWidget(label("Cỡ chữ", "secondary"), 3, 0)
        g.addWidget(self.wm_size, 3, 1)
        g.addWidget(self.wm_size_lbl, 3, 2)
        g.setColumnStretch(1, 1)
        v.addWidget(self.wm_body)
        self.wm_cb.toggled.connect(self._on_watermark)
        self.wm_text.textChanged.connect(self._on_watermark)
        self.wm_pos.currentIndexChanged.connect(self._on_watermark)
        self.wm_opacity.valueChanged.connect(self._on_watermark)
        self.wm_size.valueChanged.connect(self._on_watermark)
        self._on_watermark(save=False)
        return box

    def watermark(self) -> dict:
        return {"on": self.wm_cb.isChecked(), "text": self.wm_text.text().strip(),
                "pos": self.wm_pos.currentData() or "tr", "opacity": self.wm_opacity.value(),
                "size": self.wm_size.value()}

    def _on_watermark(self, *_a, save: bool = True) -> None:
        wm = self.watermark()
        self.wm_body.setEnabled(wm["on"])
        self.wm_opacity_lbl.setText(f"{wm['opacity']}%")
        self.wm_size_lbl.setText(f"{wm['size']}px")
        if hasattr(self, "preview"):        # the preview is built after the options
            self.preview.set_watermark(wm if wm["on"] and wm["text"] else None)
        self._update_post_summary()
        if save:
            store.update_ui_state(watermark=wm)

    # --- series: "Tập 9" chip + "Còn tiếp" end card (disclosure.episode_events) ---------
    def _build_episode_card(self, st: dict) -> QWidget:
        from app.pipeline import disclosure

        card = {**disclosure.CARD_DEFAULT, **(st.get("episode_card") or {})}
        box = QWidget()
        box.setObjectName("bare")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(6)
        self.card_cb = QCheckBox("Thẻ tập: số tập ở đầu, “Còn tiếp” ở cuối")
        self.card_cb.setToolTip("Cho phim nhiều tập. Số tập lấy từ tiêu đề (《剧名》06, 第12集, "
                                "Tập 5…); {n} = tập này, {next} = tập sau. Video không có số tập "
                                "thì bỏ qua.")
        self.card_cb.setChecked(bool(card["on"]))
        v.addWidget(self.card_cb)
        self.card_body = QWidget()
        self.card_body.setObjectName("bare")
        g = QGridLayout(self.card_body)
        g.setContentsMargins(24, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.card_chip = QLineEdit(str(card["chip"]))
        self.card_chip.setPlaceholderText(disclosure.CARD_DEFAULT["chip"])
        self.card_chip.setMaxLength(60)
        g.addWidget(label("Đầu video", "secondary"), 0, 0)
        g.addWidget(self.card_chip, 0, 1)
        self.card_end = QLineEdit(str(card["end"]))
        self.card_end.setPlaceholderText(disclosure.CARD_DEFAULT["end"])
        self.card_end.setMaxLength(60)
        g.addWidget(label("4 giây cuối", "secondary"), 1, 0)
        g.addWidget(self.card_end, 1, 1)
        self.card_hint = label("", "small", wrap=True)
        g.addWidget(self.card_hint, 2, 0, 1, 2)
        g.setColumnStretch(1, 1)
        v.addWidget(self.card_body)
        self.card_cb.toggled.connect(self._on_episode_card)
        self.card_chip.textChanged.connect(self._on_episode_card)
        self.card_end.textChanged.connect(self._on_episode_card)
        self._on_episode_card(save=False)
        return box

    def episode_card(self) -> dict:
        return {"on": self.card_cb.isChecked(), "chip": self.card_chip.text().strip(),
                "end": self.card_end.text().strip()}

    def _on_episode_card(self, *_a, save: bool = True) -> None:
        from app.pipeline import compilation, disclosure

        card = self.episode_card()
        self.card_body.setEnabled(card["on"])
        title = compilation._job_title(self._job.work_dir) if self._job is not None else ""
        chip, end = disclosure.card_texts(card, title)
        self.card_hint.setText(f"Video này: “{chip}” … “{end}”" if chip or end else
                               "Tiêu đề video này không có số tập — sẽ không hiện thẻ.")
        self._update_post_summary()
        if save:
            store.update_ui_state(episode_card=card)

    # --- cover ↔ style, quality ------------------------------------------------------
    def _sync_cover(self) -> None:
        """“Kính mờ trắng” is a subtitle style AND a cover: with that style the cover is
        the glass (a black box under dark text would hide it); without it the glass
        cover can't be picked (white text on white glass can't be read)."""
        i = self.cover.findData("glass")
        item = self.cover.model().item(i) if i >= 0 else None
        if item is not None:
            item.setEnabled(self._glass_style)
        if self._glass_style:
            self.cover.setCurrentIndex(i)
            self.cover.setEnabled(False)
            self.cover.setToolTip("Kiểu chữ “Kính mờ trắng” tự làm mờ chữ Trung phía sau — đổi "
                                  "ở tab Kiểu chữ (màn Sửa phụ đề).")
        else:
            self.cover.setEnabled(True)
            self.cover.setToolTip("")
            if self.cover.currentData() == "glass":
                self.cover.setCurrentIndex(self.cover.findData("box"))

    def _on_quality(self, *_a) -> None:
        store.update_ui_state(render_res=self.res.currentData(),
                              render_quality=self.preset.currentData())
        self._sync_quality()

    def _sync_quality(self) -> None:
        """Grey out resolutions above the source (never upscale) and show a rough size."""
        from app.pipeline import assemble

        frame = self.frame.currentData()
        src = self._src_size
        for i in range(self.res.count()):
            t = assemble.RESOLUTIONS.get(self.res.itemData(i))
            item = self.res.model().item(i)
            if item is None:
                continue
            # 9:16 keeps today's 1080×1920 canvas at most; a source frame never grows
            ok = (not t or src is None or t <= min(src)) if frame != "tiktok" else True
            item.setEnabled(ok)
        cur = self.res.model().item(self.res.currentIndex())
        if cur is not None and not cur.isEnabled():
            self.res.blockSignals(True)          # not the user's choice: don't remember it
            self.res.setCurrentIndex(0)
            self.res.blockSignals(False)
        size = assemble.output_size(src, frame, self.res.currentData())
        mb = assemble.estimate_size_mb(src, frame, self.res.currentData(),
                                       self.preset.currentData(), self._src_dur)
        bits = []
        if size:
            bits.append(f"{size[0]}×{size[1]}")
        if mb:
            bits.append("ước tính ~" + (f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB")
                        .replace(".", ","))
        self.size_lbl.setText(" · ".join(bits))
        self._update_summary()

    def _probe_source(self, job) -> None:
        """Source size + length for the quality choices (ffprobe, off the UI thread)."""
        self._src_size, self._src_dur = None, 0.0
        self._sync_quality()
        src = getattr(job, "source_video", None)
        if src is None or not Path(src).exists():
            return

        def work():
            from app.pipeline import assemble

            return assemble.probe_size(Path(src)), assemble.probe_duration(Path(src))

        def done(r):
            if self._job is job:
                self._src_size, self._src_dur = r
                self._sync_quality()

        self._bg.run(work, done, lambda _m: None)

    def _toggle_focus(self) -> None:
        """Phóng to: hide / show the options column next to the video."""
        panel = self._split.widget(0)
        panel.setVisible(panel.isHidden())

    def _on_primary(self) -> None:
        if not self._approved:
            self.approveRequested.emit()
            return
        if self._batch:
            self._on_later()
            return
        self._emit_render()

    def _on_later(self) -> None:
        if not self._approved:
            self.approveRequested.emit()
            return
        self.renderLaterRequested.emit(self.collect_opts())

    def set_batch_active(self, on: bool) -> None:
        """A batch render is running in the background: "Dựng video" becomes "Thêm vào
        hàng dựng" (one render at a time) and the quick proof waits."""
        self._batch = on
        self._update_lock()

    def _on_quick(self) -> None:
        if not self._approved:
            self.approveRequested.emit()
            return
        self._emit_render(preview=True)

    def _emit_render(self, preview: bool = False) -> None:
        opts = self.collect_opts(preview)
        self._start_progress(opts)
        self.renderRequested.emit(opts)

    def collect_opts(self, preview: bool = False) -> dict:
        """The render settings chosen on this page (the job-independent part)."""
        speed = self.speed.value() / 100.0
        opts = {
            # the "(mặc định)" placeholder is UI text, not a voice id — sending it
            # to edge-tts as a literal voice name broke every render without voices()
            "voice": self.voice.currentData() if self._has_real_voices else None,
            "rate": f"{int(round((speed - 1) * 100)):+d}%",
            "voice_by_gender": self.by_gender_cb.isChecked() and self._has_real_voices,
            "audio_mode": self._audio_mode(),
            "orig_volume": self.orig_vol.value() / 100.0,
            "dub_volume": self.dub_vol.value() / 100.0,
            "cover_mode": self.cover.currentData(),
        }
        opts["frame"] = self.frame.currentData()   # E-16
        opts["tiktok_fill"] = self.fill.currentData() or "blur"
        opts["res"] = self.res.currentData()       # "Chất lượng" (the backend never upscales)
        opts["preset"] = self.preset.currentData()
        opts["credit_overlay"] = self.credit_cb.isChecked() and self.credit_cb.isEnabled()
        opts["ai_label"] = self.ai_label_cb.isChecked() and self._audio_mode() != "original"
        wm = self.watermark()
        opts["watermark"] = wm if wm["on"] and wm["text"] else None
        card = self.episode_card()
        opts["episode_card"] = card if card["on"] else None
        if preview:
            opts["preview"] = True                 # E-15: fast 640p check, same audio
        return opts

    def _start_progress(self, opts: dict) -> None:
        self._joined = None
        self.log.clear()
        self.preview.pause()
        self.result.pause()
        self._t0 = time.monotonic()
        self._tick.start()
        self.bar.setValue(0)
        self.p_badge.setText("0%")
        self.p_title.setText("Đang chuẩn bị…")
        self.p_detail.setText("")
        # without an engine the worker skips separation (assemble falls back to "duck")
        keep = opts.get("audio_mode") == "keep_music" and self._sep_ok
        self.stage_rows["separate"].setVisible(keep)
        self.stage_rows["tts"]._num = 2 if keep else 1
        self.stage_rows["assemble"]._num = 3 if keep else 2
        for r in self.stage_rows.values():
            r.set_state("wait", "Chờ đến lượt")
        self.status.setText("Kết quả sẽ được lưu vào thư mục của dự án.")
        self._right_stack.setCurrentIndex(1)
        if keep:
            self.stage_rows["separate"].set_state("active", "Đang tách giọng khỏi nhạc nền…")

    # --- progress API (driven by MainWindow) --------------------------------------
    def set_stage(self, key: str) -> None:
        order = ["separate", "tts", "assemble"]
        if key not in order:
            return
        for k in order[:order.index(key)]:
            r = self.stage_rows[k]
            if r.state in ("active", "wait") and r.isVisibleTo(self):
                r.set_state("done", "Hoàn tất")
        voice = self.voice.currentText()
        if key == "tts":
            self.p_title.setText("Đang tạo giọng tiếng Việt")
            self.stage_rows["tts"].set_state("active", f"{self._cues or '…'} câu · {voice}")
            self.stage_rows["assemble"].set_state("wait", "Chờ tạo giọng xong")
        elif key == "assemble":
            self.p_title.setText("Đang dựng MP4")
            self.stage_rows["tts"].set_state("done", "Hoàn tất")
            self.stage_rows["assemble"].set_state("active", "Ghép giọng, phụ đề và video")

    def set_progress(self, frac: float, msg: str) -> None:
        if frac >= 0:                      # < 0: only a message (e.g. waiting for the network)
            pct = int(frac * 100)
            self.bar.setValue(pct)
            self.p_badge.setText(f"{pct}%")
        self.p_detail.setText(msg)

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        if line.startswith("↷"):
            self.p_detail.setText(line.lstrip("↷ ").strip())

    def set_cancelling(self) -> None:
        self.status.setText("Đang hủy… (giọng đã tạo và các đoạn đã ghép được giữ lại)")
        self.cancel_btn.setEnabled(False)
        self.append_log("⏳ Đang hủy…")

    def show_done(self, path: str) -> None:
        quick = Path(path).name == "preview.mp4"
        if not quick:
            self._out = path
            self._show_commentary()          # the share measured by this render (F-4)
            self._out_frame = self.frame.currentData()
            self._saved = None
        self._show_result_kind(quick=quick)
        self._show_late()                    # after: _show_result_kind clears the status
        self._tick.stop()
        size = ""
        try:
            size = f"{os.path.getsize(path) / 1e6:.1f} MB".replace(".", ",")
        except OSError:
            pass
        dur = fmt_time(self.preview.duration(), 0) if self.preview.duration() else ""
        spent = ""
        if self._job is not None:
            from app import usage

            spent = usage.label(self._job.work_dir)     # E-14
        self.out_meta.setText(" · ".join(p for p in (Path(path).name, dur, size, spent) if p))
        self.result.load(Path(path))
        self._right_stack.setCurrentIndex(2)
        self.again_lbl.setVisible(True)
        self.render_btn.setEnabled(True)
        self._update_lock()

    def _show_result_kind(self, *, quick: bool) -> None:
        """The result pane shows either the real output or a quick preview; saving and
        the .mkv export only ever use the real output."""
        self._showing_quick = quick
        self._result_title.setText("Bản xem nhanh (640p) — nghe thử rồi bấm Dựng video" if quick
                                   else "Video tiếng Việt đã sẵn sàng")
        real = not quick
        self.save_btn.setEnabled(real)
        self.mkv_action.setEnabled(real)
        self.save_btn.setToolTip("Lưu một bản video, đặt tên theo tiêu đề dự án" if real else
                                 "Đây là bản xem nhanh — bấm Dựng video để có bản lưu được")
        self.res_status.setText("")

    def show_error(self, msg: str) -> None:
        self._tick.stop()
        cancelled = msg.strip().startswith("Đã hủy")
        for r in self.stage_rows.values():
            if r.state == "active":
                r.set_state("wait" if cancelled else "error",
                            "Đã dừng — bấm Dựng video để tiếp tục" if cancelled else "Lỗi ở bước này")
        self.p_title.setText("Đã dừng" if cancelled else "Chưa dựng được video")
        self.p_badge.setText("Đã dừng" if cancelled else "Lỗi")
        joining = not self.stage_rows["tts"].isVisibleTo(self) and self.stage_rows["assemble"]._num == 1
        self.p_detail.setText(("Các phần vẫn nguyên — bấm Ghép video đầy đủ để ghép lại."
                               if joining else "Giọng đã tạo được giữ lại.") if cancelled else msg)
        self.status.setText("Các câu đã tạo giọng được giữ lại cho lần dựng sau.")
        self.append_log(f"✕ {msg}")
        self.render_btn.setEnabled(True)

    def _on_tick(self) -> None:
        self.elapsed.setText(f"Đã chạy {fmt_time(time.monotonic() - self._t0, 0)}")

    # --- actions ------------------------------------------------------------------
    def _listen(self) -> None:
        if self._player is None or not self._approved:
            return
        from app.providers.registry import get_tts

        voice = self.voice.currentData() if self._has_real_voices else None
        rate = f"{int(round((self.speed.value() / 100 - 1) * 100)):+d}%"
        tts = self._tts
        out = Path(self._job.work_dir if self._job else ".") / "_voice_sample.mp3"
        self.listen_btn.setEnabled(False)
        # feedback next to the voice: self.status sits on the progress card, hidden here
        idle = f"Tiếng Việt · {tts}"
        self.voice_sub.setText("Đang tạo giọng mẫu…")
        self.voice_sub.setStyleSheet("")
        from PySide6.QtCore import QUrl

        self._player.setSource(QUrl())         # Windows: the player locks the old sample file

        def work():
            from app.providers.registry import split_voice

            prov, v = split_voice(tts, voice)          # "vieneu:Trúc Ly" → VieNeu
            get_tts(prov).synthesize(SAMPLE, out, voice=v, rate=rate)
            if not out.exists() or out.stat().st_size == 0:      # a dropped connection
                raise RuntimeError("máy chủ giọng đọc không trả âm thanh — thử lại")
            return str(out)

        def done(p):
            from PySide6.QtCore import QUrl

            self.listen_btn.setEnabled(True)
            self.voice_sub.setText(idle)
            self._player.setSource(QUrl.fromLocalFile(p))
            self._player.play()

        def fail(msg):
            self.listen_btn.setEnabled(True)
            self.voice_sub.setText(f"Không nghe thử được: {msg}")
            self.voice_sub.setStyleSheet(f"color:{V2['amber']};")

        self._bg.run(work, done, fail)

    # E-16: copies named after the project title, in the folder used last time
    def _ask_dest(self, caption: str, suffix: str, filt: str) -> Path | None:
        from app.pipeline.export import safe_name

        start = store.load_ui_state().get("export_dir") or ""
        if not start or not Path(start).is_dir():
            vids = Path.home() / "Videos"
            start = str(vids if vids.is_dir() else Path.home())
        stem = safe_name(self._title, self._job.id if self._job else "video")
        fn, _ = QFileDialog.getSaveFileName(self, caption, str(Path(start) / f"{stem}{suffix}"),
                                            filt)
        if not fn:
            return None
        store.update_ui_state(export_dir=str(Path(fn).parent))
        return Path(fn)

    def _save_video(self) -> None:
        if self._showing_quick or not self._out or not Path(self._out).exists():
            return
        tag = " (9x16)" if self._out_frame == "tiktok" else ""
        dest = self._ask_dest("Lưu video", f"{tag}.mp4", "Video MP4 (*.mp4)")
        if dest is None:
            return
        src = self._out
        self.save_btn.setEnabled(False)
        self.res_status.setText("Đang lưu…")

        def done(p):
            self._saved = p
            self.save_btn.setEnabled(True)
            self.res_status.setText(f"Đã lưu: {Path(p).name}")

        def fail(msg):
            self.save_btn.setEnabled(True)
            self.res_status.setText(f"Không lưu được: {msg}")

        self._bg.run(lambda: str(shutil.copyfile(src, dest)), done, fail)

    def _export(self, kind: str) -> None:
        if self._job is None:
            return
        rendered = self._job.work_dir / "output.srt"     # matches the video (F-4 pauses)
        src = ((rendered if rendered.exists() and self._out else self._job.vi_srt)
               if kind == "srt" else self._job.subs_ass)
        if not src.exists():
            self.res_status.setText("Chưa có tệp .ass — dựng video trước.")
            return
        dest = self._ask_dest(f"Xuất .{kind}", f".vi.{kind}", f"*.{kind}")
        if dest is None:
            return
        try:
            shutil.copyfile(src, dest)
            self.res_status.setText(f"Đã xuất: {dest.name}")
        except OSError as e:
            self.res_status.setText(f"Không xuất được: {e}")

    def _export_mkv(self) -> None:
        if self._job is None or self._showing_quick or not self._out:
            return
        dest = self._ask_dest("Xuất video phụ đề rời", ".mkv", "Video MKV (*.mkv)")
        if dest is None:
            return
        from app.pipeline import export

        job = self._job
        self.export_btn.setEnabled(False)
        self.res_status.setText("Đang xuất .mkv…")

        def done(p):
            self._saved = p
            self.export_btn.setEnabled(True)
            self.res_status.setText(f"Đã xuất: {Path(p).name}")

        def fail(msg):
            self.export_btn.setEnabled(True)
            self.res_status.setText(f"Không xuất được .mkv: {msg}")

        self._bg.run(lambda: str(export.export_softsub(job, dest)), done, fail)

    def _open_folder(self) -> None:
        """Explorer with the file selected: the last saved copy, else the result shown."""
        import subprocess

        shown = (str(Path(self._job.work_dir) / "preview.mp4") if self._showing_quick and self._job
                 else self._out)
        target = self._saved if self._saved and os.path.exists(self._saved) else shown
        if target and os.path.exists(target):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])  # noqa: S603,S607

    def shutdown(self) -> None:
        self._bg.shutdown()


def _hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{V2['line_soft']};")
    return f


VOICE_FILE = "voice.json"


def saved_rate(job) -> float | None:
    import json

    try:
        r = json.loads((Path(job.work_dir) / VOICE_FILE).read_text(encoding="utf-8")).get("rate")
        return float(r) if r else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _read_project_file(job) -> dict:
    import json

    try:
        d = json.loads((Path(job.work_dir) / VOICE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return {}
    return d if isinstance(d, dict) else {}


def _update_project_file(job, **fields) -> None:
    """Merge render choices into voice.json (voice, rate, audio_mode) — one writer must
    not drop what the other saved."""
    import json

    d = _read_project_file(job)
    d.update(fields)
    try:
        atomic_write_text((Path(job.work_dir) / VOICE_FILE), json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def saved_audio_mode(job) -> str | None:
    """The audio card the user picked for this project; the retired "mix" card maps to
    its successor "duck" (both keep the original under the dub)."""
    m = _read_project_file(job).get("audio_mode")
    if m == "mix":
        return "duck"
    return m if isinstance(m, str) and m else None


def saved_voice(job) -> str | None:
    import json

    try:
        return json.loads((Path(job.work_dir) / VOICE_FILE).read_text(encoding="utf-8")).get("voice")
    except (OSError, ValueError, AttributeError):
        return None
