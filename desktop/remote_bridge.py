"""The open app's side of "Điều khiển qua Telegram" (app/pipeline/remote.py).

- **Polling**: every few seconds, when a bot token is saved and no other process (the
  scheduled task) is reading the bot, one long ``getUpdates`` (25 s) runs on a daemon thread
  (``MainWindow._bg``); the updates are handled back on the GUI thread, where the widgets
  live: links go to the gate queue like a pasted link, a [✅ Duyệt] press goes through the
  controller's revision guard (``before_approve`` refuses a project still processed /
  rendered — ``MainWindow._processing`` — and saves the open editor first), then the same
  after-approve step as «Duyệt nhanh». Stops on close (``win._closing``) and releases the
  poll mark so the scheduled task can take over at once.
- **Blocking work** the bot asks for (a 合集 / page listing, a photo becoming a cover):
  ``work`` runs it on a daemon thread (``MainWindow._bg``) after the poll's answers were
  queued, and sends what it returns. [▶️ Dịch N tập mới] queues the page's new videos like
  «Dịch một loạt» does (batch_import.queue_items, the start page's options) — they run to the
  gate one by one, each sending its approval message (no per-step milestones for a batch).
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
    def _base(self) -> dict:
        """The start page's options for a link from the phone — never the open video's note
        or its .srt."""
        base = {k: v for k, v in self.win.input._collect_opts().items()
                if k not in ("context_note", "srt", "file", "url")}
        if base.get("subtitle_source") == "srt":
            base["subtitle_source"] = "auto"
        return base

    def queue_link(self, url: str, job_id: str | None) -> str:
        w = self.win
        item = {**self._base(), "url": url, "file": None, "series": "auto", "_remote": "telegram"}
        if job_id:
            item["_job_id"] = job_id            # an unfinished project of this link: resume it
        ahead = len(w._queue) + (1 if w._busy() else 0)
        w._start_queue([item])
        w._notify("Telegram", "Nhận link từ điện thoại — đang tải và dịch.")
        where = f" (trước nó còn {ahead} việc)" if ahead else ""
        return (f"📥 Đã nhận link — đang tải và dịch trên máy{where}. Xong sẽ gửi phụ đề để bạn "
                f"duyệt.")

    def queue_batch(self, videos: list[dict], series: str) -> str:
        """[▶️ Dịch N tập mới]: the page's new videos to the gate queue, oldest first, with the
        series the bot picked (as «Dịch một loạt» would)."""
        from app.pipeline import batch_import

        w = self.win
        items = [{**it, "_remote": "batch"}
                 for it in batch_import.queue_items(self._base(), videos, series)]
        if not items:
            return "Không có tập nào để dịch."
        ahead = len(w._queue) + (1 if w._busy() else 0)
        w._start_queue(items)
        w._notify("Telegram", f"Nhận {len(items)} tập từ điện thoại — đang tải và dịch lần lượt.")
        where = f" (trước đó còn {ahead} việc)" if ahead else ""
        return (f"📥 Đã xếp {len(items)} tập vào hàng dịch trên máy{where}. Tập nào xong sẽ gửi "
                f"để bạn duyệt — /cho để xem các tập đang chờ.")

    def busy(self, job_id: str) -> str | None:
        return self.win._processing(job_id)

    def work(self, fn):
        """Run ``fn`` (blocking) on a thread once this poll's answers are queued; send its
        messages when it is done."""
        from app.remote.telegram import scrub

        def failed(msg: str) -> None:
            self.send([remote.Out("send", f"⚠ Có lỗi, chưa làm xong: {scrub(msg)[:300]}")])

        def start() -> None:
            if self._alive():
                self.win._bg.run(fn, lambda outs: self.send(list(outs or [])), failed)

        QTimer.singleShot(0, start)
        return []

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
        why = w._processing(job.id)    # processed / rendered / waiting in a queue to be
        if why:
            return f"Tập này {why} trên máy — đợi xong rồi duyệt."
        if w.job is not None and w.job.id == job.id:
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
        from desktop.main import LIBRARY

        if w.stack.currentIndex() == LIBRARY:
            w.library.refresh_waiting()             # «Duyệt hàng loạt (N)»: one fewer waits
        title = remote.title_of(job)
        w.status.set_status("Đã duyệt từ Telegram — " + title[:40], "ok")
        w._notify("Đã duyệt từ Telegram", title)
        _p, plan = autopilot.plan_of(job)
        if plan is None:
            return ("Bộ phim này chưa bật Tự động đăng — mở vizsup trên máy để dựng như thường "
                    "(tập đã ở trạng thái đã duyệt).")
        if autopilot.render_opts(job, remote.render_kind(job)) is None:
            return ("Chưa tự dựng được: máy chưa dựng video nào — dựng tay một tập trên máy "
                    "để app học cài đặt.")
        w._autopilot_after_approve(job)
        return "🎬 Đang dựng trên máy theo cài đặt của bộ phim — xong sẽ lên lịch đăng và báo bạn."

    @staticmethod
    def _name(job_id: str, fallback: str = "") -> str:
        """The bot's name for a project ("Chuyến Xe Tận Thế · Tập 9"), else ``fallback``."""
        from app.config import settings

        d = settings.vizsup_storage_dir / str(job_id or "")
        try:
            return remote.display_title(d) if job_id and (d / "metadata.json").exists() \
                else (fallback or str(job_id or ""))
        except Exception:  # noqa: BLE001 - a label
            return fallback or str(job_id or "")

    def renders(self) -> list[str]:
        w = self.win
        out = []
        if w._batch_job is not None:
            out.append(self._name(w._batch_job.id, w._batch_job.metadata.get("title") or "")
                       + " (đang dựng)")
        out += [self._name(b["id"], b.get("title") or "") for b in w._batch]
        return out

    # --- notifications from the window ---------------------------------------------------------
    def stage(self, stage: str, job, opts: dict | None) -> None:
        # a link the owner sent: its milestones; a batch only sends each approval message
        if job is not None and (opts or {}).get("_remote") == "telegram":
            self.send(remote.stage_outs(stage, job))

    def run_failed(self, job, opts: dict | None, msg: str) -> None:
        if msg.strip().startswith("Đã hủy"):
            return
        raw = (job.metadata.get("title") if job is not None else "") or (opts or {}).get("url") or ""
        title = self._name(job.id, raw) if job is not None else raw
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
            name = self._name(item["id"], str(item.get("title") or item["id"]))
            self.send(remote.failed_outs(f"Dựng lỗi {name[:80]}", msg))
