"""Edit visual-only translations at the playhead."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QListWidget, QPlainTextEdit, QVBoxLayout,
)

from app.pipeline import ocr_area, region_captions
from desktop.cuemodel import fmt_time
from desktop.widgets.area_dialog import AreaPicker
from desktop.widgets.chrome import button, label


class RegionDialog(QDialog):
    def __init__(self, editor, job, captions, at: float, duration: float) -> None:
        super().__init__(editor)
        self.captions = list(captions)
        self.editor, self.job = editor, job
        self.at, self.duration = at, duration
        self.setWindowTitle("Dịch vùng chữ")
        self.resize(630, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"Thêm chú thích từ khung hình tại {fmt_time(at, 2)}.", wrap=True))
        self.rows = QListWidget()
        self.rows.itemDoubleClicked.connect(lambda _item: self._edit(self.rows.currentRow()))
        layout.addWidget(self.rows)
        row = QHBoxLayout()
        add = button("Thêm từ khung hiện tại", "Plus")
        edit = button("Sửa", "Type")
        remove = button("Xóa", "Trash2")
        add.clicked.connect(lambda: self._edit(-1))
        edit.clicked.connect(self._edit_selected)
        remove.clicked.connect(self._remove)
        for control in (add, edit, remove):
            row.addWidget(control)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self, index: int = 0) -> None:
        self.rows.clear()
        for c in self.captions:
            self.rows.addItem(f"{fmt_time(c.start, 2)} – {fmt_time(c.end, 2)}  ·  {' '.join(c.text.split())}")
        if self.captions:
            self.rows.setCurrentRow(min(index, len(self.captions) - 1))

    def _remove(self) -> None:
        index = self.rows.currentRow()
        if index >= 0:
            self.captions.pop(index)
            self._refresh(index)

    def _edit_selected(self) -> None:
        index = self.rows.currentRow()
        if index >= 0:
            self._edit(index)

    def _edit(self, index: int) -> None:
        existing = self.captions[index] if index >= 0 else None
        entry = _RegionEntry(self, existing)
        if not entry.exec():
            return
        if index < 0:
            index = len(self.captions)
            self.captions.append(entry.caption)
        else:
            self.captions[index] = entry.caption
        self._refresh(index)


class _RegionEntry(QDialog):
    def __init__(self, manager: RegionDialog, existing) -> None:
        super().__init__(manager)
        self.manager = manager
        self.sample = existing.sample if existing else manager.at
        self.caption = existing
        self.setWindowTitle("Chú thích vùng chữ")
        self.resize(720, 680)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"Khung {fmt_time(self.sample, 2)}. Kéo quanh chữ cần dịch. "
                               "AI nhận ảnh đã cắt qua OpenRouter.", wrap=True))
        self.picker = AreaPicker()
        if existing:
            self.picker.rect = QRectF(*(existing.area[key] for key in ("x", "y", "w", "h")))
        layout.addWidget(self.picker, 1)
        controls = QHBoxLayout()
        self.translate_button = button("Dịch vùng bằng AI", "Sparkles")
        self.translate_button.clicked.connect(self._translate)
        controls.addWidget(self.translate_button)
        center = button("Đặt chữ vào giữa vùng", "AlignCenter")
        center.clicked.connect(self._center)
        controls.addWidget(center)
        place = button("Chọn vị trí trên ảnh", "Focus")
        place.setCheckable(True)
        place.toggled.connect(lambda on: self.picker.set_mode("point" if on else "band"))
        self.picker.pointPicked.connect(self._place)
        self.picker.pointPicked.connect(lambda *_: place.setChecked(False))
        controls.addWidget(place)
        controls.addStretch()
        layout.addLayout(controls)
        form = QFormLayout()
        limit = manager.duration or 86400
        self.start = self._spin(limit, existing.start if existing else self.sample, " s")
        self.end = self._spin(limit, existing.end if existing else self.sample + 3, " s")
        span = QHBoxLayout()
        span.addWidget(self.start)
        span.addWidget(QLabel("đến"))
        span.addWidget(self.end)
        form.addRow("Hiện từ", span)
        self.x = self._spin(100, existing.x * 100 if existing else 50, " %")
        self.y = self._spin(100, existing.y * 100 if existing else 50, " %")
        self.x.valueChanged.connect(self._show_target)
        self.y.valueChanged.connect(self._show_target)
        self._show_target()
        position = QHBoxLayout()
        position.addWidget(QLabel("Ngang"))
        position.addWidget(self.x)
        position.addWidget(QLabel("Dọc"))
        position.addWidget(self.y)
        form.addRow("Vị trí tâm chữ", position)
        self.source = QPlainTextEdit(existing.source if existing else "")
        self.text = QPlainTextEdit(existing.text if existing else "")
        for field in (self.source, self.text):
            field.setFixedHeight(54)
        form.addRow("Chữ gốc", self.source)
        form.addRow("Bản dịch", self.text)
        layout.addLayout(form)
        self.status = label("Đang lấy khung hình…", wrap=True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._ready = False
        self.translate_button.setEnabled(False)
        manager.editor._run_async(lambda: region_captions.frame(manager.job, self.sample),
                                  self._show_frame, self._error)

    @staticmethod
    def _spin(limit: float, value: float, suffix: str) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setRange(0, limit)
        field.setSuffix(suffix)
        field.setValue(value)
        return field

    def _show_frame(self, data: bytes) -> None:
        if not self.isVisible():
            return
        pix = QPixmap()
        if not pix.loadFromData(data):
            self._error("Không mở được khung hình.")
            return
        self.picker.set_pixmap(pix)
        self._ready = True
        self.translate_button.setEnabled(True)
        self.status.setText("")

    def _error(self, message: str) -> None:
        if self.isVisible():
            self._set_busy(False)
            self.status.setText(message)

    def _set_busy(self, busy: bool) -> None:
        self.picker.setEnabled(not busy)
        self.source.setReadOnly(busy)
        self.text.setReadOnly(busy)
        self.translate_button.setEnabled(self._ready and not busy)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(not busy)

    def _center(self) -> None:
        area = ocr_area.clean(self.picker.value())
        if area:
            self._place(area["x"] + area["w"] / 2, area["y"] + area["h"] / 2)

    def _place(self, x: float, y: float) -> None:
        self.x.setValue(x * 100)
        self.y.setValue(y * 100)

    def _show_target(self) -> None:
        self.picker.target = QPointF(self.x.value() / 100, self.y.value() / 100)
        self.picker.update()

    def _translate(self) -> None:
        area = ocr_area.clean(self.picker.value())
        if area is None:
            self.status.setText("Kéo khung quanh chữ cần dịch.")
            return
        self._set_busy(True)
        self.status.setText("Đang đọc và dịch chữ…")
        self.manager.editor._run_async(
            lambda: region_captions.translate(self.manager.job, self.sample, area),
            self._translated, self._error)

    def _translated(self, result: tuple[str, str]) -> None:
        if not self.isVisible():
            return
        if self.caption is None and not self.text.toPlainText().strip():
            self._center()
        self.source.setPlainText(result[0])
        self.text.setPlainText(result[1])
        self._set_busy(False)
        self.status.setText("Bạn có thể sửa bản dịch và chỉnh vị trí trước khi lưu.")

    def _save(self) -> None:
        area = ocr_area.clean(self.picker.value())
        start, end = self.start.value(), self.end.value()
        text = self.text.toPlainText().strip()
        if area is None or not text or round(end - start, 2) < 0.02:
            self.status.setText("Chọn vùng, nhập bản dịch và đặt thời điểm kết thúc sau lúc bắt đầu.")
            return
        self.caption = region_captions.RegionCaption(
            self.sample, area, start, end, self.source.toPlainText().strip(), text,
            self.x.value() / 100, self.y.value() / 100)
        self.accept()
