"""«Dọn dung lượng» — the whole library's finished projects, three ticks (app/pipeline/cleanup.py).

Opened from Thư viện with the ``cleanup.scan`` rows already read off the GUI thread. Ticks:
file tạm (on), video gốc tải lại được (on), video đã đăng xong (off — the finished videos, only
for projects whose every post is already on its platform). Everything goes to the Recycle Bin."""
from __future__ import annotations

import shutil

from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QVBoxLayout

from app.pipeline import cleanup
from desktop.icons import icon
from desktop.theme import V2
from desktop.widgets.chrome import icon_label, label
from desktop.widgets.dialogs import VzDialog

KIND_TEXT = {
    "cache": ("File tạm", "Giọng lồng, âm thanh đã dựng, nhạc tách, bản xem nhanh — dựng lại "
                          "là tự tạo lại.", True),
    "source": ("Video gốc", "Tải lại được từ link khi cần (mở lại, cắt Short).", True),
    "output": ("Video đã đăng xong", "Bản dựng + bản dọc của tập đã lên đủ mọi nền tảng. Dự án "
                                     "vẫn là \"Đã dựng\", không bị dựng lại; muốn có lại video "
                                     "thì mở và dựng.", False),
}


def fmt(n: int) -> str:
    from desktop.views.library_view import fmt_size

    return fmt_size(n)


class CleanupDialog(VzDialog):
    def __init__(self, parent, rows: list[dict], storage) -> None:
        super().__init__("Dọn dung lượng", parent, width=540)
        self.rows = rows
        tot = cleanup.totals(rows)
        try:
            free = shutil.disk_usage(str(storage)).free
            free_text = f" · ổ đĩa còn trống {fmt(free)}"
        except OSError:
            free_text = ""
        self.body.addWidget(label(
            f"Chỉ xét dự án đã dựng xong ít nhất {cleanup.KEEP_DAYS} ngày, đúng bản đã duyệt. Phụ "
            f"đề, cài đặt, ảnh bìa luôn được giữ{free_text}.", "secondary", wrap=True))
        box = QFrame()
        box.setObjectName("inset")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(10)
        self.checks: dict[str, QCheckBox] = {}
        for kind in cleanup.KINDS:
            title, hint, on = KIND_TEXT[kind]
            n = sum(1 for r in rows if r[kind])
            row = QHBoxLayout()
            cb = QCheckBox(f"{title} · {n} dự án")
            cb.setChecked(on and tot[kind] > 0)
            cb.setEnabled(tot[kind] > 0)
            cb.toggled.connect(self._update)
            self.checks[kind] = cb
            row.addWidget(cb, 1)
            row.addWidget(label(fmt(tot[kind]), "monoText"))
            bl.addLayout(row)
            bl.addWidget(label(hint if tot[kind] or kind != "output" else
                               "Chưa có tập nào đăng xong (Tự động đăng).", "small", wrap=True))
        self.body.addWidget(box)
        t = QHBoxLayout()
        t.addWidget(label("Giải phóng", "secondary"))
        t.addStretch(1)
        self.total = label("", "h3")
        t.addWidget(self.total)
        self.body.addLayout(t)
        note = QHBoxLayout()
        note.setSpacing(8)
        note.addWidget(icon_label("Trash2", V2["muted"], 14))
        note.addWidget(label("Chuyển vào Thùng rác — khôi phục được; dọn Thùng rác để lấy lại "
                             "dung lượng.", "small", wrap=True), 1)
        self.body.addLayout(note)
        self.add_button("Hủy")
        self.ok = self.add_button("Dọn", icon_name="Trash2", accept=True)
        self.ok.setObjectName("danger")
        self.ok.setIcon(icon("Trash2", V2["red"], 15))
        self.ok.setDefault(False)
        self._update()

    def kinds(self) -> set[str]:
        return {k for k, cb in self.checks.items() if cb.isChecked() and cb.isEnabled()}

    def paths(self):
        return cleanup.paths_for(self.rows, self.kinds())

    def _update(self, *_a) -> None:
        tot = cleanup.totals(self.rows)
        n = sum(tot[k] for k in self.kinds())
        self.total.setText(fmt(n))
        self.ok.setEnabled(n > 0)
