"""Two pages next to the translation flow (top bar: «Tạo audio» and «Video audio»):

* ``AudioPage`` — a script (.txt or pasted) → ONE mp3 in the chosen voice and speed. No picture,
  no video encode: the owner puts the file under a video in their own editor.
* ``ScriptVideoPage`` — the sound (the script spoken, or an audio file they already have) over a
  few clips (played in order, looped until the sound ends) or one held picture, with the
  translator's watermark, the voice and the speed chosen here; the first frame is shown BEFORE
  anything is built.

Both are ordinary pages of the main window's stack, not dialogs: a build runs on its own daemon
thread (``BgRunner``) and goes on while the owner opens another tab, translates a video or starts the
other page's build. Pipeline: app/pipeline/scriptvideo.py."""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import store
from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import badge, button, label

VIDEO_FILTER = "Video (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.flv *.ts)"
IMAGE_FILTER = "Ảnh (*.jpg *.jpeg *.png *.webp *.bmp)"
AUDIO_FILTER = "Âm thanh (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus)"
WM_POSITIONS = (("tr", "Góc trên phải"), ("tl", "Góc trên trái"), ("br", "Góc dưới phải"),
                ("bl", "Góc dưới trái"), ("move", "Đổi góc mỗi 30 giây"))


def _panel(title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("panel")
    v = QVBoxLayout(box)
    v.setContentsMargins(16, 12, 16, 14)
    v.setSpacing(8)
    v.addWidget(label(title, "h3"))
    if hint:
        v.addWidget(label(hint, "secondary", wrap=True))
    return box, v


class _Page(QWidget):
    """What both pages share: the script box, the voice, a progress bar and the result buttons."""

    def __init__(self, title: str, intro: str, *, tts=None) -> None:
        super().__init__()
        from app.config import settings

        self._tts_now = tts or (lambda: settings.vizsup_default_tts)
        self._bg = BgRunner(self)
        self._cancel = threading.Event()
        self._busy = False
        self._progress_state = ("", 0.0)
        self.result_path: Path | None = None
        self.work_dir: Path | None = None
        self._voices_for: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label(title, "h1"))
        head.addStretch(1)
        self.running = badge("Đang chạy…", "purple")
        self.running.setVisible(False)
        head.addWidget(self.running)
        root.addLayout(head)
        root.addWidget(label(intro, "secondary", wrap=True))
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        root.addLayout(self.body)
        root.addStretch(1)
        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._paint_progress)

    # --- widgets both use --------------------------------------------------------------
    def _script_box(self, placeholder: str) -> tuple[QFrame, QPlainTextEdit]:
        box, v = _panel("Kịch bản")
        row = QHBoxLayout()
        self.count = label("", "small")
        row.addWidget(self.count, 1)
        self.open_btn = button("Mở tệp .txt…", "FileText", variant="ghost", small=True)
        self.open_btn.clicked.connect(self._open_txt)
        row.addWidget(self.open_btn)
        v.addLayout(row)
        self.script = QPlainTextEdit()
        self.script.setPlaceholderText(placeholder)
        self.script.setMinimumHeight(200)
        self.script.textChanged.connect(self._script_changed)
        v.addWidget(self.script)
        return box, self.script

    def _voice_box(self) -> QFrame:
        box, v = _panel("Giọng đọc")
        row = QHBoxLayout()
        row.addWidget(label("Giọng", "small"))
        self.voice = QComboBox()
        row.addWidget(self.voice, 1)
        row.addWidget(label("Tốc độ", "small"))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(70, 150)
        self.speed.setFixedWidth(150)
        st = store.load_ui_state()
        self.speed.setValue(int(round(float(st.get("render_rate") or 1.0) * 100)))
        self.speed_lbl = label("", "monoText")
        self.speed.valueChanged.connect(lambda _v: self.speed_lbl.setText(f"{self.speed.value()}%"))
        self.speed_lbl.setText(f"{self.speed.value()}%")
        row.addWidget(self.speed)
        row.addWidget(self.speed_lbl)
        v.addLayout(row)
        self.fill_voices()
        return box

    def fill_voices(self) -> None:
        """The voices of the TTS engine chosen on the start page (re-read when it changed)."""
        from app.providers import registry

        tts = self._tts_now()
        if tts == self._voices_for and self.voice.count():
            return
        self._voices_for = tts
        keep = self.voice.currentData() or store.load_ui_state().get("render_voice")
        voices: list[str] = []
        try:
            voices = list(registry.voice_choices(tts))
        except Exception:  # noqa: BLE001 - a provider that cannot list: its default voice
            pass
        self.voice.clear()
        for v in voices or ["(mặc định)"]:
            self.voice.addItem(registry.voice_label(v) if voices else v, v if voices else None)
        i = self.voice.findData(keep)
        if i >= 0:
            self.voice.setCurrentIndex(i)

    def _progress_box(self) -> None:
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        self.status = label("", "small", wrap=True)
        self.body.addWidget(self.bar)
        self.body.addWidget(self.status)

    def _rate(self) -> str | None:
        pct = self.speed.value()
        return f"{pct - 100:+d}%" if pct != 100 else None

    # --- script text -------------------------------------------------------------------
    def _script_changed(self) -> None:
        from app.pipeline import scriptvideo

        text = self.script.toPlainText()
        n = len(scriptvideo.split_script(text))
        words = len(text.split())
        self.count.setText(f"{n} câu · {words} từ · khoảng {words / 150:.1f} phút giọng đọc"
                           if n else "")
        self._refresh_buttons()

    def _open_txt(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Mở kịch bản", str(Path.home()), "Văn bản (*.txt)")
        if not fn:
            return
        try:
            self.script.setPlainText(Path(fn).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as exc:
            self.status.setText(f"✗ Không đọc được tệp: {exc}")

    # --- running ----------------------------------------------------------------------------
    def is_busy(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.running.setVisible(busy)
        self.bar.setVisible(busy or self.bar.value() > 0)
        self._lock(busy)
        self.go_btn.setText("Hủy" if busy else self._go_text)
        if busy:
            self._tick.start()
        else:
            self._tick.stop()
        self._refresh_buttons()
        self.busyChanged.emit(busy)

    def _paint_progress(self) -> None:
        stage, frac = self._progress_state
        self.bar.setValue(int(frac * 1000))
        self.status.setText(f"{stage}… {frac * 100:.0f}%")

    def _start(self, work, done) -> None:
        self._cancel.clear()
        self.result_path = None
        self._progress_state = ("Bắt đầu", 0.0)
        self.bar.setValue(0)
        self.status.setText("Đang chạy…")
        self._set_busy(True)
        self._bg.run(work, lambda p: (self._set_busy(False), self.bar.setValue(1000), done(p)),
                     self._failed)

    def _failed(self, msg: str) -> None:
        self._set_busy(False)
        self.bar.setVisible(False)
        self.status.setText("Đã hủy." if "hủy" in msg.lower() or "InterruptedError" in msg
                            else f"✗ {msg}")

    def _prog(self, stage: str, frac: float) -> None:
        self._progress_state = (stage, float(frac))

    def _open_folder(self) -> None:
        if self.work_dir:
            try:
                os.startfile(str(self.work_dir))      # noqa: S606 - Windows app
            except (OSError, AttributeError):
                pass

    # --- implemented by the pages -------------------------------------------------------
    busyChanged = Signal(bool)
    _go_text = "Bắt đầu"

    def _lock(self, busy: bool) -> None:        # pragma: no cover - overridden
        raise NotImplementedError

    def _refresh_buttons(self) -> None:         # pragma: no cover - overridden
        raise NotImplementedError


# --- 2.1 Tạo audio từ text ------------------------------------------------------------------

class AudioPage(_Page):
    useForVideo = Signal(str)          # the mp3 → the Video audio page

    _go_text = "Tạo audio"

    def __init__(self, *, tts=None) -> None:
        super().__init__(
            "Tạo audio từ text",
            "Dán hoặc mở kịch bản (.txt), chọn giọng và tốc độ — app đọc và lưu thành MỘT file mp3. "
            "Không ghép hình, nên chỉ mất vài chục giây. Bạn ghép file này vào video bằng "
            "phần mềm của bạn, hoặc dùng ngay ở tab «Video audio».", tts=tts)
        box, _ = self._script_box("Mỗi đoạn một dòng. Dòng bắt đầu bằng # là ghi chú, không đọc.")
        self.body.addWidget(box)
        self.body.addWidget(self._voice_box())
        self._progress_box()
        row = QHBoxLayout()
        self.go_btn = button(self._go_text, "AudioLines", variant="primary")
        self.go_btn.clicked.connect(self._go)
        row.addWidget(self.go_btn)
        self.save_btn = button("Lưu mp3…", "Download", variant="ghost")
        self.save_btn.clicked.connect(self._save_mp3)
        self.srt_btn = button("Lưu thời gian từng câu (.srt)…", "Captions", variant="ghost",
                              tip="Phụ đề ăn khớp với giọng đọc, để dán vào CapCut / Premiere")
        self.srt_btn.clicked.connect(self._save_srt)
        self.use_btn = button("Dựng video từ audio này", "Film", variant="ghost")
        self.use_btn.clicked.connect(lambda: self.result_path and self.useForVideo.emit(
            str(self.result_path)))
        self.folder_btn = button("Mở thư mục", "FolderOpen", variant="ghost")
        self.folder_btn.clicked.connect(self._open_folder)
        for b in (self.save_btn, self.srt_btn, self.use_btn, self.folder_btn):
            b.setVisible(False)
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        self._refresh_buttons()

    def _lock(self, busy: bool) -> None:
        for w in (self.script, self.open_btn, self.voice, self.speed):
            w.setEnabled(not busy)

    def _refresh_buttons(self) -> None:
        self.go_btn.setEnabled(self._busy or bool(self.script.toPlainText().strip()))

    def _go(self) -> None:
        from app.pipeline import scriptvideo

        if self._busy:
            self._cancel.set()
            self.status.setText("Đang hủy…")
            return
        script, voice, rate = self.script.toPlainText(), self.voice.currentData(), self._rate()
        tts = self._tts_now()
        store.update_ui_state(render_voice=voice, render_rate=self.speed.value() / 100)
        for b in (self.save_btn, self.srt_btn, self.use_btn, self.folder_btn):
            b.setVisible(False)
        self.work_dir = scriptvideo.new_dir()
        wd, cancel = self.work_dir, self._cancel

        def work():
            return scriptvideo.make_audio(wd, script, provider=tts, voice=voice, rate=rate,
                                          progress=self._prog, cancel=cancel.is_set)

        self._start(work, self._done)

    def _done(self, path) -> None:
        self.result_path = Path(path)
        self.status.setText(f"✓ Xong: {self.result_path}")
        for b in (self.save_btn, self.srt_btn, self.use_btn, self.folder_btn):
            b.setVisible(True)

    def _save_mp3(self) -> None:
        if not self.result_path:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Lưu mp3", str(Path.home() / "giong-doc.mp3"),
                                            "MP3 (*.mp3)")
        if fn:
            self._copy(self.result_path, fn)

    def _save_srt(self) -> None:
        srt = self.result_path.with_name("script.srt") if self.result_path else None
        if not srt or not srt.exists():
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Lưu phụ đề", str(Path.home() / "giong-doc.srt"),
                                            "SubRip (*.srt)")
        if fn:
            self._copy(srt, fn)

    def _copy(self, src: Path, dest: str) -> None:
        try:
            shutil.copy2(src, dest)
            self.status.setText(f"✓ Đã lưu {dest}")
        except OSError as exc:
            self.status.setText(f"✗ Không lưu được: {exc}")


# --- 2.2 Tạo video audio -------------------------------------------------------------------

class ScriptVideoPage(_Page):
    _go_text = "Dựng video"

    def __init__(self, *, tts=None) -> None:
        super().__init__(
            "Tạo video audio",
            "Ghép âm thanh với video hoặc ảnh. Nhiều video: chạy lần lượt rồi quay lại video đầu "
            "cho tới khi âm thanh hết. Một ảnh: giữ nguyên suốt video. Không đốt chữ vào hình nên "
            "dựng nhanh; chỉ có watermark nếu bạn bật.", tts=tts)
        self.backgrounds: list[Path] = []
        self.audio_file: Path | None = None

        # 1 — the sound
        sbox, sv = _panel("Âm thanh")
        mrow = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Đọc từ kịch bản", "script")
        self.mode.addItem("Dùng file audio có sẵn", "audio")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        mrow.addWidget(self.mode)
        mrow.addStretch(1)
        sv.addLayout(mrow)
        self.sound = QStackedWidget()
        script_box, _ = self._script_box("Mỗi đoạn một dòng. Dòng bắt đầu bằng # là ghi chú, không đọc.")
        script_box.setStyleSheet("QFrame#panel{border:none;background:transparent;}")
        self.sound.addWidget(script_box)
        arow = QFrame()
        al = QHBoxLayout(arow)
        al.setContentsMargins(0, 0, 0, 0)
        self.audio_lbl = label("Chưa chọn file.", "secondary")
        al.addWidget(self.audio_lbl, 1)
        self.audio_btn = button("Chọn file audio…", "AudioLines", variant="ghost", small=True)
        self.audio_btn.clicked.connect(self._pick_audio)
        al.addWidget(self.audio_btn)
        self.sound.addWidget(arow)
        sv.addWidget(self.sound)
        self.body.addWidget(sbox)
        self.voice_box = self._voice_box()
        self.body.addWidget(self.voice_box)

        # 2 — the picture
        bbox, bv = _panel("Hình nền")
        self.bg_list = QListWidget()
        self.bg_list.setMaximumHeight(110)
        self.bg_list.setToolTip("Thứ tự trong danh sách = thứ tự phát. Kéo thả để đổi chỗ.")
        self.bg_list.setDragDropMode(QListWidget.InternalMove)
        self.bg_list.model().rowsMoved.connect(lambda *_a: self._bg_changed())
        bv.addWidget(self.bg_list)
        brow = QHBoxLayout()
        self.add_vid = button("Thêm video…", "Film", variant="ghost", small=True)
        self.add_vid.clicked.connect(self._add_videos)
        self.add_img = button("Chọn ảnh…", "Image", variant="ghost", small=True)
        self.add_img.clicked.connect(self._pick_image)
        self.rm = button("Bỏ mục chọn", "X", variant="ghost", small=True)
        self.rm.clicked.connect(self._remove)
        for b in (self.add_vid, self.add_img, self.rm):
            brow.addWidget(b)
        brow.addStretch(1)
        bv.addLayout(brow)
        self.bg_hint = label("", "small", wrap=True)
        bv.addWidget(self.bg_hint)
        self.body.addWidget(bbox)

        # 3 — the watermark (as on the render page) + the first frame
        wbox, wv = _panel("Watermark và khung hình",
                          "Khung hình bên dưới là hình đầu tiên của video — chữ nằm đúng chỗ như khi dựng.")
        from app.pipeline import disclosure

        saved = store.load_ui_state()
        wm = {**disclosure.WM_DEFAULT, **(saved.get("sv_watermark") or saved.get("watermark") or {})}
        wrow = QHBoxLayout()
        self.wm_on = QCheckBox("Watermark")
        self.wm_on.setChecked(bool(wm.get("on")))
        self.wm_text = QLineEdit(str(wm.get("text") or ""))
        self.wm_text.setPlaceholderText("Tên kênh / dòng chữ")
        self.wm_pos = QComboBox()
        for key, text in WM_POSITIONS:
            self.wm_pos.addItem(text, key)
        self.wm_pos.setCurrentIndex(max(0, self.wm_pos.findData(wm.get("pos") or "tr")))
        wrow.addWidget(self.wm_on)
        wrow.addWidget(self.wm_text, 1)
        wrow.addWidget(self.wm_pos)
        wv.addLayout(wrow)
        wrow2 = QHBoxLayout()
        wrow2.addWidget(label("Độ đậm", "small"))
        self.wm_opacity = QSlider(Qt.Horizontal)
        self.wm_opacity.setRange(5, 100)
        self.wm_opacity.setValue(int(wm.get("opacity", 35)))
        self.wm_opacity.setFixedWidth(140)
        wrow2.addWidget(self.wm_opacity)
        wrow2.addSpacing(12)
        wrow2.addWidget(label("Cỡ chữ", "small"))
        self.wm_size = QSpinBox()
        self.wm_size.setRange(8, 60)
        self.wm_size.setSuffix(" px")
        self.wm_size.setValue(int(wm.get("size", 14)))
        wrow2.addWidget(self.wm_size)
        wrow2.addStretch(1)
        wv.addLayout(wrow2)
        self.frame_lbl = label("", "secondary")
        self.frame_lbl.setAlignment(Qt.AlignCenter)
        self.frame_lbl.setMinimumHeight(240)
        self.frame_lbl.setStyleSheet(f"background:{V2['input']};border-radius:6px;")
        wv.addWidget(self.frame_lbl)
        self.body.addWidget(wbox)
        self._still_timer = QTimer(self)
        self._still_timer.setSingleShot(True)
        self._still_timer.setInterval(450)
        self._still_timer.timeout.connect(self._cut_still)
        self._still_key = None
        self._still_busy = False
        self._still_dir: Path | None = None
        for sig in (self.wm_on.toggled, self.wm_text.textChanged, self.wm_pos.currentIndexChanged,
                    self.wm_opacity.valueChanged, self.wm_size.valueChanged):
            sig.connect(lambda *_a: self._wm_changed())

        # 4 — options + go
        orow = QHBoxLayout()
        self.vertical = QCheckBox("Dựng thêm bản dọc 9:16 (lâu hơn)")
        orow.addWidget(self.vertical)
        orow.addStretch(1)
        self.body.addLayout(orow)
        self._progress_box()
        row = QHBoxLayout()
        self.go_btn = button(self._go_text, "Play", variant="primary")
        self.go_btn.clicked.connect(self._go)
        row.addWidget(self.go_btn)
        self.save_btn = button("Lưu video…", "Download", variant="ghost")
        self.save_btn.clicked.connect(self._save_as)
        self.folder_btn = button("Mở thư mục", "FolderOpen", variant="ghost")
        self.folder_btn.clicked.connect(self._open_folder)
        for b in (self.save_btn, self.folder_btn):
            b.setVisible(False)
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        self._mode_changed()
        self._script_changed()
        self._bg_changed()

    # --- the sound -----------------------------------------------------------------------
    def set_audio(self, path: str) -> None:
        """«Dựng video từ audio này» on the other page: use that mp3."""
        self.audio_file = Path(path)
        self.audio_lbl.setText(self.audio_file.name)
        self.mode.setCurrentIndex(self.mode.findData("audio"))
        self._refresh_buttons()

    def _pick_audio(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn file audio", str(Path.home()), AUDIO_FILTER)
        if fn:
            self.set_audio(fn)

    def _from_audio(self) -> bool:
        return self.mode.currentData() == "audio"

    def _mode_changed(self) -> None:
        self.sound.setCurrentIndex(1 if self._from_audio() else 0)
        self.voice_box.setVisible(not self._from_audio())
        self._refresh_buttons()

    # --- the picture -----------------------------------------------------------------------
    def _is_image(self) -> bool:
        return bool(self.backgrounds) and self.backgrounds[0].suffix.lower() in (
            ".jpg", ".jpeg", ".png", ".webp", ".bmp")

    def _set_backgrounds(self, paths: list[Path]) -> None:
        self.backgrounds = list(paths)
        self.bg_list.clear()
        for p in paths:
            self.bg_list.addItem(p.name)
            self.bg_list.item(self.bg_list.count() - 1).setData(Qt.UserRole, str(p))
        self._bg_changed()

    def _sync_from_list(self) -> None:
        self.backgrounds = [Path(self.bg_list.item(i).data(Qt.UserRole))
                            for i in range(self.bg_list.count())]

    def _bg_changed(self) -> None:
        if self.bg_list.count():
            self._sync_from_list()
        if not self.backgrounds:
            self.bg_hint.setText("Chưa chọn nền: thêm video hoặc một ảnh.")
        elif self._is_image():
            self.bg_hint.setText("Ảnh được giữ nguyên suốt video.")
        else:
            self.bg_hint.setText(f"{len(self.backgrounds)} video — phát lần lượt rồi lặp lại tới khi "
                                 "âm thanh hết. Tiếng gốc của video bị bỏ.")
        self._refresh_buttons()
        self._still_timer.start()

    def _add_videos(self) -> None:
        if self._is_image():
            self.backgrounds = []                    # videos replace a picture
        fns, _ = QFileDialog.getOpenFileNames(self, "Chọn video nền", str(Path.home()), VIDEO_FILTER)
        if fns:
            self._set_backgrounds(self.backgrounds + [Path(f) for f in fns])

    def _pick_image(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Chọn ảnh", str(Path.home()), IMAGE_FILTER)
        if fn:
            self._set_backgrounds([Path(fn)])        # a picture replaces any clips

    def _remove(self) -> None:
        row = self.bg_list.currentRow()
        if row >= 0:
            self._sync_from_list()
            del self.backgrounds[row]
            self._set_backgrounds(self.backgrounds)

    # --- the watermark + the first frame ---------------------------------------------------
    def watermark(self) -> dict:
        return {"on": self.wm_on.isChecked(), "text": self.wm_text.text().strip(),
                "pos": self.wm_pos.currentData() or "tr", "opacity": self.wm_opacity.value(),
                "size": self.wm_size.value()}

    def _wm_changed(self) -> None:
        store.update_ui_state(sv_watermark=self.watermark())
        self._still_timer.start()

    def _cut_still(self) -> None:
        """The first frame as ``build`` will make it (background on its blur fill + the mark)."""
        import json

        from app.pipeline import scriptvideo

        if not self.backgrounds or not self.backgrounds[0].exists():
            self.frame_lbl.setPixmap(QPixmap())
            self.frame_lbl.setText("Chọn video hoặc ảnh để xem khung hình.")
            return
        if self._still_busy:
            self._still_timer.start()
            return
        bg, wm = self.backgrounds[0], self.watermark()
        key = json.dumps([str(bg), wm], sort_keys=True)
        if key == self._still_key:
            return
        if self._still_dir is None:
            from app.config import settings

            self._still_dir = Path(settings.vizsup_storage_dir) / "_scriptvideos" / "_preview"
        d = self._still_dir
        self._still_busy = True

        def done(path):
            self._still_busy = False
            self._still_key = key
            pix = QPixmap(str(path))
            if not pix.isNull():
                self.frame_lbl.setPixmap(pix.scaledToWidth(min(640, max(240, self.frame_lbl.width() - 12)),
                                                           Qt.SmoothTransformation))

        def failed(msg):
            self._still_busy = False
            self._still_key = key
            self.frame_lbl.setPixmap(QPixmap())
            self.frame_lbl.setText(f"Không xem trước được: {msg}")

        self._bg.run(lambda: str(scriptvideo.still(d, bg, wm)), done, failed)

    # --- building ---------------------------------------------------------------------------
    def _lock(self, busy: bool) -> None:
        for w in (self.script, self.open_btn, self.add_vid, self.add_img, self.rm, self.bg_list,
                  self.voice, self.speed, self.mode, self.audio_btn, self.vertical, self.wm_on,
                  self.wm_text, self.wm_pos, self.wm_opacity, self.wm_size):
            w.setEnabled(not busy)

    def _ready(self) -> bool:
        sound = (self.audio_file is not None and self.audio_file.exists()) if self._from_audio() \
            else bool(self.script.toPlainText().strip())
        return sound and bool(self.backgrounds)

    def _refresh_buttons(self) -> None:
        if hasattr(self, "go_btn") and hasattr(self, "backgrounds"):
            self.go_btn.setEnabled(self._busy or self._ready())

    def _go(self) -> None:
        from app.pipeline import scriptvideo

        if self._busy:
            self._cancel.set()
            self.status.setText("Đang hủy…")
            return
        try:
            scriptvideo.kind_of(self.backgrounds)
        except ValueError as exc:
            self.status.setText(f"✗ {exc}")
            return
        self._sync_from_list()
        from_audio = self._from_audio()
        script = None if from_audio else self.script.toPlainText()
        audio = self.audio_file if from_audio else None
        bgs, voice, rate, tts = list(self.backgrounds), self.voice.currentData(), self._rate(), self._tts_now()
        wm = self.watermark()
        wm = wm if wm["on"] and wm["text"] else None
        vert = self.vertical.isChecked()
        if not from_audio:
            store.update_ui_state(render_voice=voice, render_rate=self.speed.value() / 100)
        for b in (self.save_btn, self.folder_btn):
            b.setVisible(False)
        self.work_dir = scriptvideo.new_dir()
        wd, cancel = self.work_dir, self._cancel

        def work():
            return scriptvideo.build(wd, script, bgs, provider=tts, voice=voice, rate=rate,
                                     audio=audio, watermark=wm, also_vertical=vert,
                                     progress=self._prog, cancel=cancel.is_set)

        self._start(work, self._done)

    def _done(self, path) -> None:
        self.result_path = Path(path)
        self.status.setText(f"✓ Xong: {self.result_path}")
        self.save_btn.setVisible(True)
        self.folder_btn.setVisible(True)

    def _save_as(self) -> None:
        if not self.result_path:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Lưu video", str(Path.home() / "video-audio.mp4"),
                                            "MP4 (*.mp4)")
        if not fn:
            return
        try:
            shutil.copy2(self.result_path, fn)
            vert = self.result_path.with_name("output.vertical.mp4")
            if vert.exists():
                shutil.copy2(vert, Path(fn).with_name(Path(fn).stem + ".doc.mp4"))
            self.status.setText(f"✓ Đã lưu {fn}")
        except OSError as exc:
            self.status.setText(f"✗ Không lưu được: {exc}")
