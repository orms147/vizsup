"""Glossary editor dialog (N-23) — maintain the zh→vi term table without
hand-editing glossary.json. Two scopes: global (<storage>/glossary.json, every
project) and the open job (<work_dir>/glossary.json, wins on conflict). All
file I/O goes through backend helpers; this dialog is a thin shell.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from desktop import paths  # noqa: F401  (puts backend on sys.path)
from app.config import settings  # noqa: E402
from app.pipeline.translate import save_glossary  # noqa: E402


def _load_raw(path: Path) -> dict[str, str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class GlossaryDialog(QDialog):
    def __init__(self, parent=None, job=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bảng thuật ngữ (Trung → Việt)")
        self.resize(480, 420)
        self._paths: dict[str, Path] = {
            "global": Path(settings.vizsup_storage_dir) / "glossary.json"}
        if job is not None:
            self._paths["job"] = job.work_dir / "glossary.json"
        self._cache: dict[str, dict[str, str]] = {}   # scope → edited entries
        self._scope = "global"

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Phạm vi:"))
        self.scope = QComboBox()
        self.scope.addItem("Chung (mọi dự án)", "global")
        if job is not None:
            self.scope.addItem("Dự án này (ghi đè mục chung)", "job")
        self.scope.currentIndexChanged.connect(self._on_scope)
        row.addWidget(self.scope, 1)
        lay.addLayout(row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Tiếng Trung", "Tiếng Việt"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 200)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        add = QPushButton("+ Thêm dòng")
        add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        rm = QPushButton("− Xóa dòng chọn")
        rm.clicked.connect(self._remove_row)
        btns.addWidget(add)
        btns.addWidget(rm)
        btns.addStretch(1)
        lay.addLayout(btns)

        note = QLabel("Bảng này được tự nạp vào bước dịch; mục của dự án ghi đè mục chung.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save_all)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._fill(_load_raw(self._paths["global"]))

    # --- helpers ---------------------------------------------------------------
    def _fill(self, entries: dict[str, str]) -> None:
        self.table.setRowCount(0)
        for zh, vi in entries.items():
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(zh))
            self.table.setItem(r, 1, QTableWidgetItem(vi))

    def _collect(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in range(self.table.rowCount()):
            zh = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            vi = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if zh and vi:
                out[zh] = vi
        return out

    def _remove_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _on_scope(self) -> None:
        self._cache[self._scope] = self._collect()    # stash edits before switching
        self._scope = self.scope.currentData()
        entries = self._cache.get(self._scope)
        if entries is None:
            entries = _load_raw(self._paths[self._scope])
        self._fill(entries)

    def _save_all(self) -> None:
        self._cache[self._scope] = self._collect()
        for scope, entries in self._cache.items():    # only touched scopes are cached
            save_glossary(self._paths[scope], entries)
        self.accept()
