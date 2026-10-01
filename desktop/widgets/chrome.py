"""App chrome for the v2 design: top app bar (brand + step rail + actions), the
project bar under it, the status bar, and small shared builders (icon buttons,
key caps, badges). Specs: appdesign/v2/design.css (.app-nav, .statusbar, .btn, kbd,
.badge) and qt-handoff.md.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from desktop.icons import icon, pixmap
from desktop.theme import V2, fonts


# --- small builders -------------------------------------------------------------

def button(text: str = "", icon_name: str | None = None, *, variant: str = "",
           tip: str = "", icon_color: str | None = None, checkable: bool = False,
           icon_size: int = 16, small: bool = False) -> QPushButton:
    """A v2 button. ``variant``: "" (secondary) | primary | ghost | danger | small |
    large | nudge | link. ``small`` shrinks any variant (ghost + small). Icon color
    follows the variant unless given."""
    b = QPushButton(text)
    if variant:
        b.setObjectName(variant)
    if small:
        b.setProperty("small", True)
    if icon_name:
        color = icon_color or ("#ffffff" if variant == "primary" else
                               V2["red"] if variant == "danger" else V2["secondary"])
        b.setIcon(icon(icon_name, color, icon_size, active=V2["purple_bright"]))
        b.setIconSize(QSize(icon_size, icon_size))
    if tip:
        b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    return b


def icon_button(icon_name: str, tip: str, *, checkable: bool = False, size: int = 16,
                color: str | None = None) -> QToolButton:
    b = QToolButton()
    b.setObjectName("icon")
    b.setIcon(icon(icon_name, color or V2["secondary"], size, active=V2["purple_bright"]))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    return b


def kbd(text: str) -> QLabel:
    k = QLabel(text)
    k.setObjectName("kbd")
    k.setFixedHeight(18)
    k.setAlignment(Qt.AlignCenter)
    k.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return k


def badge(text: str, kind: str = "") -> QLabel:
    """kind: "" | purple | warn | ok | err"""
    b = QLabel(text)
    b.setObjectName({"purple": "badgePurple", "warn": "badgeWarn", "ok": "badgeOk",
                     "err": "badgeErr"}.get(kind, "badge"))
    b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return b


def label(text: str, role: str = "", *, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setObjectName(role)
    lab.setWordWrap(wrap)
    return lab


def icon_label(icon_name: str, color: str = V2["secondary"], size: int = 16) -> QLabel:
    lab = QLabel()
    lab.setPixmap(pixmap(icon_name, color, size))
    lab.setFixedSize(size, size)
    return lab


def divider(vertical: bool = False) -> QFrame:
    d = QFrame()
    d.setObjectName("vdivider" if vertical else "divider")
    return d


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(*margins)
    h.setSpacing(spacing)
    for w in widgets:
        if w is None:
            h.addStretch(1)
        elif isinstance(w, int):
            h.addSpacing(w)
        elif isinstance(w, QWidget):
            h.addWidget(w)
        else:
            h.addLayout(w)
    return h


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    v = QVBoxLayout()
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    for w in widgets:
        if w is None:
            v.addStretch(1)
        elif isinstance(w, int):
            v.addSpacing(w)
        elif isinstance(w, QWidget):
            v.addWidget(w)
        else:
            v.addLayout(w)
    return v


# --- step rail ------------------------------------------------------------------

STEPS = ["Bắt đầu", "Sửa phụ đề", "Dựng video"]


def _step_badge(num: int, state: str) -> QPixmap:
    """22 px circle with the step number; done steps use a check icon instead."""
    if state == "done":
        return pixmap("Check", V2["green"], 16)
    size = 22
    from PySide6.QtGui import QGuiApplication

    dpr = QGuiApplication.instance().devicePixelRatio() if QGuiApplication.instance() else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    active = state == "active"
    p.setPen(QPen(QColor(V2["purple"] if active else V2["field_line"]), 1.2))
    p.setBrush(QColor(V2["purple_soft"]) if active else Qt.NoBrush)
    p.drawEllipse(QRectF(0.6, 0.6, size - 1.2, size - 1.2))
    f = QFont(fonts()[1])
    f.setPixelSize(10)
    p.setFont(f)
    p.setPen(QColor(V2["purple_bright"] if active else V2["muted"]))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, str(num))
    p.end()
    return pm


class StepRail(QWidget):
    """Bắt đầu › Sửa phụ đề › Dựng video. Drop-in for the v1 FlowSteps API:
    ``clicked(int)`` and ``set_active(idx)``."""

    clicked = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        self._btns: list[QPushButton] = []
        for i, name in enumerate(STEPS):
            b = QPushButton(name)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(22, 22))
            b.clicked.connect(lambda _=False, k=i: self.clicked.emit(k))
            self._btns.append(b)
            h.addWidget(b)
            if i < len(STEPS) - 1:
                h.addWidget(icon_label("ChevronRight", V2["dim"], 14))
        self.set_active(0)

    def set_active(self, idx: int) -> None:
        for i, b in enumerate(self._btns):
            state = "done" if i < idx else "active" if i == idx else "todo"
            b.setObjectName({"done": "stepDone", "active": "stepActive", "todo": "step"}[state])
            b.setIcon(_step_badge(i + 1, state))
            b.style().unpolish(b)
            b.style().polish(b)


# --- app bar --------------------------------------------------------------------

class AppNav(QFrame):
    """Top bar: brand · steps … [saved] [keyboard] [Cài đặt] [primary action]."""

    stepClicked = Signal(int)
    settingsClicked = Signal()
    keysClicked = Signal()
    primaryClicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("appnav")
        self.setFixedHeight(61)
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(14)

        brand_icon = QLabel()
        brand_icon.setFixedSize(30, 30)
        brand_icon.setAlignment(Qt.AlignCenter)
        brand_icon.setPixmap(pixmap("Captions", "#ffffff", 19))
        brand_icon.setStyleSheet(f"background:{V2['purple']};border-radius:8px;")
        brand = label("vizsup", "h2")
        h.addWidget(brand_icon)
        h.addWidget(brand)
        h.addSpacing(6)
        h.addWidget(divider(vertical=True))
        h.addSpacing(6)
        self.steps = StepRail()
        self.steps.clicked.connect(self.stepClicked)
        h.addWidget(self.steps)
        h.addStretch(1)

        self.saved = button("", "CheckCheck", variant="ghost")
        self.saved.setFocusPolicy(Qt.NoFocus)
        self.saved.setCursor(Qt.ArrowCursor)
        self.saved.setStyleSheet("QPushButton#ghost:hover{background:transparent;}")
        h.addWidget(self.saved)
        self.keys_btn = icon_button("Keyboard", "Phím tắt (?)", size=17)
        self.keys_btn.clicked.connect(self.keysClicked)
        h.addWidget(self.keys_btn)
        self.settings_btn = button("Cài đặt", "Settings2", variant="ghost")
        self.settings_btn.clicked.connect(self.settingsClicked)
        h.addWidget(self.settings_btn)
        self.primary = button("", "ShieldCheck", variant="primary")
        self.primary.setMinimumHeight(38)
        self.primary.clicked.connect(self.primaryClicked)
        h.addWidget(self.primary)
        self.set_primary(None)
        self.set_saved(None)

    def set_primary(self, text: str | None, icon_name: str = "ShieldCheck",
                    enabled: bool = True) -> None:
        self.primary.setVisible(bool(text))
        if text:
            self.primary.setText(text)
            self.primary.setIcon(icon(icon_name, "#ffffff", 16))
            self.primary.setEnabled(enabled)

    def set_saved(self, state: str | None) -> None:
        """None (hidden) | "saving" | "saved" | "error" — the editor's autosave."""
        self.keys_btn.setVisible(state is not None)
        if state is None:
            self.saved.setVisible(False)
            return
        self.saved.setVisible(True)
        text, color, ic = {
            "saving": ("Đang lưu…", V2["muted"], "LoaderCircle"),
            "saved": ("Đã lưu", V2["secondary"], "CheckCheck"),
            "error": ("Chưa lưu được", V2["red"], "TriangleAlert"),
        }[state]
        self.saved.setText(text)
        self.saved.setIcon(icon(ic, color, 15))
        self.saved.setStyleSheet(
            f"QPushButton#ghost{{color:{color};}} QPushButton#ghost:hover{{background:transparent;}}")
        self.saved.setToolTip("Phụ đề tự lưu vào thư mục dự án sau mỗi chỉnh sửa")


