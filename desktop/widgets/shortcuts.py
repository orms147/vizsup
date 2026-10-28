"""The "?" overlay: every shortcut in one place (v2 design.js showShortcuts) — the editor's
keys, the whole-app keys and Thư viện's. Rows never clip their text (a description that is
wider than its row is a bug: tests/test_ux_shortcuts.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop.widgets.chrome import button, kbd, label

# (section, [(keys, description)]) — descriptions stay short: two columns of ~420 px
LEFT = [
    ("Phát và di chuyển", [
        ("Space", "Phát / dừng video"),
        ("Ctrl+Space", "Lặp câu đang chọn"),
        ("↑ / ↓", "Câu trước / sau"),
        ("← / →", "Tua lùi / tới 10 giây"),
        ("F / Esc", "Video toàn màn hình / thoát"),
        ("Tab / Shift+Tab", "Câu sau / trước"),
        ("F8 / Shift+F8", "Câu cần xem lại sau / trước"),
        ("Ctrl + cuộn", "Thu phóng dòng thời gian"),
    ]),
    ("Duyệt", [
        ("D / Ctrl+D", "Đã xem, tới câu cần xem tiếp"),
        ("Ctrl+Enter", "Duyệt và dựng video"),
    ]),
    ("Toàn ứng dụng", [
        ("Ctrl+L", "Mở Thư viện"),
        ("Ctrl+,", "Mở Cài đặt"),
        ("Ctrl+1 / 2 / 3", "Bắt đầu / Sửa phụ đề / Dựng"),
        ("Esc", "Thoát Thư viện / Cài đặt"),
        ("?", "Mở bảng phím tắt"),
    ]),
    ("Thư viện", [
        ("↑ / ↓", "Chọn dự án"),
        ("Enter / Space", "Mở để sửa / xem video"),
        ("Delete", "Xóa dự án (hỏi lại trước)"),
        ("Ctrl+A", "Chọn tất cả"),
        ("Ctrl+F", "Tìm theo tên"),
    ]),
]
RIGHT = [
    ("Sửa câu", [
        ("Enter", "Lưu và sang câu sau"),
        ("Shift+Enter", "Xuống dòng phụ đề"),
        ("Ctrl+↑ / ↓", "Câu trước / sau, ngay trong ô"),
        ("Esc", "Rời ô sửa, về danh sách"),
        ("[ / ]", "Đặt đầu / cuối = vị trí phát"),
        ("Alt+← / →", "Dịch đầu câu 50 ms"),
        ("Ctrl+Alt+← / →", "Dịch cuối câu 50 ms"),
        ("Giữ thêm Shift", "Dịch 200 ms"),
        ("A / Shift+A", "Tự canh câu / tất cả câu"),
        ("Alt+R", "Dịch lại câu (3 phương án)"),
        ("S", "Tách câu tại vị trí phát"),
        ("M", "Gộp với câu sau"),
        ("Chuột phải", "Menu câu: tách, gộp, xóa…"),
        ("Ctrl+Z / Ctrl+Y", "Hoàn tác / làm lại"),
        ("Ctrl+F", "Tìm tiếng Việt"),
        ("Ctrl+H", "Tìm và thay thế"),
    ]),
]
SECTIONS = LEFT + RIGHT
SHORTCUTS = [row for _title, rows in SECTIONS for row in rows]      # flat list (v1 name)


def _column(sections) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(4)
    for n, (title, rows) in enumerate(sections):
        head = label(title, "section")
        head.setContentsMargins(2, 10 if n else 0, 0, 2)
        col.addWidget(head)
        for keys, desc in rows:
            row = QFrame()
            row.setObjectName("inset")
            h = QHBoxLayout(row)
            h.setContentsMargins(11, 4, 9, 4)
            d = label(desc, "secondary")
            d.setObjectName("shortcutDesc")
            h.addWidget(d)
            h.addStretch(1)
            h.addWidget(kbd(keys))
            col.addWidget(row)
    col.addStretch(1)
    return col


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Phím tắt")
        self.setMinimumWidth(900)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(14)
        v.addWidget(label("Phím tắt · sửa nhanh, ít chạm chuột", "h2"))
        inner = QWidget()
        inner.setObjectName("bare")
        cols = QHBoxLayout(inner)
        cols.setContentsMargins(0, 0, 8, 0)
        cols.setSpacing(18)
        cols.addLayout(_column(LEFT), 1)
        cols.addLayout(_column(RIGHT), 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen is not None else 800
        # as tall as its content, but never taller than the screen allows (then it scrolls)
        scroll.setMinimumHeight(min(inner.sizeHint().height() + 4, int(avail * 0.95) - 170))
        self.scroll = scroll
        v.addWidget(scroll, 1)
        close = button("Đóng", variant="primary")
        close.clicked.connect(self.accept)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(close)
        v.addLayout(h)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
