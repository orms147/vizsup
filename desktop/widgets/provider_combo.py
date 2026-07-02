"""A QComboBox populated from registry.list_providers() entries.
Shows 'display_name — cost_note'; disables options where available is false, and
(optionally) where a capability flag is false — e.g. the refine picker passes
require="can_refine" so engines whose refine pass is a silent no-op can't be
chosen at all.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import QComboBox

_REQUIRE_NOTE = {"can_refine": "không hỗ trợ tinh chỉnh",
                 "can_proofread": "không hỗ trợ rà soát OCR"}


class ProviderCombo(QComboBox):
    def __init__(self, providers: list[dict], *, allow_none: bool = False,
                 require: str | None = None) -> None:
        super().__init__()
        self._names: list[str | None] = []
        self._require = require
        if allow_none:
            self.addItem("— không dùng —")
            self._names.append(None)
        for p in providers:
            label = p["display_name"]
            if p.get("cost_note"):
                label += f"  —  {p['cost_note']}"
            enabled = self._enabled(p)
            if not p.get("available", True):
                label += f"   ({p.get('unavailable_note') or 'cần API key'})"
            elif not enabled:
                label += f"   ({_REQUIRE_NOTE.get(require, 'không hỗ trợ')})"
            self.addItem(label)
            self._names.append(p["name"])
            if not enabled:
                item = self.model().item(self.count() - 1)
                if isinstance(item, QStandardItem):
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        # default to the first *available* (or none) option
        for i, n in enumerate(self._names):
            it = self.model().item(i)
            if n is not None and (it is None or it.flags() & Qt.ItemIsEnabled):
                self.setCurrentIndex(i)
                break

    def _enabled(self, p: dict) -> bool:
        return bool(p.get("available", True)
                    and (not self._require or p.get(self._require, True)))

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
        enabled = {p["name"]: self._enabled(p) for p in providers}
        for i, name in enumerate(self._names):
            if name is None:
                continue
            item = self.model().item(i)
            if item is None:
                continue
            flags = item.flags()
            if enabled.get(name, False):
                item.setFlags(flags | Qt.ItemIsEnabled)
            else:
                item.setFlags(flags & ~Qt.ItemIsEnabled)
