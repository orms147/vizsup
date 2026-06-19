"""A QComboBox populated from registry.list_providers() entries.
Shows 'display_name — cost_note'; disables options where available is false.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import QComboBox


class ProviderCombo(QComboBox):
    def __init__(self, providers: list[dict], *, allow_none: bool = False) -> None:
        super().__init__()
        self._names: list[str | None] = []
        if allow_none:
            self.addItem("— không dùng —")
            self._names.append(None)
        for p in providers:
            label = p["display_name"]
            if p.get("cost_note"):
                label += f"  —  {p['cost_note']}"
            if not p.get("available", True):
                label += "   (cần API key)"
            self.addItem(label)
            self._names.append(p["name"])
            if not p.get("available", True):
                item = self.model().item(self.count() - 1)
                if isinstance(item, QStandardItem):
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        # default to the first *available* (or none) option
        for i, n in enumerate(self._names):
            it = self.model().item(i)
            if n is not None and (it is None or it.flags() & Qt.ItemIsEnabled):
                self.setCurrentIndex(i)
                break

    def current_name(self) -> str | None:
        idx = self.currentIndex()
        return self._names[idx] if 0 <= idx < len(self._names) else None

    def select(self, name: str | None) -> None:
        """Pre-select an option by provider name (if present and enabled)."""
        if name in self._names:
            i = self._names.index(name)
            item = self.model().item(i)
            if item is None or (item.flags() & Qt.ItemIsEnabled):
                self.setCurrentIndex(i)

    def update_availability(self, providers: list[dict]) -> None:
        """Re-enable/disable items from a fresh list_providers() result (e.g. after
        the user adds an API key in Settings) without rebuilding the widget."""
        avail = {p["name"]: p.get("available", True) for p in providers}
        for i, name in enumerate(self._names):
            if name is None:
                continue
            item = self.model().item(i)
            if item is None:
                continue
            flags = item.flags()
            if avail.get(name, False):
                item.setFlags(flags | Qt.ItemIsEnabled)
            else:
                item.setFlags(flags & ~Qt.ItemIsEnabled)
