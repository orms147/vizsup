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

    def mousePressEvent(self, e) -> None:
        p = self._norm(e.position())
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
        p.end()

    def value(self) -> dict | None:
        if self.rect is None:
            return None
        r = self.rect.normalized()
        return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


class AreaDialog(QDialog):
    """Returns (via :meth:`result_area`) the band, or None for "Để app tự dò"."""

    def __init__(self, frames: list[Path], suggestion: dict | None, series_name: str | None,
                 parent=None) -> None:
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

    def result_area(self) -> dict | None:
        from app.pipeline import ocr_area

        return ocr_area.clean(self.picker.value()) if self.result() == QDialog.Accepted else None
