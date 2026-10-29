"""«Cắt Short» — pick (or edit) one 30–60 s segment of an approved episode and its hook title.

Proposals come from ``app.pipeline.clipshort`` (a free heuristic at once; "Gợi ý bằng AI" adds one
cheap call, on request only). The range is free text (mm:ss or seconds): whatever is typed is made
to hold whole lines when the project is created, and the dialog shows the first / last line it will
hold. Nothing is created here — ``spec()`` is read by the library, which runs ``clipshort.make``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem

from app.pipeline import clipshort
from desktop.theme import V2
from desktop.widgets.chrome import button, label
from desktop.widgets.dialogs import VzDialog

ROLE = Qt.UserRole


def fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


def parse(text: str) -> float | None:
    """"1:23.5" / "83.5" / "83,5" → seconds; None when it is neither. Pure."""
    t = (text or "").strip().replace(",", ".")
    if not t:
        return None
    try:
        if ":" in t:
            parts = [float(p) for p in t.split(":")]
            if len(parts) > 3 or any(p < 0 for p in parts):
                return None
            sec = 0.0
            for p in parts:
                sec = sec * 60 + p
            return sec
        v = float(t)
        return v if v >= 0 else None
    except ValueError:
        return None


class ShortDialog(VzDialog):
    aiRequested = Signal()

    def __init__(self, parent, title: str, cues, duration: float,
                 candidates: list[clipshort.Candidate], *, ai_hint: str = "") -> None:
        super().__init__("Cắt Short", parent, width=620)
        self._cues = list(cues)
        self._duration = float(duration or 0)
        self.body.addWidget(label(
            f"Từ tập đã duyệt: {title}\nChọn một đoạn 30–60 giây có câu mở đầu hút người xem và kết ở "
            "chỗ lửng. Short mới là một dự án riêng — bạn vẫn phải duyệt lại phụ đề của nó.",
            "secondary", wrap=True))
        self.list = QListWidget()
        self.list.setMinimumHeight(120)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setStyleSheet("QListWidget::item{padding:5px 6px;}")
        self.list.currentRowChanged.connect(self._on_pick)
        self.body.addWidget(self.list)
        self.empty = label("Chưa có gợi ý tự động (tập ngắn hoặc ít thoại) — nhập khoảng thời gian "
                           "bên dưới.", "small", wrap=True)
        self.body.addWidget(self.empty)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("Từ", "secondary"))
        self.start = QLineEdit()
        self.start.setFixedWidth(84)
        self.start.setPlaceholderText("0:00.0")
        row.addWidget(self.start)
        row.addWidget(label("đến", "secondary"))
        self.end = QLineEdit()
        self.end.setFixedWidth(84)
        self.end.setPlaceholderText("0:45.0")
        row.addWidget(self.end)
        self.length = label("", "small")
        row.addWidget(self.length)
        row.addStretch(1)
        self.ai_btn = button("Gợi ý bằng AI", "Sparkles", variant="ghost", small=True,
                             tip=ai_hint or "Một lệnh gọi AI rẻ (vai trò “rút gọn”, vài phần nghìn đô) "
                                            "đọc lời thoại đã duyệt và chọn đoạn hay nhất.")
        self.ai_btn.clicked.connect(self._ask_ai)
        row.addWidget(self.ai_btn)
        self.body.addLayout(row)
        self.span = label("", "small", wrap=True)
        self.body.addWidget(self.span)
        self.body.addWidget(label("Tiêu đề hiện 3 giây đầu (≤ 60 ký tự)", "secondary"))
        self.hook = QLineEdit()
        self.hook.setMaxLength(clipshort.HOOK_MAX)
        self.hook.setPlaceholderText("Câu mở đầu hút người xem")
        self.body.addWidget(self.hook)
        self.note = label("", "small", wrap=True)
        self.body.addWidget(self.note)
        self.add_button("Hủy")
        self.ok_btn = self.add_button("Tạo Short", icon_name="Scissors", primary=True, accept=True)
        self.start.textChanged.connect(self._refresh)
        self.end.textChanged.connect(self._refresh)
        self.set_candidates(candidates)

    # --- data in -----------------------------------------------------------------------------
    def set_candidates(self, cands: list[clipshort.Candidate]) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for c in cands:
            it = QListWidgetItem(f"{fmt(c.start)} – {fmt(c.end)}  ·  {c.length:.0f} s  ·  {c.hook}")
            it.setData(ROLE, c)
            it.setToolTip(c.reason)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.empty.setVisible(not cands)
        if cands:
            self.list.setCurrentRow(0)
            self._on_pick(0)
        else:
            self._refresh()

    def set_busy(self, on: bool, text: str = "") -> None:
        self.ai_btn.setEnabled(not on)
        self.ai_btn.setText("Đang hỏi AI…" if on else "Gợi ý bằng AI")
        self.set_note(text)

    def set_note(self, text: str, *, warn: bool = False) -> None:
        self.note.setText(text)
        self.note.setStyleSheet(f"color:{V2['amber'] if warn else V2['muted']};")

    # --- reading -----------------------------------------------------------------------------
    def spec(self) -> dict | None:
        """{"start", "end", "hook"} of the range as typed, or None when it is not usable."""
        a, b = parse(self.start.text()), parse(self.end.text())
        if a is None or b is None or b <= a:
            return None
        return {"start": a, "end": b, "hook": self.hook.text().strip()}

    def _on_pick(self, row: int) -> None:
        it = self.list.item(row) if row >= 0 else None
        c = it.data(ROLE) if it else None
        if c is None:
            return
        self.start.setText(fmt(c.start))
        self.end.setText(fmt(c.end))
        self.hook.setText(c.hook)

    def _ask_ai(self) -> None:
        self.aiRequested.emit()

    def _refresh(self, *_a) -> None:
        spec = self.spec()
        ok, msg, warn = False, "", False
        self.span.setText("")
        if spec is None:
            msg, warn = "Nhập thời gian bắt đầu và kết thúc (mm:ss hoặc số giây).", True
        else:
            fit = clipshort.fit_range(self._cues, spec["start"], spec["end"])
            if fit is None:
                msg, warn = "Khoảng này không có câu thoại nào.", True
            else:
                length = fit[1] - fit[0]
                ok = clipshort.RANGE_MIN <= length <= clipshort.RANGE_MAX
                inside = [c for c in self._cues
                          if spec["start"] <= (c.start + c.end) / 2 < spec["end"] and c.text.strip()]
                first, last = " ".join(inside[0].text.split()), " ".join(inside[-1].text.split())
                self.span.setText(f"{len(inside)} câu · mở bằng “{first[:50]}” · kết bằng “{last[:50]}”")
                msg = f"≈ {length:.0f} giây"
                if length < clipshort.SHORT_MIN:
                    msg += " — ngắn hơn 30 giây"
                elif length > clipshort.SHORT_MAX:
                    msg += " — dài hơn 60 giây (YouTube Shorts / Reels tối đa 3 phút)"
                warn = not (clipshort.SHORT_MIN <= length <= clipshort.SHORT_MAX) or not ok
                if not ok:
                    msg = f"Đoạn Short phải dài {clipshort.RANGE_MIN:.0f}–{clipshort.RANGE_MAX:.0f} giây."
        self.length.setText(msg)
        self.length.setStyleSheet(f"color:{V2['amber'] if warn else V2['muted']};")
        self.ok_btn.setEnabled(ok)
