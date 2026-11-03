"""“Khoanh vùng chữ Trung”: after the download, the owner draws the band OCR reads on a frame
of the video (pipeline/ocr_area.py). The app's own detection is drawn first; drag on the
picture to draw a new rectangle, drag inside it to move it. A few frames can be flipped
through (a subtitle may not show on every one)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from desktop.widgets.chrome import button, label


class AreaPicker(QWidget):
    """A frame fitted into the widget + one normalized rectangle the mouse draws / moves."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self.rect: QRectF | None = None          # normalized 0..1 of the frame
        self.logos: list[QRectF] = []            # regions OCR must skip (creator logo…)
        self.mode = "band"                       # "band" = the subtitle band · "logo" = logo regions
        self.logo_sel = -1
        self._drag: tuple[str, QPointF, QRectF | None] | None = None
        self.setMinimumSize(560, 315)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.CrossCursor)

    def set_frame(self, path: Path) -> None:
        pix = QPixmap(str(path))
        self._pix = pix if not pix.isNull() else None
        self.update()

    def _view(self) -> QRectF:
        if not self._pix:
            return QRectF(self.rect_widget())
        pw, ph = self._pix.width(), self._pix.height()
        s = min(self.width() / pw, self.height() / ph)
        w, h = pw * s, ph * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def rect_widget(self):
        return self.contentsRect()

    def _norm(self, p: QPointF) -> QPointF:
        v = self._view()
        return QPointF(min(1.0, max(0.0, (p.x() - v.x()) / v.width())),
                       min(1.0, max(0.0, (p.y() - v.y()) / v.height())))

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def remove_logo(self) -> None:
        if 0 <= self.logo_sel < len(self.logos):
            del self.logos[self.logo_sel]
        elif self.logos:
            self.logos.pop()
        self.logo_sel = -1
        self.update()

    def mousePressEvent(self, e) -> None:
        p = self._norm(e.position())
        if self.mode == "logo":
            hit = next((i for i in range(len(self.logos) - 1, -1, -1) if self.logos[i].contains(p)), -1)
            if hit >= 0:
                self.logo_sel = hit
                self._drag = ("move", p, QRectF(self.logos[hit]))
            else:
                self.logos.append(QRectF(p, p))
                self.logo_sel = len(self.logos) - 1
                self._drag = ("draw", p, None)
            self.update()
            return
        if self.rect is not None and self.rect.contains(p):
            self._drag = ("move", p, QRectF(self.rect))
        else:
            self._drag = ("draw", p, None)
            self.rect = QRectF(p, p)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if not self._drag:
            return
        kind, start, orig = self._drag
        p = self._norm(e.position())
        if self.mode == "logo" and 0 <= self.logo_sel < len(self.logos):
            if kind == "draw":
                self.logos[self.logo_sel] = QRectF(start, p).normalized()
            else:
                r = QRectF(orig).translated(p.x() - start.x(), p.y() - start.y())
                r.moveLeft(min(max(0.0, r.left()), 1.0 - r.width()))
                r.moveTop(min(max(0.0, r.top()), 1.0 - r.height()))
                self.logos[self.logo_sel] = r
            self.update()
            return
        if kind == "draw":
            self.rect = QRectF(start, p).normalized()
        else:
            dx, dy = p.x() - start.x(), p.y() - start.y()
            r = QRectF(orig).translated(dx, dy)
            r.moveLeft(min(max(0.0, r.left()), 1.0 - r.width()))
            r.moveTop(min(max(0.0, r.top()), 1.0 - r.height()))
            self.rect = r
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
        if self.mode == "logo" and 0 <= self.logo_sel < len(self.logos):
            r = self.logos[self.logo_sel]
            if r.width() < 0.01 or r.height() < 0.01:         # a click, not a rectangle
                del self.logos[self.logo_sel]
                self.logo_sel = -1
                self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect_widget(), QColor("#0b0b10"))
        v = self._view()
        if self._pix:
            p.drawPixmap(v, self._pix, QRectF(self._pix.rect()))
        if self.rect is not None and self.rect.width() > 0:
            r = QRectF(v.x() + self.rect.x() * v.width(), v.y() + self.rect.y() * v.height(),
                       self.rect.width() * v.width(), self.rect.height() * v.height())
            shade = QColor(0, 0, 0, 120)             # dim everything OCR will not read
            p.fillRect(QRectF(v.x(), v.y(), v.width(), r.top() - v.y()), shade)
            p.fillRect(QRectF(v.x(), r.bottom(), v.width(), v.bottom() - r.bottom()), shade)
            p.fillRect(QRectF(v.x(), r.top(), r.left() - v.x(), r.height()), shade)
            p.fillRect(QRectF(r.right(), r.top(), v.right() - r.right(), r.height()), shade)
            pen = QPen(QColor("#bb99ff"), 2, Qt.DashLine)
            p.setPen(pen)
            p.drawRect(r)
        for i, lg in enumerate(self.logos):                    # logo regions: red
            r = QRectF(v.x() + lg.x() * v.width(), v.y() + lg.y() * v.height(),
                       lg.width() * v.width(), lg.height() * v.height())
            p.fillRect(r, QColor(255, 60, 60, 90))
            p.setPen(QPen(QColor("#ff5c5c"), 3 if i == self.logo_sel else 2))
            p.drawRect(r)
        p.end()

    def logo_values(self) -> list[dict]:
        out = []
        for lg in self.logos:
            r = lg.normalized()
            out.append({"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()})
        return out

    def value(self) -> dict | None:
        if self.rect is None:
            return None
        r = self.rect.normalized()
        return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


class AreaDialog(QDialog):
    """Returns (via :meth:`result_area`) the band, or None for "Để app tự dò"."""

    def __init__(self, frames: list[Path], suggestion: dict | None, series_name: str | None,
                 parent=None, logos: list[dict] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Khoanh vùng chữ Trung")
        self.frames = list(frames)
        self.i = 0
        lay = QVBoxLayout(self)
        lay.addWidget(label("Khoanh vùng phụ đề tiếng Trung", "h2"))
        lay.addWidget(label("Kéo chuột quanh dải phụ đề tiếng Trung — chỉ phụ đề, tránh logo, tên "
                            "tác giả, chữ trong cảnh. App chỉ đọc chữ trong khung này, và khi dựng "
                            "sẽ che đúng dải này. Kéo bên trong khung để dời.", "secondary",
                            wrap=True))
        self.picker = AreaPicker()
        if suggestion:
            self.picker.rect = QRectF(suggestion["x"], suggestion["y"], suggestion["w"],
                                      suggestion["h"])
        for lg in logos or []:
            self.picker.logos.append(QRectF(lg["x"], lg["y"], lg["w"], lg["h"]))
        lay.addWidget(self.picker, 1)
        tools = QHBoxLayout()
        self.band_btn = button("Khung phụ đề", "Captions", variant="ghost", small=True, checkable=True)
        self.logo_btn = button("Vùng logo (nhiều vùng)", "EyeOff", variant="ghost", small=True,
                               checkable=True,
                               tip="Đánh dấu logo / tên kênh nằm cố định trên video — OCR bỏ qua các vùng này")
        self.band_btn.setChecked(True)
        self.band_btn.clicked.connect(lambda: self._set_mode("band"))
        self.logo_btn.clicked.connect(lambda: self._set_mode("logo"))
        self.del_logo = button("Xóa vùng logo", "Trash2", variant="ghost", small=True)
        self.del_logo.clicked.connect(self.picker.remove_logo)
        tools.addWidget(self.band_btn)
        tools.addWidget(self.logo_btn)
        tools.addWidget(self.del_logo)
        tools.addStretch(1)
        lay.addLayout(tools)
        self.hint = label("", "small", wrap=True)
        lay.addWidget(self.hint)
        nav = QHBoxLayout()
        self.prev_btn = button("Khung trước", "ArrowLeft", variant="ghost", small=True)
        self.next_btn = button("Khung sau", "ArrowRight", variant="ghost", small=True)
        self.pos_lbl = QLabel()
        self.prev_btn.clicked.connect(lambda: self._show(self.i - 1))
        self.next_btn.clicked.connect(lambda: self._show(self.i + 1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.pos_lbl)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        lay.addLayout(nav)
        self.remember = QCheckBox(f"Nhớ cho bộ phim 《{series_name}》 — các tập sau dùng luôn, "
                                  f"không hỏi lại" if series_name else "Nhớ cho dự án này")
        self.remember.setChecked(True)
        self.remember.setVisible(bool(series_name))
        lay.addWidget(self.remember)
        row = QHBoxLayout()
        row.addStretch(1)
        auto = button("Để app tự dò", variant="ghost")
        use = button("Dùng vùng này", "Check", variant="primary")
        auto.clicked.connect(self.reject)
        use.clicked.connect(self._accept)
        row.addWidget(auto)
        row.addWidget(use)
        lay.addLayout(row)
        self.resize(900, 640)
        self._show(0)

    def _set_mode(self, mode: str) -> None:
        self.picker.set_mode(mode)
        self.band_btn.setChecked(mode == "band")
        self.logo_btn.setChecked(mode == "logo")
        self.hint.setText(
            "Đang chọn VÙNG LOGO: kéo chuột quanh logo / tên kênh để đánh dấu (vẽ được nhiều vùng, "
            "kéo bên trong để dời). App không đọc chữ trong các vùng đỏ." if mode == "logo" else "")

    def _show(self, i: int) -> None:
        if not self.frames:
            self.pos_lbl.setText("Không lấy được khung hình")
            return
        self.i = max(0, min(len(self.frames) - 1, i))
        self.picker.set_frame(self.frames[self.i])
        self.pos_lbl.setText(f"Khung {self.i + 1}/{len(self.frames)}")
        self.prev_btn.setEnabled(self.i > 0)
        self.next_btn.setEnabled(self.i < len(self.frames) - 1)

    def _accept(self) -> None:
        from app.pipeline import ocr_area

        if ocr_area.clean(self.picker.value()) is None:
            self.pos_lbl.setText("Khung quá nhỏ — kéo chuột quanh cả dải phụ đề")
            return
        self.accept()

    def result_logos(self) -> list[dict]:
        """The logo regions (cleaned) when the dialog was accepted; [] otherwise."""
        from app.pipeline import ocr_area

        return ocr_area.clean_logos(self.picker.logo_values()) if self.result() == QDialog.Accepted else []

    def result_area(self) -> dict | None:
        from app.pipeline import ocr_area

        return ocr_area.clean(self.picker.value()) if self.result() == QDialog.Accepted else None


class BoxDialog(QDialog):
    """"Che logo, ghi tên phim": the owner draws ONE rectangle on a frame (the creator's logo, a
    Chinese title); the render covers it with their own text. :meth:`result_box` → the rectangle
    (normalized to the frame) or None."""

    def __init__(self, frames: list[Path], box: dict | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chọn vùng che logo")
        self.frames = list(frames)
        self.i = 0
        lay = QVBoxLayout(self)
        lay.addWidget(label("Chọn vùng cần che", "h2"))
        lay.addWidget(label("Kéo chuột quanh logo / tên kênh / tiêu đề tiếng Trung cần che — vùng này "
                            "sẽ được phủ một ô màu có chữ của bạn. Kéo bên trong khung để dời.",
                            "secondary", wrap=True))
        self.picker = AreaPicker()
        if box:
            self.picker.rect = QRectF(box["x"], box["y"], box["w"], box["h"])
        lay.addWidget(self.picker, 1)
        nav = QHBoxLayout()
        self.prev_btn = button("Khung trước", "ArrowLeft", variant="ghost", small=True)
        self.next_btn = button("Khung sau", "ArrowRight", variant="ghost", small=True)
        self.pos_lbl = QLabel()
        self.prev_btn.clicked.connect(lambda: self._show(self.i - 1))
        self.next_btn.clicked.connect(lambda: self._show(self.i + 1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.pos_lbl)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        lay.addLayout(nav)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Hủy", variant="ghost")
        use = button("Dùng vùng này", "Check", variant="primary")
        cancel.clicked.connect(self.reject)
        use.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(use)
        lay.addLayout(row)
        self.resize(900, 620)
        self._show(0)

    def _show(self, i: int) -> None:
        if not self.frames:
            self.pos_lbl.setText("Không lấy được khung hình")
            return
        self.i = max(0, min(len(self.frames) - 1, i))
        self.picker.set_frame(self.frames[self.i])
        self.pos_lbl.setText(f"Khung {self.i + 1}/{len(self.frames)}")
        self.prev_btn.setEnabled(self.i > 0)
        self.next_btn.setEnabled(self.i < len(self.frames) - 1)

    def _accept(self) -> None:
        from app.pipeline import ocr_area

        if ocr_area.clean(self.picker.value()) is None:
            self.pos_lbl.setText("Vùng quá nhỏ — kéo chuột quanh cả logo")
            return
        self.accept()

    def result_box(self) -> dict | None:
        from app.pipeline import ocr_area

        return ocr_area.clean(self.picker.value()) if self.result() == QDialog.Accepted else None
