"""Vietnamese for Qt's own built-in strings (Qt ships no qtbase_vi.qm).

Standard dialog buttons ("Save", "Cancel", "&Yes" …) and the right-click menu of text
fields would otherwise show in English inside an all-Vietnamese app. One QTranslator,
installed in main(); anything not in the table falls through to Qt's English.
"""
from __future__ import annotations

from PySide6.QtCore import QTranslator

STRINGS: dict[tuple[str, str], str] = {
    ("QPlatformTheme", "OK"): "OK",
    ("QPlatformTheme", "Save"): "Lưu",
    ("QPlatformTheme", "Save All"): "Lưu tất cả",
    ("QPlatformTheme", "Open"): "Mở",
    ("QPlatformTheme", "&Yes"): "&Có",
    ("QPlatformTheme", "Yes to &All"): "Có với &tất cả",
    ("QPlatformTheme", "&No"): "&Không",
    ("QPlatformTheme", "N&o to All"): "Không với tất &cả",
    ("QPlatformTheme", "Abort"): "Hủy bỏ",
    ("QPlatformTheme", "Retry"): "Thử lại",
    ("QPlatformTheme", "Ignore"): "Bỏ qua",
    ("QPlatformTheme", "Close"): "Đóng",
    ("QPlatformTheme", "Cancel"): "Hủy",
    ("QPlatformTheme", "Discard"): "Bỏ thay đổi",
    ("QPlatformTheme", "Don't Save"): "Không lưu",
    ("QPlatformTheme", "Help"): "Trợ giúp",
    ("QPlatformTheme", "Apply"): "Áp dụng",
    ("QPlatformTheme", "Reset"): "Đặt lại",
    ("QPlatformTheme", "Restore Defaults"): "Khôi phục mặc định",
}
# right-click menu of QLineEdit / QTextEdit / QPlainTextEdit (same texts, two contexts)
for _ctx in ("QLineEdit", "QWidgetTextControl"):
    STRINGS.update({
        (_ctx, "&Undo"): "&Hoàn tác", (_ctx, "&Redo"): "&Làm lại", (_ctx, "Cu&t"): "Cắ&t",
        (_ctx, "&Copy"): "&Sao chép", (_ctx, "&Paste"): "&Dán", (_ctx, "Delete"): "Xóa",
        (_ctx, "Select All"): "Chọn tất cả", (_ctx, "Copy &Link Location"): "Sao chép &liên kết",
    })


class ViTranslator(QTranslator):
    def translate(self, context, source, disambiguation=None, n=-1):  # noqa: D401 - Qt API
        return STRINGS.get((context, source)) or ""

    def isEmpty(self) -> bool:  # noqa: N802 - Qt API
        return False
