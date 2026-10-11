"""The "?" overlay: every editor shortcut in one grid (v2 design.js showShortcuts)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout

from desktop.widgets.chrome import button, kbd, label

SHORTCUTS = [
    ("Space", "Phát / dừng video"),
    ("Ctrl+Space", "Lặp câu đang chọn"),
    ("↑ / ↓", "Câu trước / sau"),
    ("Tab / Shift+Tab", "Câu sau / trước trong danh sách"),
    ("Enter", "Sửa; lưu và sang câu sau"),
    ("Shift+Enter", "Xuống dòng phụ đề"),
    ("[ / ]", "Đặt đầu / cuối = vị trí phát"),
    ("Alt+← / →", "Dịch đầu câu 50 ms"),
    ("Ctrl+Alt+← / →", "Dịch cuối câu 50 ms"),
    ("Giữ thêm Shift", "Dịch 200 ms"),
    ("A / Shift+A", "Tự canh câu / tất cả câu theo giọng nói"),
    ("S", "Tách câu tại vị trí phát"),
    ("M", "Gộp với câu sau"),
    ("Ctrl+Z / Ctrl+Y", "Hoàn tác / làm lại"),
    ("Ctrl+F", "Tìm và thay tiếng Việt"),
    ("F8 / Shift+F8", "Câu cần xem lại sau / trước"),
    ("Ctrl+Enter", "Duyệt và dựng video"),
    ("Ctrl + cuộn", "Thu phóng dòng thời gian"),
    ("?", "Mở bảng phím tắt"),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Phím tắt")
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(14)
        v.addWidget(label("Phím tắt · sửa nhanh, ít chạm chuột", "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        half = (len(SHORTCUTS) + 1) // 2
        for i, (keys, desc) in enumerate(SHORTCUTS):
            row = QFrame()
            row.setObjectName("inset")
            h = QHBoxLayout(row)
            h.setContentsMargins(11, 7, 9, 7)
            h.addWidget(label(desc, "secondary"))
            h.addStretch(1)
            h.addWidget(kbd(keys))
            grid.addWidget(row, i % half, i // half)
        v.addLayout(grid)
        close = button("Đóng", variant="primary")
        close.clicked.connect(self.accept)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(close)
        v.addLayout(h)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
