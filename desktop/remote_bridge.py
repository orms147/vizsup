"""The open app's side of "Điều khiển qua Telegram" (app/pipeline/remote.py).

- **Polling**: every few seconds, when a bot token is saved and no other process (the
  scheduled task) is reading the bot, one long ``getUpdates`` (25 s) runs on a daemon thread
  (``MainWindow._bg``); the updates are handled back on the GUI thread, where the widgets
  live: links go to the gate queue like a pasted link, a [✅ Duyệt] press goes through the
  controller's revision guard (``before_approve`` saves the open editor first), then the
  same after-approve step as «Duyệt nhanh». Stops on close (``win._closing``) and releases
  the poll mark so the scheduled task can take over at once.
- **Sending** never blocks the GUI thread: everything goes through ``remote.Outbox`` (one
  daemon thread, in order).
- **Notifications** the window calls: ``stage`` / ``run_failed`` (a link the owner sent),
  ``gate`` (an episode reached the gate), ``planned`` (posts queued), ``posted`` (a publisher's
  result), ``render_failed`` (an autopilot render).
"""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer

from desktop import paths  # noqa: F401
from desktop import store

from app.pipeline import remote  # noqa: E402

POLL_EVERY_MS = 5000
LONG_POLL_S = 25


class RemoteBridge(remote.Host):
    def __init__(self, win, *, start: bool = True) -> None:
        self.win = win
        self._polling = False
        self._pause_until = 0.0
        self.outbox = remote.Outbox(log=store.debug)
        self.timer = QTimer(win)
        self.timer.timeout.connect(self.poll)
        if start:
            self.timer.start(POLL_EVERY_MS)

    # --- polling ------------------------------------------------------------------------------
    def _alive(self) -> bool:
        return not getattr(self.win, "_closing", False)

    def poll(self) -> None:
        from app.remote import telegram

        if self._polling or not self._alive() or time.time() < self._pause_until:
            return
        if not telegram.configured() or not remote.take_poll("app"):
            return
        self._polling = True
        ch = telegram.TelegramChannel()
        self.win._bg.run(lambda: ch.poll(LONG_POLL_S), self._got, self._failed)

    def _got(self, updates) -> None:
        self._polling = False
        if not self._alive():
            return
        from app.remote import telegram

        was = telegram.paired()
        outs = remote.process(list(updates or []), self, telegram.TelegramChannel(),
                              log=store.debug)
        self.outbox.put(outs)
        if telegram.paired() and not was:
            self.win._notify("Telegram", "Đã ghép bot Telegram — gửi link từ điện thoại được rồi.")
            self.win.settings_view.refresh_telegram()
        if updates:
            QTimer.singleShot(0, self.poll)            # more may be waiting

    def _failed(self, msg: str) -> None:
        from app.remote.telegram import scrub

        self._polling = False
        store.debug(f"telegram poll: {scrub(msg)[:200]}")
        # 409: another reader (the scheduled task mid-run); 401: token revoked; offline …
        if "Unauthorized" in msg or "401" in msg:
            wait = 600                            # until the owner pastes a new token
        elif "Conflict" in msg or "409" in msg:
            wait = 60
        else:
            wait = 30
        self._pause_until = time.time() + wait

    def send(self, outs) -> None:
        from app.remote import telegram

        if outs and telegram.paired():
            self.outbox.put(outs)

    def shutdown(self) -> None:
        try:
            self.timer.stop()
        except RuntimeError:
            pass
        self.outbox.close()
        try:
            remote.release_poll()
        except OSError:
            pass

    # --- remote.Host: what the owner's messages do here ----------------------------------------
    def queue_link(self, url: str, job_id: str | None) -> str:
        w = self.win
        base = {k: v for k, v in w.input._collect_opts().items()
                if k not in ("context_note", "srt", "file", "url")}
        if base.get("subtitle_source") == "srt":
            base["subtitle_source"] = "auto"
        item = {**base, "url": url, "file": None, "series": "auto", "_remote": "telegram"}
        if job_id:
            item["_job_id"] = job_id            # an unfinished project of this link: resume it
        ahead = len(w._queue) + (1 if w._busy() else 0)
        w._start_queue([item])
        w._notify("Telegram", "Nhận link từ điện thoại — đang tải và dịch.")
        where = f" (trước nó còn {ahead} việc)" if ahead else ""
        return (f"📥 Đã nhận link — đang tải và dịch trên máy{where}. Xong sẽ gửi phụ đề để bạn "
                f"duyệt.")

    def link_busy(self, url: str) -> str | None:
        w = self.win
        running = [w._queue_opts] if w._queue_opts else []
        if w._gate_job is not None:
            running.append(w.opts)              # pasted in the app, running in the foreground
        if any((o or {}).get("url") == url for o in [*running, *w._queue]):
            return "Link này đang được xử lý trên máy — xong sẽ gửi để bạn duyệt."
        return None

    def before_approve(self, job) -> str | None:
        w = self.win

        def same(j) -> bool:
            return j is not None and j.id == job.id

        why = None
        if same(w._queue_job) and w._queue_opts is not None:   # still running (not at the gate)
            why = "đang xử lý trong hàng đợi"
        elif same(w._gate_job):
            why = "đang xử lý"
        elif same(w._batch_job):
            why = "đang dựng"
        if why:
            return f"Tập này {why} trên máy — đợi xong rồi duyệt."
        if same(w.job):
            try:
                w.editor.save_now()             # the open editor's text is what would be approved
            except Exception as exc:  # noqa: BLE001
                return f"Không lưu được bản đang mở trên máy ({type(exc).__name__}) — chưa duyệt."
        return None

    def after_approve(self, job) -> str:
        from app.pipeline import autopilot

        w = self.win
        if w.job is not None and w.job.id == job.id:
            from desktop.main import RENDER

            w.editor.refresh_approval()
            if w.stack.currentIndex() == RENDER:
                w._sync_render_page()               # its "chưa duyệt" lock lifts now
        w.input.refresh_recent()
        title = remote.title_of(job)
        w.status.set_status("Đã duyệt từ Telegram — " + title[:40], "ok")
        w._notify("Đã duyệt từ Telegram", title)
        _p, plan = autopilot.plan_of(job)
        if plan is None:
            return ("Bộ phim này chưa bật Tự động đăng — mở vizsup trên máy để dựng như thường "
                    "(tập đã ở trạng thái đã duyệt).")
        if autopilot.render_opts(job) is None:
            return ("Chưa tự dựng được: máy chưa dựng video nào — dựng tay một tập trên máy "
                    "để app học cài đặt.")
        w._autopilot_after_approve(job)
        return "🎬 Đang dựng trên máy theo cài đặt của bộ phim — xong sẽ lên lịch đăng và báo bạn."

    def renders(self) -> list[str]:
        w = self.win
        out = []
        if w._batch_job is not None:
            out.append((w._batch_job.metadata.get("title") or w._batch_job.id) + " (đang dựng)")
        out += [b.get("title") or b["id"] for b in w._batch]
        return out

    # --- notifications from the window ---------------------------------------------------------
    def stage(self, stage: str, job, opts: dict | None) -> None:
        if job is not None and (opts or {}).get("_remote"):
            self.send(remote.stage_outs(stage, job))

    def run_failed(self, job, opts: dict | None, msg: str) -> None:
        if msg.strip().startswith("Đã hủy"):
            return
        title = (job.metadata.get("title") if job is not None else "") or (opts or {}).get("url") or ""
        self.send(remote.failed_outs(f"Chưa xong {title[:80]}".strip(), msg,
                                     asked=bool((opts or {}).get("_remote"))))

    def gate(self, job, *, intro: str = "") -> None:
        from app.remote import telegram

        if job is None or not telegram.paired() or not telegram.notify_on("gate"):
            return
        try:
            loaded = remote.load_job(job.id)
            self.send(remote.gate_outs(loaded, intro=intro))
        except Exception as exc:  # noqa: BLE001 - a notification never breaks the gate
            store.debug(f"telegram gate message: {type(exc).__name__}: {exc}")

    def planned(self, job, posts: list[dict]) -> None:
        if posts:
            self.send(remote.planned_outs(job, posts))

    def posted(self, post: dict | None) -> None:
        self.send(remote.posted_outs(post))

    def render_failed(self, item: dict, msg: str) -> None:
        if not msg.strip().startswith("Đã hủy"):
            self.send(remote.failed_outs(f"Dựng lỗi {str(item.get('title') or item['id'])[:80]}",
                                         msg))
