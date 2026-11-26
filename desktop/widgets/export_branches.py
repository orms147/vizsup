"""Destination tree for direct exports."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from app.config import settings
from app.fsutil import atomic_write_text
from app.pipeline.direct_export import ROOT
from app.pipeline.export import safe_name
from desktop.widgets.chrome import button, label


# "Độ phân giải tối đa" of a branch → render options (assemble.UPSCALE_RES: a smaller source is
# made bigger up to it); "AI làm nét" is its own tick (pipeline/upscale.py). A 9:16 branch keeps
# its 1080×1920 canvas, the AI tick still applies to its picture.
QUALITIES = (("Theo bộ phim / trang Dựng", ""),
             ("Giữ độ phân giải nguồn", "source"),
             ("Tối đa 1080p", "1080"),
             ("Tối đa 1440p · YouTube", "1440"))
QUALITIES_BY_KEY = {"": {},                  # the series' / app's own resolution
                    "source": {"res": "source"},
                    "1080": {"res": "1080"},
                    "1440": {"res": "1440"},
                    "1440ai": {"res": "1440", "upscale": "ai"}}     # saved before 2026-11-26


def branch_render(node: dict) -> dict:
    """The render options a branch carries: its resolution (not for 9:16) and the AI tick."""
    out: dict = {}
    quality = node.get("quality") or ""
    if node.get("frame", "source") != "tiktok":
        out.update(QUALITIES_BY_KEY.get(quality, {}))
    if node.get("ai") or quality == "1440ai":
        out["upscale"] = "ai"
    return out


class ExportBranches(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.path = Path(settings.vizsup_storage_dir) / "_branches.json"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        self.nodes = data.get("nodes", [
            {"id": "animation", "parent": "", "name": "Phim hoạt hình 2D",
             "folder": str(Path(ROOT) / "Phim hoạt hình 2D"), "frame": "source"},
            {"id": "short", "parent": "", "name": "Short",
             "folder": str(Path(ROOT) / "Short"), "frame": "tiktok"},
        ])
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMaximumHeight(116)
        self.frames = QCheckBox("Kèm khung hình làm bìa")
        self.frames.setChecked(bool(data.get("frames")))
        self.error = label("", "small", wrap=True)
        self.error.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Thư mục thành phẩm", "h3"))
        layout.addWidget(self.tree)
        actions = QHBoxLayout()
        self.add = button("Thêm nhánh", "Plus", small=True)
        self.edit = button("Sửa", "Type", small=True)
        self.remove = button("Gỡ nhánh", "Trash2", small=True)
        self.add.clicked.connect(lambda: self._edit(new=True))
        self.edit.clicked.connect(lambda: self._edit(new=False))
        self.remove.clicked.connect(self._remove)
        for action in (self.add, self.edit, self.remove):
            actions.addWidget(action)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(self.frames)
        layout.addWidget(self.error)
        self._fill(data.get("selected", ""))
        self.tree.currentItemChanged.connect(self._changed)
        self.frames.toggled.connect(self._save)
        self._changed(save=False)

    def _fill(self, selected: str) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        root = QTreeWidgetItem(self.tree, [ROOT])
        root.setData(0, Qt.UserRole, "")
        items = {"": root}
        pending = list(self.nodes)
        while pending:
            rest = []
            for node in pending:
                parent = items.get(node["parent"])
                if parent is None:
                    rest.append(node)
                    continue
                item = QTreeWidgetItem(parent, [node["name"]])
                item.setData(0, Qt.UserRole, node["id"])
                item.setToolTip(0, node["folder"])
                items[node["id"]] = item
            if len(rest) == len(pending):
                break
            pending = rest
        self.tree.expandAll()
        self.tree.setCurrentItem(items.get(selected, root))
        self.tree.blockSignals(False)

    def _selection(self) -> str:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else ""

    def _changed(self, *_args, save=True) -> None:
        editable = bool(self._selection())
        self.edit.setEnabled(editable)
        self.remove.setEnabled(editable)
        if save:
            self._save()

    def _save(self, *_args) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps(
                {"nodes": self.nodes, "selected": self._selection(), "frames": self.frames.isChecked()},
                ensure_ascii=False, indent=2))
            self.error.hide()
        except OSError as exc:
            self.error.setText(f"Chưa lưu được cây thư mục ({exc}).")
            self.error.show()

    def request(self) -> dict:
        node = next((n for n in self.nodes if n["id"] == self._selection()), {})
        out = {"folder": node.get("folder", ROOT), "frame": node.get("frame", "source"),
               "frames": self.frames.isChecked()}
        out.update(branch_render(node))
        return out

    def _edit(self, *, new: bool) -> None:
        selected = self._selection()
        old = next((n for n in self.nodes if n["id"] == selected), {})
        if not new and not old:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Thêm nhánh" if new else "Sửa nhánh")
        form = QFormLayout(dialog)
        name = QLineEdit("" if new else old["name"])
        folder = QLineEdit("" if new else old["folder"])
        base = Path(self.request()["folder"])
        folder.setPlaceholderText(str(base))
        pick = button("Chọn thư mục", "FolderOpen", small=True)

        def browse():
            chosen = QFileDialog.getExistingDirectory(dialog, "Chọn thư mục thành phẩm",
                                                       folder.text() or str(base))
            if chosen:
                folder.setText(chosen)

        pick.clicked.connect(browse)
        row = QHBoxLayout()
        row.addWidget(folder)
        row.addWidget(pick)
        frame = QComboBox()
        frame.addItem("Giữ tỷ lệ nguồn", "source")
        frame.addItem("Short · khung dọc 9:16", "tiktok")
        frame.setCurrentIndex(frame.findData(old.get("frame", "source")))
        quality = QComboBox()
        for text, key in QUALITIES:
            quality.addItem(text, key)
        saved = old.get("quality") or ""
        quality.setCurrentIndex(max(quality.findData("1440" if saved == "1440ai" else saved), 0))
        quality.setToolTip("Độ phân giải tối đa: video nguồn nhỏ hơn được phóng to bằng card đồ "
                           "họa (Douyin 720p → 1080p). Video dài đăng YouTube: 1440p nét hơn trên "
                           "màn lớn sau khi YouTube nén lại (file ~3×).")
        ai = QCheckBox("AI làm nét (chậm, cần card NVIDIA)")
        ai.setChecked(bool(old.get("ai")) or saved == "1440ai")
        ai.setToolTip("Real-ESRGAN: viền tóc, mắt, nét vẽ sắc và sạch — hợp hoạt hình / phim AI; "
                      "da người thật mịn hơn. Lần đầu ~3× thời lượng video ở 1080p.")
        frame.currentIndexChanged.connect(
            lambda _i: quality.setEnabled(frame.currentData() != "tiktok"))
        quality.setEnabled(frame.currentData() != "tiktok")
        form.addRow("Tên nhánh", name)
        form.addRow("Thư mục đích", row)
        form.addRow("Mẫu dựng", frame)
        form.addRow("Độ phân giải", quality)
        form.addRow("", ai)
        error = label("", "small", wrap=True)
        form.addRow(error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            title = name.text().strip()
            target = Path(folder.text().strip()) if folder.text().strip() else base / safe_name(title, "")
            if not title or not target.is_absolute():
                error.setText("Nhập tên nhánh và đường dẫn thư mục đầy đủ.")
                return
            node = {"id": uuid.uuid4().hex if new else selected, "name": title,
                    "parent": selected if new else old["parent"],
                    "folder": str(target), "frame": frame.currentData(),
                    "quality": quality.currentData(), "ai": ai.isChecked()}
            if new:
                self.nodes.append(node)
            else:
                old.update(node)
            self._fill(node["id"])
            self._changed()
            dialog.accept()

        buttons.accepted.connect(save)
        dialog.resize(550, 170)
        dialog.exec()

    def _remove(self) -> None:
        selected = self._selection()
        if not selected:
            return
        parent = next(n["parent"] for n in self.nodes if n["id"] == selected)
        removed = {selected}
        while True:
            children = {n["id"] for n in self.nodes if n["parent"] in removed}
            if children <= removed:
                break
            removed.update(children)
        self.nodes = [n for n in self.nodes if n["id"] not in removed]
        self._fill(parent)
        self._changed()
