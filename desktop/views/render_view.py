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
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.cuemodel import fmt_time
from desktop.icons import icon, pixmap
from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import ProjectBar, badge, button, compact_combo, icon_label, label
from desktop.widgets.pointer import glow
from desktop.widgets.video_panel import VideoPanel

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    _HAVE_MM = True
except Exception:  # noqa: BLE001
    _HAVE_MM = False

MODES = (("replace", "Mic", "Thay giọng gốc", "Chỉ nghe giọng Việt"),
         ("mix", "Volume2", "Trộn hai giọng", "Giữ tiếng gốc nhẹ"),
         ("duck", "SlidersHorizontal", "Giảm nền tự động", "Nền nhỏ khi có lời"),
         ("keep_music", "Music2", "Giữ nhạc nền", "AI tách lời gốc"),
         ("original", "Captions", "Giữ giọng gốc", "Không lồng tiếng, chỉ phụ đề — hợp video hát"))
FRAMES = (("Giữ nguyên khung hình gốc", "source"),
          ("Dọc 9:16 · TikTok / Reels / Shorts (1080×1920)", "tiktok"))
COVERS = (("Hộp đen · dễ đọc, che kín chữ gốc", "box"),
          ("Làm mờ dải dưới video · nhẹ nhàng", "blur"),
          ("Không che", "off"))
SAMPLE = "Xin chào, đây là giọng đọc tiếng Việt cho video của bạn."


def _voice_name(v: str) -> str:
    """vi-VN-HoaiMyNeural → Hoài My (edge ids), anything else unchanged."""
    known = {"HoaiMy": "Hoài My", "NamMinh": "Nam Minh"}
    core = v.replace("vi-VN-", "").replace("Neural", "")
    return known.get(core, core or v)


