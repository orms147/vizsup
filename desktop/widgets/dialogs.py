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


class RetranslateDialog(VzDialog):
    """E-6 "Dịch lại": three AI renderings of one line + an optional request.
    The editor fills the options asynchronously (``set_options`` / ``set_error``)."""

    from PySide6.QtCore import Signal as _Signal

    regenerate = _Signal(str)

    def __init__(self, parent, *, index: int, zh: str, vi: str) -> None:
        from PySide6.QtWidgets import QButtonGroup, QLineEdit, QRadioButton

        super().__init__(f"Dịch lại câu {index + 1}", parent, width=560)
        zh_lbl = label(zh, "secondary", wrap=True)
        zh_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.addWidget(zh_lbl)
        self.body.addWidget(label(f"Hiện tại: {vi}", "small", wrap=True))
        self.status = label("AI đang viết 3 phương án…", "small")
        self.body.addWidget(self.status)
        self.group = QButtonGroup(self)
        self.radios: list[QRadioButton] = []
        for k, style in enumerate(("Sát nghĩa", "Tự nhiên", "Ngắn gọn")):
            rb = QRadioButton("")
            rb.setToolTip(style)
            rb.setVisible(False)
            self.group.addButton(rb, k)
            self.radios.append(rb)
            self.body.addWidget(rb)
        row = QHBoxLayout()
        self.request = QLineEdit()
        self.request.setPlaceholderText("Yêu cầu thêm (tùy chọn): trang trọng hơn, giữ chơi chữ…")
        again = button("Tạo lại", "RefreshCw", small=True)
        again.clicked.connect(self._again)
        self.request.returnPressed.connect(self._again)
        row.addWidget(self.request, 1)
        row.addWidget(again)
        self.body.addLayout(row)
        self.add_button("Giữ nguyên")
        self.use_btn = self.add_button("Dùng phương án này", icon_name="Check", primary=True,
                                       accept=True)
        self.use_btn.setEnabled(False)

    def _again(self) -> None:
        self.status.setText("AI đang viết lại…")
        self.use_btn.setEnabled(False)
        self.regenerate.emit(self.request.text().strip())

    def set_options(self, options: list[str]) -> None:
        styles = ("Sát nghĩa", "Tự nhiên", "Ngắn gọn")
        for k, rb in enumerate(self.radios):
            ok = k < len(options)
            rb.setVisible(ok)
            if ok:
                rb.setText(f"{styles[k]} — {options[k]}")
                rb.setProperty("text_value", options[k])
        if self.radios and options:
            self.radios[min(1, len(options) - 1)].setChecked(True)
        self.status.setText("Chọn một phương án (hoặc nhập yêu cầu rồi “Tạo lại”).")
        self.use_btn.setEnabled(bool(options))

    def set_error(self, msg: str) -> None:
        self.status.setText(f"Không dịch lại được: {msg}")

    def choice(self) -> str:
        b = self.group.checkedButton()
        return (b.property("text_value") or "") if b else ""


class PostDialog(VzDialog):
    """F-1/F-2/F-3: the post description (editable, one click to copy) and a short
    checklist before posting."""

    def __init__(self, parent, *, text: str, checklist: list[tuple[bool, str]]) -> None:
        from PySide6.QtWidgets import QApplication

        super().__init__("Mô tả đăng bài", parent, width=560)
        self.body.addWidget(label("Dán vào phần mô tả khi đăng. Sửa thoải mái — đây chỉ là bản "
                                  "nháp tự tạo từ thông tin video gốc.", "secondary", wrap=True))
        self.text = QPlainTextEdit(text)
        self.text.setMinimumHeight(150)
        self.body.addWidget(self.text)
        self.body.addWidget(label("Trước khi đăng", "h3"))
        for done, item in checklist:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(icon_label("CircleCheck" if done else "CircleAlert",
                                     V2["green"] if done else V2["amber"], 14), 0, Qt.AlignTop)
            row.addWidget(label(item, "small", wrap=True), 1)
            self.body.addLayout(row)
        self.status = label("", "small")
        self.body.addWidget(self.status)
        copy = button("Sao chép", "Copy", variant="primary")

        def do_copy():
            QApplication.clipboard().setText(self.text.toPlainText())
            self.status.setText("Đã sao chép mô tả.")

        copy.clicked.connect(do_copy)
        self._buttons.addWidget(copy)
        self.add_button("Đóng")
