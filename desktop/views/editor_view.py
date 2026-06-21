"""Screen 2 — Subtitle Editor (the human gate). Video + table + timeline, kept
in sync. Autosaves edits to job.vi_srt. Split/Merge/Add/Delete tools.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import cuemodel
from desktop.cuemodel import EditorCue
from desktop.theme import C
from desktop.widgets.style_panel import StylePanel
from desktop.widgets.subtitle_table import SubtitleTable
from desktop.widgets.timeline import Timeline
from desktop.widgets.video_panel import VideoPanel

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    _HAVE_MM = True
except Exception:  # noqa: BLE001
    _HAVE_MM = False


class _Task(QObject):
    """Run one blocking backend call off the UI thread."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class EditorView(QWidget):
    dirtyChanged = Signal(bool)
    previewStyleRequested = Signal()   # "Xem thử kiểu" — main burns a WYSIWYG frame

    def __init__(self) -> None:
        super().__init__()
        self.job = None
        self.cues: list[EditorCue] = []
        self._ctx: dict = {}          # tts/voice/translator context from step 1
        self._sel: int = -1           # selected cue index
        self._tasks: list = []        # keep async (thread, worker) refs alive
        self._dub_player = None
        if _HAVE_MM:
            self._dub_player = QMediaPlayer()
            self._dub_audio = QAudioOutput()
            self._dub_player.setAudioOutput(self._dub_audio)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        split = QSplitter(Qt.Vertical)
        # --- top: video | table ---
        top = QSplitter(Qt.Horizontal)
        self.video = VideoPanel()
        vwrap = QWidget()
        vlay = QVBoxLayout(vwrap)
        vlay.setContentsMargins(14, 14, 14, 14)
        cap = QLabel("VIDEO NGUỒN")
        cap.setObjectName("section")
        vlay.addWidget(cap)
        vlay.addWidget(self.video, 1)
        vwrap.setMinimumWidth(280)

        # right = tabbed: "Nội dung & Lồng tiếng" | "Kiểu chữ"
        content = QWidget()
        rlay = QVBoxLayout(content)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        rlay.addLayout(self._toolbar())
        rlay.addWidget(self._cue_panel())
        self.table = SubtitleTable()
        rlay.addWidget(self.table, 1)

        self.style_panel = StylePanel()
        self.style_panel.previewRequested.connect(self.previewStyleRequested)
        self.style_panel.styleChanged.connect(self._on_style_changed)

        self.tabs = QTabWidget()
        self.tabs.addTab(content, "Nội dung & Lồng tiếng")
        self.tabs.addTab(self.style_panel, "Kiểu chữ")

        top.addWidget(vwrap)
        top.addWidget(self.tabs)
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setSizes([332, 900])

        split.addWidget(top)
        split.addWidget(self._dock())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([560, 190])
        root.addWidget(split)

        # wiring
        self.table.rowSelected.connect(lambda i: self._select(i, seek=True))
        self.table.seekRequested.connect(self.video.seek_seconds)
        self.table.edited.connect(self._mark_dirty)
        self.timeline.seeked.connect(self.video.seek_seconds)
        self.timeline.blockSelected.connect(lambda i: self.table.selectRow(i))
        self.timeline.cueChanged.connect(self._on_cue_dragged)
        self.video.positionChanged.connect(self.timeline.set_time)

        # undo (Ctrl+Z): snapshot before table cell edits and before each tool action
        self._undo_stack: list[list[EditorCue]] = []
        self.table.beforeEdit.connect(self._snapshot)
        QShortcut(QKeySequence.Undo, self, activated=self._undo_action)

    def _toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 9, 16, 9)
        title = QLabel("Phụ đề")
        title.setStyleSheet(f"font-weight:600;color:{C['text2']};")
        self.count = QLabel("0 dòng")
        self.count.setObjectName("mono")
        bar.addWidget(title)
        bar.addWidget(self.count)
        bar.addStretch(1)
        for label, slot in [("Tách", self._split), ("Gộp", self._merge),
                            ("Thêm", self._add), ("Xóa", self._delete)]:
            b = QPushButton(label)
            b.setObjectName("ghost")
            b.clicked.connect(slot)
            bar.addWidget(b)
        return bar

    def _dock(self) -> QWidget:
        dock = QWidget()
        dock.setObjectName("dock")
        lay = QVBoxLayout(dock)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 7, 16, 7)
        lb = QLabel("DÒNG THỜI GIAN")
        lb.setObjectName("section")
        head.addWidget(lb)
        head.addStretch(1)
        zo = QPushButton("－")
        zi = QPushButton("＋")
        for b in (zo, zi):
            b.setObjectName("ghost")
            b.setFixedSize(26, 26)
        head.addWidget(zo)
        head.addWidget(zi)
        lay.addLayout(head)
        self.timeline = Timeline()
        zo.clicked.connect(self.timeline.zoom_out)
        zi.clicked.connect(self.timeline.zoom_in)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.timeline)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)
        return dock

    # --- per-cue dub panel ---------------------------------------------------
    def _cue_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("dock")
        h = QHBoxLayout(panel)
        h.setContentsMargins(16, 7, 16, 7)
        h.setSpacing(9)
        self.sel_lbl = QLabel("Câu —")
        self.sel_lbl.setStyleSheet(f"color:{C['text2']};font-weight:600;")
        self.fit_lbl = QLabel("")
        self.fit_lbl.setObjectName("mono")
        h.addWidget(self.sel_lbl)
        h.addWidget(self.fit_lbl)
        h.addSpacing(8)
        h.addWidget(QLabel("Âm lượng"))
        self.gain = QSlider(Qt.Horizontal)
        self.gain.setFixedWidth(110)
        self.gain.setRange(-12, 12)
        self.gain.valueChanged.connect(self._on_gain)
        self.gain_lbl = QLabel("0 dB")
        self.gain_lbl.setObjectName("mono")
        h.addWidget(self.gain)
        h.addWidget(self.gain_lbl)
        self.mute_cb = QCheckBox("Tắt tiếng")
        self.mute_cb.toggled.connect(self._on_mute)
        h.addWidget(self.mute_cb)
        h.addStretch(1)
        self.btn_listen = QPushButton("🔊 Nghe thử")
        self.btn_listen.setObjectName("ghost")
        self.btn_listen.clicked.connect(self._preview_dub)
        self.btn_shorten = QPushButton("✂ Rút gọn")
        self.btn_shorten.setObjectName("ghost")
        self.btn_shorten.clicked.connect(self._shorten)
        h.addWidget(self.btn_listen)
        h.addWidget(self.btn_shorten)
        self._set_panel_enabled(False)
        return panel

    def set_context(self, opts: dict) -> None:
        """tts/voice/rate/translator chosen in step 1 — used by preview & shorten."""
        self._ctx = opts or {}

    def _set_panel_enabled(self, on: bool) -> None:
        for w in (self.gain, self.mute_cb, self.btn_listen, self.btn_shorten):
            w.setEnabled(on)

    def _update_cue_panel(self, idx: int) -> None:
        self._sel = idx
        if not (0 <= idx < len(self.cues)):
            self.sel_lbl.setText("Câu —")
            self.fit_lbl.setText("")
            self._set_panel_enabled(False)
            return
        c = self.cues[idx]
        self._set_panel_enabled(True)
        self.sel_lbl.setText(f"Câu #{idx + 1}")
        self.fit_lbl.setText("⚠ quá dài" if c.too_long else "✓ vừa")
        self.fit_lbl.setStyleSheet(f"color:{C['amber'] if c.too_long else C['muted']};")
        self.gain.blockSignals(True)
        self.mute_cb.blockSignals(True)
        self.gain.setValue(int(round(c.gain_db)))
        self.gain_lbl.setText(f"{int(round(c.gain_db)):+d} dB")
        self.mute_cb.setChecked(c.mute)
        self.gain.blockSignals(False)
        self.mute_cb.blockSignals(False)

    def _on_gain(self, v: int) -> None:
        if 0 <= self._sel < len(self.cues):
            self.cues[self._sel].gain_db = float(v)
            self.gain_lbl.setText(f"{v:+d} dB")
            self._mark_dirty()

    def _on_mute(self, on: bool) -> None:
        if 0 <= self._sel < len(self.cues):
            self.cues[self._sel].mute = on
            self.table.refresh_row(self._sel)
            self._mark_dirty()

    def _toast(self, msg: str) -> None:
        self.fit_lbl.setStyleSheet(f"color:{C['amber']};")
        self.fit_lbl.setText(msg)

    def _preview_dub(self) -> None:
        """Synthesize the selected line's VI (off-thread) and play it."""
        if not (0 <= self._sel < len(self.cues)) or self._dub_player is None or self.job is None:
            return
        text = self.cues[self._sel].vi.strip()
        if not text:
            return
        gain_db = self.cues[self._sel].gain_db
        out = self.job.work_dir / "_dub_preview.mp3"
        tts_name = self._ctx.get("tts", "edge")
        voice, rate = self._ctx.get("voice"), self._ctx.get("rate")
        self.btn_listen.setEnabled(False)
        self.btn_listen.setText("⏳ …")

        def work():
            from app.providers.registry import get_tts
            get_tts(tts_name).synthesize(text, out, voice=voice, rate=rate)
            return str(out)

        def done(path):
            from PySide6.QtCore import QUrl
            self._dub_audio.setVolume(min(1.0, 10 ** (gain_db / 20.0)))
            self._dub_player.setSource(QUrl.fromLocalFile(str(path)))
            self._dub_player.play()
            self.btn_listen.setEnabled(True)
            self.btn_listen.setText("🔊 Nghe thử")

        def fail(msg):
            self.btn_listen.setEnabled(True)
            self.btn_listen.setText("🔊 Nghe thử")
            self._toast(f"Lỗi nghe thử: {msg}")

        self._run_async(work, done, fail)

    def _shorten(self) -> None:
        """Ask the LLM to shorten the selected VI line so it fits its dub slot."""
        if not (0 <= self._sel < len(self.cues)):
            return
        idx = self._sel
        text = self.cues[idx].vi.strip()
        if not text:
            return
        provider = self._ctx.get("translator") or "openrouter"
        self.btn_shorten.setEnabled(False)
        self.btn_shorten.setText("⏳ …")

        def work():
            from app.pipeline.translate import shorten_line
            return shorten_line(text, provider=provider)

        def done(result):
            self.btn_shorten.setEnabled(True)
            self.btn_shorten.setText("✂ Rút gọn")
            if result and result != text and 0 <= idx < len(self.cues):
                self._snapshot()
                self.cues[idx].vi = result
                self.table.refresh_row(idx)
                self.video.set_cues(self.cues)
                self._update_cue_panel(idx)
                self._mark_dirty()

        def fail(msg):
            self.btn_shorten.setEnabled(True)
            self.btn_shorten.setText("✂ Rút gọn")
            self._toast(f"Lỗi rút gọn: {msg}")

        self._run_async(work, done, fail)

    def shutdown(self) -> None:
        """Join any short-lived task threads so closing the window never tears down
        a running QThread (hard crash)."""
        for thread, _task in list(self._tasks):
            try:
                thread.quit()
                thread.wait(2000)
            except RuntimeError:
                pass

    def _run_async(self, fn, on_done, on_fail=None) -> None:
        thread = QThread()
        task = _Task(fn)
        pair = (thread, task)
        self._tasks.append(pair)
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.done.connect(on_done)
        if on_fail:
            task.failed.connect(on_fail)
        task.done.connect(thread.quit)
        task.failed.connect(thread.quit)
        thread.finished.connect(task.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._tasks.remove(pair) if pair in self._tasks else None)
        thread.start()

    # --- load / save ---------------------------------------------------------
    def load(self, job) -> None:
        self.job = job
        self.cues = cuemodel.load_pair(job)
        self.video.load(job.source_video)
        self.video.set_cues(self.cues)
        self.table.load(self.cues)
        self.timeline.set_cues(self.cues)
        self.count.setText(f"{len(self.cues)} dòng")
        self._undo_stack.clear()
        self._update_cue_panel(-1)
        st = self._load_style(job)
        if st:
            self.style_panel.set_from(st)

    def _reload(self) -> None:
        self.table.load(self.cues)
        self.timeline.set_cues(self.cues)
        self.video.set_cues(self.cues)
        self.count.setText(f"{len(self.cues)} dòng")
        self._mark_dirty()

    # --- undo ----------------------------------------------------------------
    def _snapshot(self) -> None:
        self._undo_stack.append(
            [EditorCue(c.start, c.end, c.vi, c.zh, c.gain_db, c.mute) for c in self.cues])
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def _undo_action(self) -> None:
        if not self._undo_stack:
            return
        self.cues = self._undo_stack.pop()
        self._reload()

    def _save(self) -> None:
        if self.job is not None:
            cuemodel.save_vi(self.job, self.cues)
            self._save_style()
            self.dirtyChanged.emit(False)

    # --- style (delegates to the Kiểu chữ tab) -------------------------------
    def _on_style_changed(self) -> None:
        self._mark_dirty()

    def get_style(self) -> dict:
        return self.style_panel.get_style()

    def get_font(self) -> str:
        return self.style_panel.get_font()

    def get_size_px(self) -> int:
        return self.style_panel.get_size_px()

    def _load_style(self, job):
        import json
        p = job.work_dir / "style.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
        return None

    def _save_style(self) -> None:
        import json
        data = {"font": self.style_panel.get_font(), "size": self.style_panel.get_size_px(),
                "style": self.style_panel.get_style()}
        (self.job.work_dir / "style.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_now(self) -> None:
        self._save_timer.stop()
        self._save()

    def _mark_dirty(self) -> None:
        self.dirtyChanged.emit(True)
        self._save_timer.start()

    # --- sync ----------------------------------------------------------------
    def _select(self, idx: int, *, seek: bool) -> None:
        self.timeline.set_selected(idx)
        self._update_cue_panel(idx)
        if seek and 0 <= idx < len(self.cues):
            self.video.seek_seconds(self.cues[idx].start)

    def _on_cue_dragged(self, idx: int) -> None:
        self.table.refresh_row(idx)
        self._mark_dirty()

    # --- tools ---------------------------------------------------------------
    def _cur(self) -> int:
        return self.table.currentRow()

    def _add(self) -> None:
        self._snapshot()
        i = self._cur()
        last = self.cues[-1] if self.cues else None
        start = (self.cues[i].end if 0 <= i < len(self.cues) else (last.end if last else 0.0))
        self.cues.insert(i + 1 if i >= 0 else len(self.cues),
                         EditorCue(start=start, end=start + 2.0, vi="", zh=""))
        self._reload()

    def _delete(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues):
            self._snapshot()
            self.cues.pop(i)
            self._reload()

    def _split(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues):
            self._snapshot()
            c = self.cues[i]
            orig_end = c.end                 # capture BEFORE mutating, so the second half is exact
            mid = (c.start + c.end) / 2
            words = c.vi.split()
            half = len(words) // 2
            left, right = " ".join(words[:half]), " ".join(words[half:])
            c.end, c.vi = mid, left
            self.cues.insert(i + 1, EditorCue(start=mid, end=orig_end, vi=right, zh=""))
            self._reload()

    def _merge(self) -> None:
        i = self._cur()
        if 0 <= i < len(self.cues) - 1:
            self._snapshot()
            a, b = self.cues[i], self.cues[i + 1]
            a.end = b.end
            a.vi = (a.vi + " " + b.vi).strip()
            a.zh = (a.zh + b.zh).strip()
            a.mute = a.mute or b.mute                                  # don't silently un-mute
            a.gain_db = a.gain_db if abs(a.gain_db) >= abs(b.gain_db) else b.gain_db
            self.cues.pop(i + 1)
            self._reload()
