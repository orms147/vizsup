"""“Dựng hàng loạt” — pick approved projects to render one after another in the
background, with the settings of a project already rendered (app/pipeline/batch.py).

Only approved projects are listed: the human gate stays where it is. Each video keeps
its own subtitle look when it has one (Sửa phụ đề → Kiểu chữ)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QListWidget, QListWidgetItem

from app.pipeline import batch
from app.pipeline.compilation import clean_title, episode_number
from desktop.widgets.chrome import button, label
from desktop.widgets.dialogs import VzDialog
from desktop.widgets.recent_projects import duration_label

ROLE = Qt.UserRole


def episode_order(rows: list[dict]) -> list[dict]:
    """Numbered titles by episode number first, the rest newest first. Pure."""
    numbered = sorted((r for r in rows if episode_number(r["title"]) is not None),
                      key=lambda r: episode_number(r["title"]))
    rest = sorted((r for r in rows if episode_number(r["title"]) is None),
                  key=lambda r: r.get("mtime") or 0, reverse=True)
    return numbered + rest


class BatchDialog(VzDialog):
    def __init__(self, parent, candidates: list[dict], templates: list[dict], *,
                 preselect: set[str] | None = None, waiting: int = 0,
                 queued: set[str] | None = None) -> None:
        super().__init__("Dựng hàng loạt", parent, width=640)
        self.body.addWidget(label(
            "Dựng lần lượt các video đã duyệt phụ đề — chạy nền, bạn vẫn sửa và duyệt video "
            "khác được. Chỉ video đã duyệt mới được dựng; sửa sau khi duyệt thì video đó được "
            "bỏ qua.", "secondary", wrap=True))
        queued = queued or set()
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        self.list.setStyleSheet("QListWidget::item{padding:5px 6px;}")
        for c in episode_order(candidates):
            note = "đã duyệt lại sau lần dựng" if c["state"] == "stale" else "chưa dựng"
            if c["id"] in queued:
                note = "đã trong hàng dựng"
            dur = f" · {duration_label(c['duration'])}" if c.get("duration") else ""
            it = QListWidgetItem(f"{clean_title(c['title'], 70)}{dur} · {note}")
            it.setData(ROLE, c["id"])
            it.setToolTip(c["title"])
            flags = it.flags() | Qt.ItemIsUserCheckable
            if c["id"] in queued:
                flags &= ~Qt.ItemIsEnabled
            it.setFlags(flags)
            pick = c["id"] not in queued and (not preselect or c["id"] in preselect)
            it.setCheckState(Qt.Checked if pick else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda _it: self._update())
        self.body.addWidget(self.list)
        row = QHBoxLayout()
        for text, state in (("Chọn tất cả", Qt.Checked), ("Bỏ chọn", Qt.Unchecked)):
            b = button(text, variant="ghost", small=True)
            b.clicked.connect(lambda _=False, st=state: self._set_all(st))
            row.addWidget(b)
        row.addStretch(1)
        if waiting:
            row.addWidget(label(f"{waiting} video khác chưa duyệt — mở để duyệt rồi thêm sau",
                                "small"))
        self.body.addLayout(row)
        if not candidates:
            self.body.addWidget(label("Chưa có video nào đã duyệt mà chưa dựng.", "small"))

        trow = QHBoxLayout()
        trow.setSpacing(10)
        trow.addWidget(label("Cài đặt dựng giống", "section"))
        self.template = QComboBox()
        for t in templates:
            self.template.addItem(clean_title(t["title"], 60), t["id"])
        self._templates = {t["id"]: t["opts"] for t in templates}
        self.template.currentIndexChanged.connect(lambda _i: self._update())
        trow.addWidget(self.template, 1)
        self.body.addLayout(trow)
        self.desc = label("", "secondary", wrap=True)
        self.body.addWidget(self.desc)
        self.body.addWidget(label("Kiểu phụ đề: mỗi video giữ kiểu đã chỉnh ở Sửa phụ đề; video "
                                  "chưa chỉnh dùng kiểu của video mẫu.", "small", wrap=True))
        self.add_button("Hủy")
        self.ok_btn = self.add_button("Dựng", icon_name="MonitorPlay", primary=True, accept=True)
        self._update()

    def _set_all(self, state) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.flags() & Qt.ItemIsEnabled:
                it.setCheckState(state)

    def picked(self) -> list[str]:
        return [self.list.item(i).data(ROLE) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked
                and self.list.item(i).flags() & Qt.ItemIsEnabled]

    def _update(self) -> None:
        tid = self.template.currentData()
        self.desc.setText(batch.describe(self._templates.get(tid)) if tid else
                          "Chưa có video nào đã dựng để làm mẫu — dựng một video ở trang Dựng "
                          "video trước (hoặc bấm “Dựng sau” ở đó).")
        n = len(self.picked())
        self.ok_btn.setText(f"Dựng {n} video" if n else "Dựng")
        self.ok_btn.setEnabled(bool(n and tid))

    def spec(self) -> dict:
        return {"ids": self.picked(), "template": self.template.currentData()}
