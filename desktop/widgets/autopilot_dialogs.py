"""Autopilot dialogs (docs/plan/J-autopilot.md):

- ``FastApproveDialog`` — "Duyệt nhanh": an episode's flagged lines only (or "nothing flagged"),
  what happens after approval (render, Short, posting slots), and ONE button that approves.
  The gate stays the owner's click.
- ``AutopilotDialog`` — "Tự động đăng": the per-series plan (platforms × long/Short, daily
  times, posts per day, automatic Short, the Vietnamese series name in titles) and the post
  queue with each post's status / link.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHeaderView,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from desktop.widgets.chrome import label
from desktop.widgets.dialogs import VzDialog

PLATFORM_NAMES = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok",
                  "folder": "Gói đăng (thư mục)"}
STATUS_NAMES = {"pending": "chờ đến giờ", "done": "đã đăng", "scheduled": "đã hẹn giờ",
                "private": "đã tải lên (riêng tư)", "draft": "gói đăng sẵn", "failed": "lỗi"}


def _mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


class FastApproveDialog(VzDialog):
    """``after``: one line saying what the app does once approved (from autopilot.summary)."""

    def __init__(self, parent, *, title: str, cues: int, flagged: list[dict], after: str) -> None:
        super().__init__("Duyệt nhanh", parent, width=760)
        self.edit_requested = False
        self.body.addWidget(label(title, "h3"))
        if flagged:
            self.body.addWidget(label(f"{cues} câu · {len(flagged)} câu được đánh dấu cần xem — "
                                      "đọc nhanh các câu dưới đây; cần sửa thì mở editor.",
                                      "secondary", wrap=True))
            t = QTableWidget(len(flagged), 4)
            t.setHorizontalHeaderLabels(["Lúc", "Tiếng Trung", "Tiếng Việt", "Cần xem vì"])
            t.verticalHeader().setVisible(False)
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.setWordWrap(True)
            for r, row in enumerate(flagged):
                for c, text in enumerate((_mmss(row["start"]), row["zh"], row["vi"],
                                          ", ".join(row["flags"]))):
                    t.setItem(r, c, QTableWidgetItem(text))
            h = t.horizontalHeader()
            h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            for c in (1, 2, 3):
                h.setSectionResizeMode(c, QHeaderView.Stretch)
            t.resizeRowsToContents()
            t.setMinimumHeight(min(360, 40 + 34 * len(flagged)))
            self.table = t
            self.body.addWidget(t)
        else:
            self.table = None
            self.body.addWidget(label(f"{cues} câu · không có câu nào bị đánh dấu.", "secondary"))
        self.body.addWidget(label(after, "small", wrap=True))
        edit = self.add_button("Sửa trong editor", icon_name="Pencil")
        edit.clicked.connect(self._edit)
        self.approve_btn = self.add_button("Duyệt", icon_name="ShieldCheck", primary=True,
                                           accept=True)

    def _edit(self) -> None:
        self.edit_requested = True


class AutopilotDialog(VzDialog):
    """Edit one series' plan (picked in the combo); shows the post queue. ``save()`` on accept."""

    def __init__(self, parent, *, slug: str | None = None) -> None:
        from app.pipeline import series

        super().__init__("Tự động đăng", parent, width=720)
        self.body.addWidget(label(
            "Sau khi bạn duyệt một tập, app tự dựng (theo cài đặt đã dựng của bộ phim), tự cắt "
            "Short, rồi đăng vào các khung giờ dưới đây. Nền tảng chưa kết nối tài khoản (Cài "
            "đặt → Tài khoản đăng video) sẽ được xuất thành gói đăng để bạn tự đăng.",
            "secondary", wrap=True))
        self.series = QComboBox()
        for p in series.list_profiles():
            self.series.addItem(p.name, p.slug)
        if slug:
            self.series.setCurrentIndex(max(0, self.series.findData(slug)))
        self.body.addWidget(self.series)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        self.on = QCheckBox("Bật tự động cho bộ phim này")
        g.addWidget(self.on, 0, 0, 1, 4)
        g.addWidget(label("Tên phim trong tiêu đề", "secondary"), 1, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("vd. Xe Buýt Mạt Thế (để trống = tên gốc)")
        g.addWidget(self.name, 1, 1, 1, 3)
        g.addWidget(label("Giờ đăng", "secondary"), 2, 0)
        self.times = QLineEdit()
        self.times.setPlaceholderText("vd. 12:00, 19:00")
        g.addWidget(self.times, 2, 1)
        g.addWidget(label("Mỗi ngày", "secondary"), 2, 2)
        self.per_day = QSpinBox()
        self.per_day.setRange(1, 6)
        g.addWidget(self.per_day, 2, 3)
        self.auto_short = QCheckBox("Tự cắt 1 Short (30–60 giây) từ mỗi tập đã duyệt")
        g.addWidget(self.auto_short, 3, 0, 1, 4)
        self.private = QCheckBox("Đăng thử: video để RIÊNG TƯ (YouTube riêng tư, TikTok chỉ mình "
                                 "tôi, Facebook xuất gói) — tắt khi đã ổn")
        g.addWidget(self.private, 4, 0, 1, 4)
        g.addWidget(label("Nơi đăng", "h3"), 5, 0, 1, 4)
        from app.providers.registry import PUBLISHERS

        self.rows: dict[str, tuple[QCheckBox, QCheckBox, QCheckBox]] = {}
        for r, (name, pub) in enumerate(PUBLISHERS.items(), start=6):
            on = QCheckBox(PLATFORM_NAMES.get(name, pub.display_name))
            lg, sh = QCheckBox("video dài"), QCheckBox("Short")
            state = pub.account_label() if pub.connected() else "chưa kết nối"
            g.addWidget(on, r, 0)
            g.addWidget(lg, r, 1)
            g.addWidget(sh, r, 2)
            g.addWidget(label(state, "small"), r, 3)
            self.rows[name] = (on, lg, sh)
        self.body.addLayout(g)
        self.body.addWidget(label("Hàng đăng", "h3"))
        self.queue = QTableWidget(0, 5)
        self.queue.setHorizontalHeaderLabels(["Giờ", "Nơi đăng", "Loại", "Video", "Trạng thái"])
        self.queue.verticalHeader().setVisible(False)
        self.queue.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.queue.setMinimumHeight(160)
        self.body.addWidget(self.queue)
        self.series.currentIndexChanged.connect(self._load)
        self.add_button("Đóng")
        self.save_btn = self.add_button("Lưu", icon_name="Check", primary=True, accept=True)
        self._load()

    def slug(self) -> str | None:
        return self.series.currentData()

    def _load(self, *_a) -> None:
        from app.pipeline import autopilot

        slug = self.slug()
        plan = autopilot.load_plan(slug or "")
        self.on.setChecked(bool(plan.get("on")))
        self.name.setText(plan.get("series_name") or "")
        self.times.setText(", ".join(plan.get("times") or []))
        self.per_day.setValue(int(plan.get("per_day") or 1))
        self.auto_short.setChecked(bool(plan.get("auto_short")))
        self.private.setChecked(bool(plan.get("private")))
        for name, (on, lg, sh) in self.rows.items():
            cfg = (plan.get("platforms") or {}).get(name) or {}
            kinds = cfg.get("kinds") or ["long", "short"]
            on.setChecked(bool(cfg.get("on")))
            lg.setChecked("long" in kinds)
            sh.setChecked("short" in kinds)
        self._fill_queue(slug)

    def _fill_queue(self, slug: str | None) -> None:
        from app.pipeline import autopilot

        posts = sorted((p for p in autopilot.load_posts() if p.get("series") == slug),
                       key=lambda p: p["slot"], reverse=True)[:50]
        self.queue.setRowCount(len(posts))
        for r, p in enumerate(posts):
            res = p.get("result") or {}
            cells = (time.strftime("%d/%m %H:%M", time.localtime(p["slot"])),
                     PLATFORM_NAMES.get(p["platform"], p["platform"]),
                     "Short" if p["kind"] == "short" else "dài", p["job"],
                     STATUS_NAMES.get(p.get("status"), p.get("status", "")) +
                     (f" · {res.get('message')}" if res.get("message") else ""))
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c == 4 and res.get("url"):
                    it.setToolTip(res["url"])
                it.setTextAlignment(int(Qt.AlignVCenter | Qt.AlignLeft))
                self.queue.setItem(r, c, it)

    def plan(self) -> dict:
        from app.pipeline import autopilot

        times = [t.strip() for t in self.times.text().replace(";", ",").split(",") if t.strip()]
        platforms = {}
        for name, (on, lg, sh) in self.rows.items():
            kinds = [k for k, box in (("long", lg), ("short", sh)) if box.isChecked()]
            platforms[name] = {"on": on.isChecked(), "kinds": kinds}
        return {**autopilot.load_plan(self.slug() or ""), "on": self.on.isChecked(),
                "series_name": self.name.text().strip(), "times": times or ["19:00"],
                "per_day": self.per_day.value(), "auto_short": self.auto_short.isChecked(),
                "private": self.private.isChecked(), "platforms": platforms}

    def save(self) -> None:
        from app.pipeline import autopilot

        if self.slug():
            autopilot.save_plan(self.slug(), self.plan())
