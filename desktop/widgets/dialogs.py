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
        self.review_requested = False      # "Xem N câu": go back and show the pending lines
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
        if pending:
            self.review_btn = self.add_button(f"Xem {pending} câu")
            self.review_btn.setToolTip("Quay lại và chỉ hiện các câu còn cần xem lại")
            self.review_btn.clicked.connect(lambda: setattr(self, "review_requested", True))
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


class BulkEditDialog(VzDialog):
    """Many proposed line changes at once ("Rút gọn hàng loạt", "Gợi ý AI"): one row per
    line — tick · câu N · current (struck) · editable proposal · optional note. Nothing
    changes until "Áp dụng"; ``chosen()`` returns [(row index, text)] of ticked rows."""

    def __init__(self, parent, *, title: str, intro: str,
                 rows: list[tuple[int, str, str, str]], checked: bool = True,
                 mark_rest: str = "") -> None:
        from PySide6.QtWidgets import QCheckBox, QLineEdit, QScrollArea

        super().__init__(title, parent, width=860)
        self.body.addWidget(label(intro, "secondary", wrap=True))
        bar = QHBoxLayout()
        self.count = label("", "small")
        bar.addWidget(self.count, 1)
        for text, on in (("Chọn hết", True), ("Bỏ chọn hết", False)):
            b = button(text, variant="ghost", small=True)
            b.clicked.connect(lambda _c=False, on=on: self._set_all(on))
            bar.addWidget(b)
        self.body.addLayout(bar)
        inner = QWidget()
        lst = QVBoxLayout(inner)
        lst.setContentsMargins(0, 0, 8, 0)
        lst.setSpacing(10)
        self._rows: list[tuple[int, QCheckBox, QLineEdit]] = []
        for idx, old, new, note in rows:
            card = QFrame()
            card.setObjectName("inset")
            g = QVBoxLayout(card)
            g.setContentsMargins(12, 8, 12, 8)
            g.setSpacing(4)
            head = QHBoxLayout()
            tick = QCheckBox(f"Câu {idx + 1}")
            tick.setChecked(checked)
            tick.toggled.connect(self._update_count)
            head.addWidget(tick)
            if note:
                head.addWidget(label(note, "small", wrap=True), 1)
            else:
                head.addStretch(1)
            g.addLayout(head)
            was = label(old, "secondary", wrap=True)
            was.setStyleSheet("text-decoration: line-through;")
            g.addWidget(was)
            ed = QLineEdit(new)
            ed.textEdited.connect(lambda _t, t=tick: t.setChecked(True))
            g.addWidget(ed)
            lst.addWidget(card)
            self._rows.append((idx, tick, ed))
        lst.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(560, 110 * max(1, len(rows))))
        self.body.addWidget(scroll, 1)
        # e.g. "the suggestions I didn't take: I looked, they're fine" → Đã xem
        self.mark_rest = QCheckBox(mark_rest) if mark_rest else None
        if self.mark_rest is not None:
            self.mark_rest.setChecked(True)
            self.mark_rest.toggled.connect(self._update_count)
            self.body.addWidget(self.mark_rest)
        self.add_button("Hủy")
        self.apply_btn = self.add_button("Áp dụng", icon_name="Check", primary=True, accept=True)
        self._update_count()

    def _set_all(self, on: bool) -> None:
        for _i, tick, _e in self._rows:
            tick.setChecked(on)

    def _update_count(self, *_a) -> None:
        n = sum(t.isChecked() for _i, t, _e in self._rows)
        self.count.setText(f"Đã chọn {n} / {len(self._rows)} câu")
        rest = self.mark_rest is not None and self.mark_rest.isChecked() and n < len(self._rows)
        self.apply_btn.setText(f"Áp dụng {n} câu" if n else
                               "Đánh dấu đã xem" if rest else "Áp dụng")
        self.apply_btn.setEnabled(n > 0 or rest)

    def unticked(self) -> list[int]:
        """Rows left unticked — only when the "mark the rest" box is on."""
        if self.mark_rest is None or not self.mark_rest.isChecked():
            return []
        return [i for i, t, _e in self._rows if not t.isChecked()]

    def chosen(self) -> list[tuple[int, str]]:
        return [(i, e.text().strip()) for i, t, e in self._rows
                if t.isChecked() and e.text().strip()]


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

    def __init__(self, parent, *, text: str, checklist: list[tuple[bool, str]],
                 suggest=None) -> None:
        from PySide6.QtWidgets import QApplication

        super().__init__("Mô tả đăng bài", parent, width=560)
        self.body.addWidget(label("Dán vào phần mô tả khi đăng. Sửa thoải mái — đây chỉ là bản "
                                  "nháp tự tạo từ thông tin video gốc.", "secondary", wrap=True))
        self._base = text
        self.text = QPlainTextEdit(text)
        self.text.setMinimumHeight(150)
        self.body.addWidget(self.text)
        self.ai_btn = None
        self.alt_titles = label("", "small", wrap=True)
        self.alt_titles.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.alt_titles.setVisible(False)
        if suggest is not None:
            # postcopy.suggest: one cheap AI call → a title on top + hashtags at the end
            from desktop.ui_thread import BgRunner

            self._bg = BgRunner(self)
            self.ai_btn = button("Gợi ý tiêu đề + hashtag (AI)", "Sparkles", variant="ghost",
                                 small=True)
            self.ai_btn.setToolTip("Một lần gọi model rẻ (≈ $0.0005) từ tiêu đề gốc, ngữ cảnh và "
                                   "lời thoại đầu video. Chỉ là bản nháp — sửa trước khi đăng.")

            def ask():
                self.ai_btn.setEnabled(False)
                self.ai_btn.setText("Đang nghĩ…")
                self._bg.run(suggest, self._suggested, self._suggest_failed)

            self.ai_btn.clicked.connect(ask)
            row = QHBoxLayout()
            row.addWidget(self.ai_btn)
            row.addStretch(1)
            self.body.addLayout(row)
            self.finished.connect(lambda _r: self._bg.shutdown())
        self.body.addWidget(self.alt_titles)
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

    def _suggested(self, picked: dict) -> None:
        from app.pipeline import postcopy

        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("Gợi ý lại")
        self.text.setPlainText(postcopy.compose(self._base, picked))
        others = picked.get("titles", [])[1:]
        self.alt_titles.setText("Tiêu đề khác: " + "  ·  ".join(others) if others else "")
        self.alt_titles.setVisible(bool(others))
        self.status.setText("Đã thêm tiêu đề + hashtag — sửa lại cho đúng ý bạn.")

    def _suggest_failed(self, msg: str) -> None:
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("Gợi ý tiêu đề + hashtag (AI)")
        self.status.setText(f"✗ {msg}")