# --- project bar ----------------------------------------------------------------

class ProjectBar(QFrame):
    """Under the app bar on Sửa phụ đề / Dựng video: file icon · title · meta, then a
    right-hand slot the page fills with its own actions."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(62)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 8, 18, 6)
        h.setSpacing(12)
        ic = QLabel()
        ic.setFixedSize(34, 34)
        ic.setAlignment(Qt.AlignCenter)
        ic.setPixmap(pixmap("FileVideo", V2["purple_bright"], 17))
        ic.setStyleSheet(f"background:{V2['raised']};border:1px solid {V2['line']};"
                         f"border-radius:8px;")
        h.addWidget(ic)
        self.title = label("", "h2")
        self.meta = label("", "mono")
        h.addLayout(vbox(self.title, self.meta, spacing=1))
        h.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        h.addLayout(self.actions)

    def set_project(self, title: str, meta: str) -> None:
        fm = self.title.fontMetrics()
        self.title.setText(fm.elidedText(title, Qt.ElideRight, 620))
        self.title.setToolTip(title)
        self.meta.setText(meta)

    def add_action(self, w: QWidget) -> QWidget:
        self.actions.addWidget(w)
        return w


# --- status bar -----------------------------------------------------------------

class StatusBar(QFrame):
    """Bottom bar: ● status text, then optional key hints (e.g. Space Phát / dừng)."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("statusbar")
        self.setFixedHeight(29)
        self._h = QHBoxLayout(self)
        self._h.setContentsMargins(18, 0, 18, 0)
        self._h.setSpacing(10)
        self.dot = QLabel("●")
        self.text = label("Sẵn sàng", "small")
        self._h.addWidget(self.dot)
        self._h.addWidget(self.text)
        self._hints = QWidget()
        self._hints_lay = QHBoxLayout(self._hints)
        self._hints_lay.setContentsMargins(14, 0, 0, 0)
        self._hints_lay.setSpacing(6)
        self._h.addWidget(self._hints)
        self._h.addStretch(1)
        self.set_status("Sẵn sàng")

    def set_status(self, text: str, kind: str = "ok") -> None:
        color = {"ok": V2["green"], "busy": V2["purple"], "warn": V2["amber"],
                 "err": V2["red"]}.get(kind, V2["muted"])
        self.dot.setStyleSheet(f"color:{color};font-size:9px;")
        self.text.setText(text.lstrip("● "))

    def setText(self, text: str) -> None:  # noqa: N802 - v1 call sites use QLabel API
        t = text.lstrip("● ").strip()
        low = t.lower()
        kind = ("err" if ("lỗi" in low or "error" in low) else
                "busy" if ("đang" in low and "sẵn" not in low) else "ok")
        self.set_status(t, kind)

    def set_hints(self, pairs: list[tuple[str, str]]) -> None:
        """[(keys, description)] e.g. [("Space", "Phát / dừng"), ("[ ]", "Canh lời")]."""
        while self._hints_lay.count():
            it = self._hints_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for keys, desc in pairs:
            for k in keys.split():
                self._hints_lay.addWidget(kbd(k))
            self._hints_lay.addWidget(label(desc, "small"))
            self._hints_lay.addSpacing(10)