class ModeCard(QPushButton):
    """A checkable card: icon · title · one-line description."""

    def __init__(self, ic: str, title: str, desc: str) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("modeCard")
        self.setMinimumHeight(54)
        self.setStyleSheet(
            f"QPushButton#modeCard{{text-align:left;background:{V2['panel']};"
            f"border:1px solid {V2['line']};border-radius:7px;padding:0;min-height:54px;}}"
            f"QPushButton#modeCard:hover{{border-color:#525161;}}"
            f"QPushButton#modeCard:checked{{background:{V2['purple_soft']};"
            f"border-color:{V2['purple_line']};}}")
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
        col.addWidget(self._title)
        col.addWidget(self._desc)
        h.addLayout(col, 1)
        self._ic.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.toggled.connect(self._paint_state)
        self._paint_state(False)
        glow(self, corner=7)

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
    backRequested = Signal()
    cancelRequested = Signal()
    approveRequested = Signal()        # locked state → re-approve
    joinRequested = Signal(str)        # a split video's group id → join the rendered parts

    def __init__(self) -> None:
        super().__init__()
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

        body = QHBoxLayout()
        body.setContentsMargins(18, 4, 18, 14)
        body.setSpacing(14)
        body.addWidget(self._left(), 0)
        body.addWidget(self._right(), 1)
        root.addLayout(body, 1)
        self._update_lock()

    # --- left: options ---------------------------------------------------------
    def _left(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(440)
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
        self.voice_sub = label("Tiếng Việt", "small")
        vc.addWidget(self.voice)
        vc.addWidget(self.voice_sub)
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

        ll.addWidget(_hline())
        ll.addWidget(label("Âm thanh bạn muốn nghe", "h3"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.audio_group = QButtonGroup(self)
        self.audio_group.setExclusive(True)
        self.mode_cards: dict[str, ModeCard] = {}
        for i, (key, ic, title, desc) in enumerate(MODES):
            mc = ModeCard(ic, title, desc)
            if key == "keep_music":
                mc.setToolTip("Tách lời khỏi nhạc nền bằng Demucs, thay lời bằng bản lồng tiếng "
                              "nhưng GIỮ nhạc / hiệu ứng. Cần cài Demucs (.[audio]); nếu chưa tách "
                              "được sẽ tự chuyển sang trộn.")
            self.audio_group.addButton(mc)
            self.mode_cards[key] = mc
            if key == "original":
                grid.addWidget(mc, i // 2, 0, 1, 2)        # full-width last row
            else:
                grid.addWidget(mc, i // 2, i % 2)
        self.mode_cards["replace"].setChecked(True)
        self.audio_group.buttonToggled.connect(lambda *_: self._sync_audio_enabled())
        ll.addLayout(grid)
        self.orig_vol, self.orig_vol_lbl = self._slider(ll, "Âm lượng gốc", 0, 150, 100,
                                                        lambda v: f"{v}%")
        self.dub_vol, self.dub_vol_lbl = self._slider(ll, "Giọng tiếng Việt", 50, 150, 100,
                                                      lambda v: f"{v}%")
        self._sync_audio_enabled()

        ll.addWidget(_hline())
        ll.addWidget(label("Che phụ đề Trung trên video gốc", "h3"))
        self.cover = QComboBox()
        for text, key in COVERS:
            self.cover.addItem(text, key)
        ll.addWidget(self.cover)
        ll.addWidget(label("Khung hình", "h3"))
        self.frame = QComboBox()
        for text, key in FRAMES:
            self.frame.addItem(text, key)
        self.frame.setToolTip("Dọc 9:16: video được thu vừa khung 1080×1920 rồi thêm viền đen — "
                              "đăng thẳng lên TikTok/Reels. Video Douyin vốn đã dọc thì gần như "
                              "không có viền.")
        i = self.frame.findData(store.load_ui_state().get("render_frame"))
        self.frame.setCurrentIndex(max(i, 0))
        self.frame.currentIndexChanged.connect(
            lambda _i: store.update_ui_state(render_frame=self.frame.currentData()))
        ll.addWidget(self.frame)
        ll.addWidget(label("Kiểu chữ lấy từ màn Sửa phụ đề.", "small", wrap=True))

        # F-1/F-2/F-3: credit, AI-voice label, right to use (docs/plan/F-roadmap.md)
        ll.addWidget(_hline())
        ll.addWidget(label("Đăng tải", "h3"))
        st = store.load_ui_state()
        self.credit_cb = QCheckBox("Ghi nguồn trên video (5 giây đầu)")
        self.credit_cb.setChecked(bool(st.get("credit_overlay", True)))
        self.credit_cb.toggled.connect(lambda on: store.update_ui_state(credit_overlay=on))
        self.ai_label_cb = QCheckBox("Nhãn “Lồng tiếng AI” (5 giây đầu)")
        self.ai_label_cb.setChecked(bool(st.get("ai_label", True)))
        self.ai_label_cb.setToolTip(
            "Một nhãn nhỏ ở góc trên. TikTok yêu cầu gắn nhãn nội dung AI chân thực; Luật AI "
            "134/2025 yêu cầu khi giọng AI mô phỏng người thật. Không ảnh hưởng chất lượng.")
        self.ai_label_cb.toggled.connect(lambda on: store.update_ui_state(ai_label=on))
        ll.addWidget(self.credit_cb)
        ll.addWidget(self.ai_label_cb)
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
        for combo in (self.voice, self.cover, self.frame):
            compact_combo(combo, 10)
        compact_combo(self.rights, 6)            # shares its row with a caption + button
        rrow.addWidget(self.rights, 1)
        self.proof_btn = button("Bằng chứng…", "Upload", variant="ghost", small=True,
                                tip="Đính kèm ảnh chụp / email cho phép — được chép vào thư mục dự án")
        self.proof_btn.clicked.connect(self._attach_proof)
        rrow.addWidget(self.proof_btn)
        ll.addLayout(rrow)
        self.rights_note = label("", "small", wrap=True)
        ll.addWidget(self.rights_note)
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
        self.render_btn = button("Dựng video", "MonitorPlay", variant="primary")
        self.render_btn.setStyleSheet("QPushButton{min-height:40px;}")
        self.render_btn.clicked.connect(self._on_primary)
        self.quick_btn = button("Xem nhanh", "Eye", variant="ghost",
                                tip="Dựng nhanh bản chất lượng thấp (640p) để nghe thử toàn bộ lồng "
                                    "tiếng trước. Giọng đã tạo được giữ — dựng thật sau nhanh hơn.")
        self.quick_btn.setStyleSheet("QPushButton{min-height:40px;}")
        self.quick_btn.clicked.connect(self._on_quick)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        prow.addWidget(self.quick_btn)
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
        rv.addWidget(self.result, 1)
        acts = QFrame()
        acts.setObjectName("panel")
        al = QHBoxLayout(acts)
        al.setContentsMargins(14, 10, 14, 10)
        al.setSpacing(8)
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
        for b in (self.save_btn, self.open_btn, self.export_btn, self.post_btn, back):
            al.addWidget(b)
        al.addStretch(1)
        self.res_status = label("", "small")
        al.addWidget(self.res_status)
        rv.addSpacing(8)
        rv.addWidget(acts)
        self._right_stack.addWidget(res)
        return w

    # --- state ---------------------------------------------------------------------
    def set_project(self, title: str, meta: str) -> None:
        self._title = title
        self.project.set_project(title, meta)

    def set_job(self, job, *, cues: int, tts: str = "") -> None:
        if job is not self._job:
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
            self.render_btn.setText(("Dựng lại" if self._out else "Dựng video") if ok else
                                    "Duyệt phụ đề")
            self.render_btn.setIcon(icon("MonitorPlay" if ok else "ShieldCheck", "#ffffff", 16))
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

        if self._job is None:
            return
        disclosure.save_rights(self._job, self.rights.currentData() or "unknown")
        self._show_rights_note(self.rights.currentData() or "unknown")

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
        return len(notes), commentary.estimate(notes, dur)[1] if notes else 0.0

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
        from app.pipeline import disclosure
        from desktop.widgets.dialogs import PostDialog

        if self._job is None:
            return
        ai = self._audio_mode() != "original"
        text = disclosure.post_description(self._job, ai_voice=ai)
        rights = disclosure.load_rights(self._job).get("basis", "unknown")
        credit = self.credit_cb.isChecked() and self.credit_cb.isEnabled()
        PostDialog(self, text=text, checklist=disclosure.checklist(
            ai_voice=ai, credit=credit or bool(disclosure.credit_line(self._job)),
            rights=rights, commentary=self._commentary_stats())).exec()

    def _sync_audio_enabled(self) -> None:
        mode = self._audio_mode()
        keep = mode not in ("replace", "original")
        self.orig_vol.setEnabled(keep)
        self.orig_vol_lbl.setEnabled(keep)
        dub = mode != "original"
        for w in (self.dub_vol, self.dub_vol_lbl, self.voice, self.speed, self.speed_lbl):
            w.setEnabled(dub)
        if hasattr(self, "ai_label_cb"):           # built after the audio cards
            self.ai_label_cb.setEnabled(dub)

    def suggest_original(self, singing: str | None) -> None:
        """E-8: a mostly-sung video starts on "Giữ giọng gốc" (once per job; the user's
        own choice afterwards is kept)."""
        card = self.mode_cards["original"]
        card.setToolTip("AI nghe thấy video chủ yếu là hát — nên giữ giọng hát gốc."
                        if singing == "yes" else "")
        if singing == "yes" and not self._suggested:
            self._suggested = True
            card.setChecked(True)

    def _audio_mode(self) -> str:
        for key, card in self.mode_cards.items():
            if card.isChecked():
                return key
        return "replace"

    def set_voices(self, voices: list[str]) -> None:
        self.voice.clear()
        self._has_real_voices = bool(voices)
        for v in voices or ["(mặc định)"]:
            self.voice.addItem(_voice_name(v) if voices else v, v)

    def set_rendering(self, on: bool) -> None:
        """Lock/unlock this screen around a render run. Every exit path (done,
        failed, cancelled) unlocks via MainWindow._on_finished — one point, so
        the app can't soft-lock."""
        self._rendering = on
        self.render_btn.setEnabled(not on)
        self.back_btn.setEnabled(not on)
        self.quick_btn.setEnabled(not on)
        self.cancel_btn.setVisible(on)
        self.cancel_btn.setEnabled(on)
        if on:
            self._right_stack.setCurrentIndex(1)
            self.render_btn.setText("Đang xử lý…")
            self.render_btn.setIcon(icon("LoaderCircle", "#ffffff", 16))
        else:
            self._tick.stop()
            self._update_lock()

    def set_preview(self, video_path: Path, cues: list | None = None) -> None:
        self.preview.load(video_path)
        if cues is not None:
            self.preview.set_cues(cues)
        if self._right_stack.currentIndex() == 1:
            return
        self._right_stack.setCurrentIndex(2 if self._out and Path(self._out).exists() else 0)

    def set_style(self, style: dict, size_px: int, font: str) -> None:
        self.preview.set_style(style, size_px, font)

    def _on_primary(self) -> None:
        if not self._approved:
            self.approveRequested.emit()
            return
        self._emit_render()

    def _on_quick(self) -> None:
        if not self._approved:
            self.approveRequested.emit()
            return
        self._emit_render(preview=True)

    def _emit_render(self, preview: bool = False) -> None:
        speed = self.speed.value() / 100.0
        opts = {
            # the "(mặc định)" placeholder is UI text, not a voice id — sending it
            # to edge-tts as a literal voice name broke every render without voices()
            "voice": self.voice.currentData() if self._has_real_voices else None,
            "rate": f"{int(round((speed - 1) * 100)):+d}%",
            "audio_mode": self._audio_mode(),
            "orig_volume": self.orig_vol.value() / 100.0,
            "dub_volume": self.dub_vol.value() / 100.0,
            "cover_mode": self.cover.currentData(),
        }
        opts["frame"] = self.frame.currentData()   # E-16
        opts["credit_overlay"] = self.credit_cb.isChecked() and self.credit_cb.isEnabled()
        opts["ai_label"] = self.ai_label_cb.isChecked() and self._audio_mode() != "original"
        if preview:
            opts["preview"] = True                 # E-15: fast 640p check, same audio
        self._start_progress(opts)
        self.renderRequested.emit(opts)

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
        keep = opts.get("audio_mode") == "keep_music"
        self.stage_rows["separate"].setVisible(keep)
        self.stage_rows["tts"]._num = 2 if keep else 1
        self.stage_rows["assemble"]._num = 3 if keep else 2
        for r in self.stage_rows.values():
            r.set_state("wait", "Chờ đến lượt")
        self.status.setText("Kết quả sẽ được lưu vào thư mục của dự án.")
        self._right_stack.setCurrentIndex(1)
        if keep:
            self.stage_rows["separate"].set_state("active", "Đang tách nhạc nền (Demucs)…")

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

        def work():
            get_tts(tts).synthesize(SAMPLE, out, voice=voice, rate=rate)
            return str(out)

        def done(p):
            from PySide6.QtCore import QUrl

            self.listen_btn.setEnabled(True)
            self._player.setSource(QUrl.fromLocalFile(p))
            self._player.play()

        def fail(msg):
            self.listen_btn.setEnabled(True)
            self.status.setText(f"Không nghe thử được: {msg}")

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
