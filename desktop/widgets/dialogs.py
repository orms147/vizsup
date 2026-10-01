"""v2 dialogs: approval summary (the human gate's one explicit confirmation) and the
"Rút gọn" proposal (applied only when the user picks it)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from desktop.theme import V2
from desktop.widgets.chrome import button, icon_label, label


class VzDialog(QDialog):
    """Title, body, right-aligned buttons. ``self.body`` is the content layout."""

    def __init__(self, title: str, parent: QWidget | None = None, *, width: int = 460) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.setStyleSheet(f"QDialog{{background:{V2['panel']};}}")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(14)
        v.addWidget(label(title, "h2"))
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        v.addLayout(self.body)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(8)
        self._buttons.addStretch(1)
        v.addLayout(self._buttons)

    def add_button(self, text: str, *, icon_name: str | None = None, primary: bool = False,
                   accept: bool = False):
        b = button(text, icon_name, variant="primary" if primary else "")
        b.clicked.connect(self.accept if accept else self.reject)
        self._buttons.addWidget(b)
        if primary:
            b.setDefault(True)
        return b


class ApproveDialog(VzDialog):
    def __init__(self, parent, *, cues: int, voice: str, duration: str, pending: int) -> None:
        super().__init__("Duyệt phụ đề để lồng tiếng", parent, width=480)
        self.body.addWidget(label(
            "Nội dung bạn đang thấy sẽ được dùng để tạo giọng tiếng Việt. Sau bước này, "
            "bạn chọn âm thanh và dựng video.", "secondary", wrap=True))
        box = QFrame()
        box.setObjectName("inset")
        h = QHBoxLayout(box)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(18)
        for title, value in (("Phụ đề", f"{cues} câu"), ("Giọng đọc", voice),
                             ("Thời lượng", duration)):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(label(title, "small"))
            col.addWidget(label(value, "h3"))
            h.addLayout(col)
        h.addStretch(1)
        self.body.addWidget(box)
        if pending:
            warn = QFrame()
            warn.setObjectName("warnCard")
            wh = QHBoxLayout(warn)
            wh.setContentsMargins(12, 8, 12, 8)
            wh.addWidget(icon_label("TriangleAlert", V2["amber"], 15))
            wh.addWidget(label(f"Còn {pending} câu cần xem lại. Bạn vẫn có thể duyệt nếu "
                               f"đã kiểm tra.", "warnText", wrap=True), 1)
            self.body.addWidget(warn)
        self.body.addWidget(label(
            "Chỉnh sửa nội dung sau khi duyệt sẽ yêu cầu duyệt lại.", "small", wrap=True))
        self.add_button("Quay lại sửa")
        self.add_button("Duyệt phụ đề", icon_name="ShieldCheck", primary=True, accept=True)


class ShortenDialog(VzDialog):
    def __init__(self, parent, *, index: int, current: str, proposal: str) -> None:
        super().__init__(f"Rút gọn câu {index + 1}", parent, width=480)
        self.body.addWidget(label("Hiện tại", "small"))
        cur = label(current, "secondary", wrap=True)
        cur.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.addWidget(cur)
        self.body.addWidget(label("Đề xuất · giữ ý chính", "purpleText"))
        self.edit = QPlainTextEdit(proposal)
        self.edit.setFixedHeight(76)
        self.body.addWidget(self.edit)
        self.add_button("Giữ nguyên")
        self.add_button("Dùng đề xuất", icon_name="Check", primary=True, accept=True)

    def text(self) -> str:
        return self.edit.toPlainText().strip()
