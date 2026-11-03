"""Editor actions on SEVERAL lines at once (2026-11-02): Ctrl+A / Shift+click / Ctrl+click in the
list and on the timeline, Ctrl+C / Ctrl+V of the Vietnamese text, "Dịch lại" for all of them,
"Nghe lại" (the original sound of the line) and "AI nghe lại" (a listening model writes down what
is said, so a line the OCR got wrong or missed is checked by ear, not by the OCR).

A mixin of EditorView: it uses its ``cues``, ``cue_list``, ``timeline``, ``video``, ``job`` and
``_snapshot / _reload / _run_async / _apply_bulk`` — nothing here owns state beyond two flags.
The text inputs keep their own Ctrl+C / V / A (Qt lets them claim the standard keys first)."""
from __future__ import annotations

import json

from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu

MIME = "application/x-vizsup-lines"     # [{vi, zh}] — an exact paste inside the app
LISTEN_MAX_S = 120.0                    # "Nghe lại" plays at most this much
AI_LISTEN_MAX = 40                      # lines per "AI nghe lại" (backend listen.MAX_LINES)


def plain_lines(text: str) -> list[str]:
    """The lines of pasted text (blank ones dropped, a line's own indent trimmed). Pure."""
    return [ln.strip() for ln in (text or "").replace("\r", "").split("\n") if ln.strip()]


def paste_plan(texts: list[str], selected: list[int], n_cues: int) -> list[tuple[int, str]]:
    """Which row gets which text. One text → every selected row; several texts → in order from
    the first selected row, but never more rows than are selected when more than one is (so
    Ctrl+V over 3 rows with 10 lines fills those 3), and a single selected row takes them on
    down the list like a spreadsheet. Pure."""
    if not texts or not selected:
        return []
    rows = sorted(selected)
    if len(texts) == 1:
        return [(r, texts[0]) for r in rows]
    if len(rows) > 1:
        return list(zip(rows, texts))
    return [(rows[0] + k, t) for k, t in enumerate(texts) if rows[0] + k < n_cues]


