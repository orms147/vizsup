"""The 1 · Nhập / 2 · Sửa phụ đề / 3 · Dựng video step indicator in the toolbar."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from desktop.theme import C

STEPS = ["Nhập", "Sửa phụ đề", "Dựng video"]


class FlowSteps(QFrame):
    clicked = Signal(int)  # a step chip was clicked (0=Input, 1=Editor, 2=Render)

    def __init__(self) -> None:
        super().__init__()
        self._chips: list[QLabel] = []
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for i, name in enumerate(STEPS):
            chip = QLabel()
            chip.setTextFormat(Qt.RichText)
            self._chips.append(chip)
            lay.addWidget(chip)
            if i < len(STEPS) - 1:
                sep = QLabel("›")
                sep.setStyleSheet(f"color:{C['line4']};font-size:15px;")
                lay.addWidget(sep)
        self.set_active(0)

    def set_active(self, idx: int) -> None:
        for i, chip in enumerate(self._chips):
            num = i + 1
            if i < idx:  # done
                badge = f"<span style='color:{C['green']};font-weight:700'>✓</span>"
                color = C["muted"]
            elif i == idx:  # active
                badge = f"<span style='color:#fff;font-weight:700'>{num}</span>"
                color = C["text_hi"]
            else:  # pending
                badge = f"<span style='color:{C['muted3']}'>{num}</span>"
                color = C["muted3"]
            weight = "600" if i == idx else "500"
            chip.setText(
                f"<span style='font-family:JetBrains Mono'>{badge}</span>"
                f"&nbsp;&nbsp;<span style='color:{color};font-weight:{weight};font-size:13px'>{STEPS[i]}</span>"
            )
            if i == idx:
                chip.setStyleSheet(
                    f"background:rgba(139,92,246,0.13);border:1px solid rgba(139,92,246,0.34);"
                    f"border-radius:8px;padding:5px 11px;"
                )
            else:
                chip.setStyleSheet("padding:5px 7px;background:transparent;border:none;")

    def mousePressEvent(self, e) -> None:
        pos = e.position().toPoint()
        for i, chip in enumerate(self._chips):
            if chip.geometry().contains(pos):
                self.clicked.emit(i)
                return
        super().mousePressEvent(e)
