"""Project-level output substitutions."""
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from app.pipeline import word_filter
from desktop.widgets.chrome import button, label


class WordFilterDialog(QDialog):
    def __init__(self, enabled: bool, rules: list[tuple[str, str]], parent=None, *,
                 keep_where: str | None = None, kept: bool = False) -> None:
        """``keep_where``: the series name new episodes take the list from, "" = app-wide, None =
        no "keep for new" choice; ``kept``: a list is kept there already."""
        super().__init__(parent)
        self.setWindowTitle("Lọc từ trong bản xuất")
        self.resize(560, 400)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Tự thay từ khi dựng phụ đề và lồng tiếng")
        self.enabled.setChecked(enabled)
        layout.addWidget(self.enabled)
        layout.addWidget(label("Mỗi dòng nhập một cặp, ví dụ giết → dít. "
                               "Danh sách gợi ý có thể chỉnh theo cách bạn muốn biên tập.", wrap=True))
        self.editor = QPlainTextEdit()
        self._set_rules(rules)
        layout.addWidget(self.editor)
        defaults = button("Dùng danh sách gợi ý", "RefreshCw")
        defaults.clicked.connect(lambda: self._set_rules(word_filter.DEFAULT_RULES))
        layout.addWidget(defaults)
        self.keep = None
        if keep_where is not None:
            self.keep = QCheckBox(f"Dùng danh sách này cho các tập mới của bộ phim 《{keep_where}》"
                                  if keep_where else "Dùng danh sách này cho mọi video mới")
            self.keep.setToolTip("Video mới (dán link, Telegram, theo dõi) tự nhận danh sách này "
                                 "khi bắt đầu chạy. Video đã duyệt không bị đổi.")
            self.keep.setChecked(kept)
            layout.addWidget(self.keep)
        self.status = label("", wrap=True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rules = rules

    def _set_rules(self, rules) -> None:
        self.editor.setPlainText("\n".join(f"{a} → {b}" for a, b in rules))

    def _save(self) -> None:
        rules = []
        seen = set()
        for i, line in enumerate(self.editor.toPlainText().splitlines(), 1):
            if not line.strip():
                continue
            source, arrow, target = line.partition("→" if "→" in line else "->")
            source = " ".join(source.split())
            key = word_filter.normal(source).lower()
            if not arrow or not source or not target.strip() or key in seen:
                self.status.setText(f"Dòng {i} cần một cặp từ hợp lệ và chưa có trong danh sách.")
                return
            seen.add(key)
            rules.append((source, target.strip()))
        self.rules = rules
        self.accept()
