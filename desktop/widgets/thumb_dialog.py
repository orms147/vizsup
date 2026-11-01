"""“Ảnh bìa” (render page → result pane): the 1280×720 cover YouTube gets (pipeline/thumbnail.py).

The current thumb.jpg on top, six of the video's best frames under it (found in the
background — one ffmpeg seek per sample, a few seconds), the title and the chip on the
picture, "Tạo lại" and "Chọn ảnh của bạn…". Every change is written to thumb.jpg at once;
anything the owner changes there is marked as theirs (thumb.json ``auto: false``) and a
later render keeps it — unless it is exactly the automatic choice again (best frame,
default words)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QToolButton

from desktop.theme import V2
from desktop.ui_thread import BgRunner
from desktop.widgets.chrome import button, label
from desktop.widgets.dialogs import VzDialog

PREVIEW = QSize(640, 360)
CHOICE = QSize(96, 54)


def _pixmap(frame) -> QPixmap:
    """A numpy H×W×3 uint8 frame as a QPixmap (copied: the array may go away)."""
    h, w = frame.shape[:2]
    data = frame.tobytes()                       # alive while QImage reads it
    img = QImage(data, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class ThumbDialog(VzDialog):
    def __init__(self, work_dir: Path, parent=None) -> None:
        from app.pipeline import thumbnail

        super().__init__("Ảnh bìa", parent, width=720)
        self.work_dir = Path(work_dir)
        self._bg = BgRunner(self)
        self.finished.connect(lambda _r: self._bg.shutdown())
        self._cands: list = []                     # thumbnail.Candidate, best first
        self._t: float | None = thumbnail.load_info(self.work_dir).get("time")
        self._busy = False
        self.body.addWidget(label("Ảnh 1280×720 gửi lên YouTube cùng video. Chọn khung hình, sửa "
                                  "chữ, hoặc dùng ảnh bạn tự làm — dựng lại video sẽ không ghi "
                                  "đè ảnh bạn đã chọn.", "secondary", wrap=True))
        self.preview = QLabel()
        self.preview.setFixedSize(PREVIEW)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(f"background:{V2['input']};border-radius:6px;")
        self.body.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.state = label("", "small")
        self.body.addWidget(self.state)
        self.body.addWidget(label("Khung hình đẹp nhất trong video", "h3"))
        self.choices = QHBoxLayout()
        self.choices.setSpacing(6)
        self.body.addLayout(self.choices)
        self.finding = label("Đang tìm khung hình đẹp…", "small")
        self.choices.addWidget(self.finding)
        self.choices.addStretch(1)
        self._buttons_by_t: dict[float, QToolButton] = {}
        info = thumbnail.load_info(self.work_dir)
        title, chip = thumbnail.default_text(self.work_dir)
        self._defaults = (title, chip)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("Chữ trên ảnh", "small"))
        self.title_edit = QLineEdit(info.get("title", title) if info.get("source") != "image"
                                    else title)
        self.title_edit.setPlaceholderText("Tên phim (để trống: chỉ có thẻ tập)")
        row.addWidget(self.title_edit, 1)
        row.addWidget(label("Thẻ", "small"))
        self.chip_edit = QLineEdit(info.get("chip", chip) if info.get("source") != "image"
                                   else chip)
        self.chip_edit.setPlaceholderText("TẬP 9")
        self.chip_edit.setMaximumWidth(130)
        row.addWidget(self.chip_edit)
        self.body.addLayout(row)
        self.status = label("", "small", wrap=True)
        self.body.addWidget(self.status)
        self.own_btn = button("Chọn ảnh của bạn…", "Upload", variant="ghost",
                              tip="Một ảnh bạn tự làm (JPG / PNG) — dùng nguyên, đưa về 1280×720")
        self.own_btn.clicked.connect(self._pick_own)
        self.redo_btn = button("Tạo lại", "RefreshCw", variant="primary",
                               tip="Vẽ lại ảnh bìa từ khung hình đang chọn với chữ ở trên")
        self.redo_btn.clicked.connect(self.redo)
        self._buttons.insertWidget(0, self.own_btn)
        self._buttons.addWidget(self.redo_btn)
        self.add_button("Đóng")
        self._show_current()
        self._find()

    # --- state ---------------------------------------------------------------------
    def _show_current(self) -> None:
        from app.pipeline import thumbnail

        p = self.work_dir / thumbnail.THUMB
        pix = QPixmap(str(p)) if thumbnail.cover(self.work_dir) else QPixmap()
        if pix.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Chưa có ảnh bìa — bấm Tạo lại")
            self.state.setText("")
            return
        self.preview.setPixmap(pix.scaled(PREVIEW, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        info = thumbnail.load_info(self.work_dir)
        self.state.setText("Ảnh của bạn — app không tự thay" if info.get("source") == "image"
                           else "Ảnh tự tạo — dựng lại video sẽ làm mới" if info.get("auto", True)
                           else "Ảnh bạn đã chọn — dựng lại video vẫn giữ")

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._busy = busy
        for w in (self.redo_btn, self.own_btn, *self._buttons_by_t.values()):
            w.setEnabled(not busy)
        if msg or not busy:
            self.status.setText(msg)

    # --- the candidates ------------------------------------------------------------
    def _find(self) -> None:
        from app.pipeline import thumbnail

        video, d = self.work_dir / "output.mp4", self.work_dir
        if not video.exists():
            self.finding.setText("Chưa có video dựng xong.")
            self.redo_btn.setEnabled(False)
            return
        self._bg.run(lambda: thumbnail.candidates(video, busy=thumbnail.busy_spans(d)),
                     self.set_candidates,
                     lambda m: self.finding.setText(f"Không lấy được khung hình: {m}"))

    def set_candidates(self, cands: list) -> None:
        """Show the frames (best first) as buttons; the one in use is checked."""
        self._cands = list(cands)
        self.finding.setVisible(not cands)
        if not cands:
            self.finding.setText("Không lấy được khung hình nào.")
        for c in cands:
            b = QToolButton()
            b.setCheckable(True)
            b.setIcon(QIcon(_pixmap(c.frame)))
            b.setIconSize(CHOICE)
            # the size in the sheet: the app's QToolButton min-height would win over setFixedSize
            w, h = CHOICE.width() + 8, CHOICE.height() + 8
            b.setStyleSheet(f"QToolButton{{padding:0;border:2px solid transparent;"
                            f"min-width:{w}px;max-width:{w}px;min-height:{h}px;max-height:{h}px;"
                            f"border-radius:6px;background:{V2['input']};}}"
                            f"QToolButton:checked{{border-color:{V2['purple_bright']};"
                            f"background:{V2['purple_bright']};}}"
                            f"QToolButton:hover{{border-color:{V2['purple_line']};}}")
            b.setToolTip(f"Giây {c.t:.1f}")
            b.clicked.connect(lambda _on=False, t=c.t: self.pick(t))
            self._buttons_by_t[c.t] = b
            self.choices.insertWidget(self.choices.count() - 1, b)
        if self._t is None and cands:
            self._t = cands[0].t
        self._sync_checked()

    def _sync_checked(self) -> None:
        near = min(self._buttons_by_t, key=lambda t: abs(t - self._t), default=None) \
            if self._t is not None else None
        for t, b in self._buttons_by_t.items():
            b.setChecked(near is not None and t == near and abs(t - self._t) < 0.05)

    # --- actions -------------------------------------------------------------------
    def pick(self, t: float) -> None:
        """A frame clicked: the cover is drawn again from it."""
        self._t = t
        self._sync_checked()
        self.redo()

    def _is_auto(self, t: float | None) -> bool:
        """Exactly what the automatic pass would make: the best frame, the default words."""
        best = self._cands[0].t if self._cands else None
        words = (self.title_edit.text().strip(), self.chip_edit.text().strip())
        return (t is None or (best is not None and abs(t - best) < 0.05)) and \
            words == tuple(s.strip() for s in self._defaults)

    def redo(self) -> None:
        from app.pipeline import thumbnail

        if self._busy:
            return
        t, title, chip = self._t, self.title_edit.text().strip(), self.chip_edit.text().strip()
        auto = self._is_auto(t)
        d = self.work_dir
        self._set_busy(True, "Đang vẽ ảnh bìa…")
        self._bg.run(lambda: thumbnail.make(d, t=t, title=title, chip=chip, auto=auto),
                     self._done, self._failed)

    def _pick_own(self) -> None:
        from app.pipeline import thumbnail

        fn, _ = QFileDialog.getOpenFileName(self, "Chọn ảnh bìa", str(Path.home()),
                                            "Ảnh (*.jpg *.jpeg *.png *.webp *.bmp)")
        if not fn:
            return
        d = self.work_dir
        self._set_busy(True, "Đang lưu ảnh…")
        self._bg.run(lambda: thumbnail.use_image(d, Path(fn)), self._done, self._failed)

    def _done(self, _path) -> None:
        from app.pipeline import thumbnail

        self._set_busy(False, "✓ Đã lưu thumb.jpg")
        self._t = thumbnail.load_info(self.work_dir).get("time", self._t)
        self._sync_checked()
        self._show_current()

    def _failed(self, msg: str) -> None:
        self._set_busy(False, f"✗ {msg}")