class SelectionActions:
    _listen_until: float | None = None
    _listen_from = 0.0

    # --- which lines ----------------------------------------------------------------
    def _sel_rows(self) -> list[int]:
        """The selected rows (list and timeline are kept in step); the current line when
        nothing is multi-selected."""
        n = len(self.cues)
        rows = [r for r in self.cue_list.selected_rows() if 0 <= r < n]
        if not rows and 0 <= self._sel < n:
            rows = [self._sel]
        return rows

    def _reload_keep(self, rows: list[int]) -> None:
        """Reload the editor after a group edit that leaves the line count alone and keep the
        same lines selected (a plain reload would collapse the selection to one line)."""
        cur = self._sel
        self._reload()
        self._syncing_sel = True
        try:
            self.cue_list.set_selected_rows(rows, cur if cur in rows else rows[0])
        finally:
            self._syncing_sel = False
        self.timeline.set_multi(rows)

    def _sync_list_to_timeline(self) -> None:
        if not getattr(self, "_syncing_sel", False):
            self.timeline.set_multi(self.cue_list.selected_rows())

    def _on_timeline_multi(self, rows) -> None:
        """Ctrl / Shift + click on a block: the list follows (without bouncing back)."""
        self._syncing_sel = True
        try:
            self.cue_list.set_selected_rows(rows, self.timeline.current())
        finally:
            self._syncing_sel = False
        self._update_scope_hint()

    def _select_all_cues(self) -> None:
        rows = self.cue_list.visible_rows() if hasattr(self.cue_list, "visible_rows") else \
            list(range(len(self.cues)))
        if not rows:
            return
        self._syncing_sel = True
        try:
            self.cue_list.set_selected_rows(rows, self._sel if self._sel in rows else rows[0])
        finally:
            self._syncing_sel = False
        self.timeline.set_multi(rows)
        self._update_scope_hint()
        self.message.emit(f"Đã chọn {len(rows)} câu — chuột phải để sửa cả nhóm.", "info")

    # --- Ctrl+C / Ctrl+V --------------------------------------------------------------
    def _clip_copy(self, *, zh: bool = False) -> None:
        rows = self._sel_rows()
        if not rows:
            return
        md = QMimeData()
        md.setText("\n".join((self.cues[r].zh if zh else self.cues[r].vi).replace("\n", " ")
                             for r in rows))
        if not zh:
            md.setData(MIME, json.dumps([{"vi": self.cues[r].vi, "zh": self.cues[r].zh}
                                         for r in rows], ensure_ascii=False).encode("utf-8"))
        QApplication.clipboard().setMimeData(md)
        self.message.emit(f"Đã chép {'chữ Trung của ' if zh else ''}{len(rows)} câu.", "ok")

    def _clip_paste(self) -> None:
        rows = self._sel_rows()
        md = QApplication.clipboard().mimeData()
        if not rows or md is None:
            return
        if md.hasFormat(MIME):
            try:
                texts = [str(x.get("vi", "")) for x in json.loads(bytes(md.data(MIME)).decode("utf-8"))]
            except (ValueError, AttributeError):
                texts = plain_lines(md.text())
        else:
            texts = plain_lines(md.text())
        plan = [(r, t) for r, t in paste_plan(texts, rows, len(self.cues)) if self.cues[r].vi != t]
        if not plan:
            return
        self._snapshot()
        for r, t in plan:
            self.cues[r].vi = t
        self._reload_keep(rows)
        self.message.emit(f"Đã dán vào {len(plan)} câu — Ctrl+Z để hoàn tác.", "ok")

    # --- Dịch lại cho cả nhóm ------------------------------------------------------------
    def _retranslate_rows(self) -> None:
        rows = self._sel_rows()
        if len(rows) <= 1:
            self._retranslate()                 # one line: the 3-option dialog
            return
        if self._busy_bulk:
            return
        request, ok = QInputDialog.getText(
            self, "Dịch lại các câu đã chọn",
            f"Dịch lại {len(rows)} câu bằng AI.\nYêu cầu thêm (có thể để trống), ví dụ “xưng tôi–cô”:")
        if not ok:
            return
        from app.pipeline import brief as brief_mod

        brief = (brief_mod.load_brief(self.job) or "") if self.job is not None else ""
        provider = self._ctx.get("refine") or self._ctx.get("translator") or "openrouter"
        items = [self._retranslate_item(r) for r in rows]
        snap = [self.cues[r].vi for r in rows]
        self._busy_bulk = True
        self.message.emit(f"AI đang dịch lại {len(rows)} câu…", "info")

        def work():
            from app.pipeline.translate import retranslate_lines
            return retranslate_lines(items, brief=brief, request=request, provider=provider)

        def done(result):
            from desktop.widgets.dialogs import BulkEditDialog

            self._busy_bulk = False
            out = [(r, old, new, self.cues[r].zh) for r, old, new in zip(rows, snap, result)
                   if new and new != old and r < len(self.cues) and self.cues[r].vi == old]
            if not out:
                self.message.emit("AI giữ nguyên bản dịch của các câu này.", "info")
                return
            dlg = BulkEditDialog(self, title=f"Dịch lại {len(out)} câu",
                                 intro="Bản AI dịch lại. Bỏ chọn câu bạn muốn giữ, hoặc sửa đề xuất.",
                                 rows=out, checked=True)
            if dlg.exec():
                self._apply_bulk(dlg.chosen(), "Đã dịch lại")

        def fail(msg):
            self._busy_bulk = False
            self._toast(f"Lỗi dịch lại: {msg}")

        self._run_async(work, done, fail)

    def _retranslate_item(self, r: int, zh: str | None = None) -> dict:
        c = self.cues[r]
        return {"zh": c.zh if zh is None else zh, "vi": c.vi,
                "before": self.cues[r - 1].vi if r > 0 else "",
                "after": self.cues[r + 1].vi if r + 1 < len(self.cues) else ""}

    # --- Nghe lại (the original sound) ----------------------------------------------------
    def _listen_rows(self) -> None:
        rows = self._sel_rows()
        if not rows:
            return
        a, b = self.cues[rows[0]].start, self.cues[rows[-1]].end
        b = min(b, a + LISTEN_MAX_S)
        self.video.seek_seconds(max(0.0, a - 0.2))
        self._listen_from, self._listen_until = a - 0.7, b + 0.15
        self.video.play()

    def _listen_tick(self, t: float) -> None:
        """Called on every playback position: stop where the listened line ends."""
        u = self._listen_until
        if u is None:
            return
        if t >= u:
            self._listen_until = None
            self.video.pause()
        elif t < self._listen_from or t > u + 1.0:    # the user moved away: no longer ours
            self._listen_until = None

    # --- AI nghe lại ---------------------------------------------------------------------
    def _ai_listen_rows(self) -> None:
        rows = self._sel_rows()
        if not rows:
            return
        if len(rows) > AI_LISTEN_MAX:
            self.message.emit(f"AI nghe tối đa {AI_LISTEN_MAX} câu một lần — chọn ít hơn.", "warn")
            return
        if self._busy_bulk or self.job is None:
            return
        from app.pipeline import brief as brief_mod

        brief = brief_mod.load_brief(self.job) or ""
        provider = self._ctx.get("refine") or self._ctx.get("translator") or "openrouter"
        spans = [(self.cues[r].start, self.cues[r].end) for r in rows]
        snap = [(self.cues[r].zh, self.cues[r].vi) for r in rows]
        base = [self._retranslate_item(r) for r in rows]      # read on the GUI thread
        job = self.job
        self.video.pause()
        self._busy_bulk = True
        self.message.emit(f"AI đang nghe lại {len(rows)} câu (gửi đoạn âm thanh ngắn lên mạng)…", "info")

        def work():
            from app.pipeline import listen
            from app.pipeline.translate import retranslate_lines

            heard = listen.hear(job, spans)
            todo = [k for k, (h, (zh, _vi)) in enumerate(zip(heard, snap))
                    if h.strip() and not listen.same_text(h, zh)]
            items = [{**base[k], "zh": heard[k]} for k in todo]
            new = retranslate_lines(items, brief=brief, provider=provider) if items else []
            return heard, todo, new

        def done(res):
            from desktop.widgets.dialogs import BulkEditDialog

            self._busy_bulk = False
            heard, todo, new = res
            if not todo:
                self.message.emit(f"AI nghe {len(rows)} câu: giống phụ đề Trung hiện có — không cần sửa.",
                                  "ok")
                return
            out, heard_of = [], {}
            for k, vi_new in zip(todo, new):
                r = rows[k]
                if r < len(self.cues) and self.cues[r].vi == snap[k][1]:
                    heard_of[r] = heard[k]
                    out.append((r, snap[k][1], vi_new,
                                f"AI nghe: {heard[k]}  ·  phụ đề Trung: {snap[k][0]}"))
            if not out:
                return
            dlg = BulkEditDialog(
                self, title=f"AI nghe lại: {len(out)}/{len(rows)} câu khác phụ đề Trung",
                intro="Bản dịch mới dựa trên chữ AI NGHE ĐƯỢC. AI có thể nghe sai (nhạc to, tiếng "
                      "chồng) — nghe lại câu rồi chọn câu bạn đồng ý; chữ Trung của câu đó cũng được "
                      "đổi theo.", rows=out, checked=False)
            if dlg.exec():
                chosen = [(i, t) for i, t in dlg.chosen() if i in heard_of]
                if chosen:
                    self._snapshot()
                    for i, t in chosen:
                        self.cues[i].vi, self.cues[i].zh = t, heard_of[i]
                    self._reload()
                    self.message.emit(f"Đã sửa {len(chosen)} câu theo AI nghe — Ctrl+Z để hoàn tác.", "ok")

        def fail(msg):
            self._busy_bulk = False
            self._toast(f"Lỗi AI nghe: {msg}")

        self._run_async(work, done, fail)

    # --- other group edits ------------------------------------------------------------------
    def _shorten_rows(self) -> None:
        """"Rút gọn" for the chosen lines: the same batched rewrite as «Rút gọn hàng loạt»."""
        rows = [r for r in self._sel_rows() if self.cues[r].vi.strip()]
        if not rows or self._busy_bulk:
            return
        provider = self._ctx.get("translator") or "openrouter"
        texts = [(self.cues[r].vi, 70) for r in rows]
        snap = [self.cues[r].vi for r in rows]
        self._busy_bulk = True
        self.message.emit(f"AI đang rút gọn {len(rows)} câu…", "info")

        def work():
            from app.pipeline.translate import shorten_lines
            return shorten_lines(texts, provider=provider)

        def done(result):
            from desktop.widgets.dialogs import BulkEditDialog

            self._busy_bulk = False
            out = [(r, old, new, self.cues[r].zh) for r, old, new in zip(rows, snap, result)
                   if new and new != old and r < len(self.cues) and self.cues[r].vi == old]
            if not out:
                self.message.emit("AI không rút gọn thêm được câu nào.", "warn")
                return
            dlg = BulkEditDialog(self, title=f"Rút gọn {len(out)} câu",
                                 intro="Bỏ chọn câu bạn muốn giữ, hoặc sửa đề xuất.", rows=out, checked=True)
            if dlg.exec():
                self._apply_bulk(dlg.chosen(), "Đã rút gọn")

        def fail(msg):
            self._busy_bulk = False
            self._toast(f"Lỗi rút gọn: {msg}")

        self._run_async(work, done, fail)

    def _snap_rows(self) -> None:
        """"Tự canh" for the chosen lines (one undo step)."""
        rows = [r for r in self._sel_rows() if not self.cues[r].locked]
        if not rows or not self._speech:
            self.message.emit("Chưa có dữ liệu giọng nói để canh — đợi vài giây rồi thử lại.", "warn")
            return
        from app.pipeline import snap

        self._snapshot()
        rep = snap.snap_cues(self.cues, self._speech, duration=self._duration(), only=set(rows))
        if not rep.changed:
            self._undo_stack.pop()
            self._update_undo_buttons()
            self.message.emit("Các câu đã chọn đã khớp giọng nói.", "info")
            return
        self._reload_keep(rows)
        self.message.emit(f"Đã canh {len(rep.changed)} câu theo giọng nói — Ctrl+Z để hoàn tác.", "ok")

    def _lock_rows(self, on: bool) -> None:
        rows = self._sel_rows()
        if not rows:
            return
        self._snapshot()
        for r in rows:
            self.cues[r].locked = on
        self._reload_keep(rows)

    def _review_rows(self) -> None:
        from desktop import cuemodel

        rows = self._sel_rows()
        if not rows:
            return
        self._snapshot()
        for r in rows:
            cuemodel.mark_reviewed(self.cues[r])
        self._reload_keep(rows)
        self.message.emit(f"Đã đánh dấu “Đã xem” {len(rows)} câu.", "ok")

    def _delete_rows(self) -> None:
        rows = self._sel_rows()
        if not rows:
            return
        self._snapshot()
        for r in reversed(rows):
            self.cues.pop(r)
        self._reload()
        if self.cues:
            self.cue_list.selectRow(min(rows[0], len(self.cues) - 1))
        self.message.emit(f"Đã xóa {len(rows)} câu — Ctrl+Z để hoàn tác.", "ok")

    def _merge_rows(self) -> None:
        """Join the chosen lines into one (they must be neighbours)."""
        from desktop import cuemodel

        rows = self._sel_rows()
        if len(rows) < 2 or rows != list(range(rows[0], rows[-1] + 1)):
            self.message.emit("Chỉ gộp được các câu nằm liền nhau.", "warn")
            return
        self._snapshot()
        merged = self.cues[rows[0]]
        for r in rows[1:]:
            merged = cuemodel.merge_cues(merged, self.cues[r])
        self.cues[rows[0]:rows[-1] + 1] = [merged]
        self._reload()
        self.cue_list.selectRow(rows[0], center=False)

    # --- the menu for a group ---------------------------------------------------------------
    def _group_menu(self, rows: list[int]) -> QMenu:
        n = len(rows)
        m = QMenu(self)
        m.addAction(f"Dịch lại {n} câu (AI)	Alt+R", self._retranslate_rows)
        m.addAction(f"Rút gọn {n} câu (AI)", self._shorten_rows)
        m.addAction(f"AI nghe lại {n} câu (đối chiếu âm thanh)	Shift+L", self._ai_listen_rows)
        m.addAction("Nghe lại các câu này (tiếng gốc)	L", self._listen_rows)
        m.addSeparator()
        m.addAction(f"Chép {n} câu	Ctrl+C", self._clip_copy)
        m.addAction("Chép chữ Trung", lambda: self._clip_copy(zh=True))
        m.addAction("Dán	Ctrl+V", self._clip_paste)
        m.addSeparator()
        m.addAction(f"Tự canh {n} câu theo giọng nói", self._snap_rows)
        m.addAction("Khóa timing", lambda: self._lock_rows(True))
        m.addAction("Mở khóa timing", lambda: self._lock_rows(False))
        m.addAction("Đánh dấu “Đã xem”", self._review_rows)
        merge = m.addAction("Gộp thành một câu", self._merge_rows)
        merge.setEnabled(rows == list(range(rows[0], rows[-1] + 1)))
        m.addSeparator()
        m.addAction(f"Xóa {n} câu (Ctrl+Z hoàn tác)", self._delete_rows)
        return m


__all__ = ["SelectionActions", "paste_plan", "plain_lines"]
