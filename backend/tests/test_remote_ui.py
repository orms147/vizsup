"""Điều khiển qua Telegram in the open app (desktop/remote_bridge.py) and its Settings card:
a link from the phone becomes a gate-queue item, a stranger is ignored, the [✅ Duyệt] press
saves the open editor first and refuses when that changed the text, the gate / post hooks
send their messages, and the card pairs / toggles / unpairs. The Bot API is faked."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
pytest.importorskip("PySide6.QtWidgets")

from app.config import settings  # noqa: E402
from app.models import Job  # noqa: E402
from app.pipeline import approval, autopilot  # noqa: E402
from app.remote import telegram  # noqa: E402

from telegram_fake import STRANGER, TOKEN, FakeTelegram, pair  # noqa: E402

SHARE = "长按复制此条消息，打开抖音搜索 https://v.douyin.com/iRNBho6u/ 打开抖音"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
    sd = tmp_path / "_series" / "末日校车"
    sd.mkdir(parents=True)
    (sd / "series.json").write_text(json.dumps({"name": "末日校车", "match": "末日校车",
                                                "render": {"voice": "vi-VN-NamMinhNeural",
                                                           "rate": "+30%", "frame": "source"}}),
                                    encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake(storage, monkeypatch):
    f = FakeTelegram()
    monkeypatch.setattr(telegram.TelegramChannel, "transport", f.transport())
    return f


@pytest.fixture
def paired(fake):
    pair(fake)
    fake.sent.clear()
    return fake


def _episode(storage: Path, jid="ep9a1b2c3d4e") -> Job:
    d = storage / jid
    d.mkdir()
    (d / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nXin chào.\n", encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": "第7集 | 《末日校车》09"}), encoding="utf-8")
    (d / "job.json").write_text(json.dumps({"id": jid, "opts": {}}), encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": 9}),
                                        encoding="utf-8")
    return Job(id=jid, work_dir=d)


def _wait(qapp, cond, timeout=8.0) -> bool:
    end = time.time() + timeout
    while not cond() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


@pytest.fixture
def win(qapp, storage):
    from desktop import main as m

    w = m.MainWindow()
    w.remote.timer.stop()                 # the tests drive the polling
    yield w
    w._closing = True
    w.close()


def _poll(win, qapp) -> None:
    win.remote.poll()
    assert _wait(qapp, lambda: not win.remote._polling)
    assert win.remote.outbox.wait()


def _gate_button(win, paired, job) -> str:
    win.remote.gate(job)
    assert win.remote.outbox.wait()
    (msg,) = paired.sent
    paired.sent.clear()
    return paired.buttons(msg)[0]


def test_a_link_from_the_phone_joins_the_gate_queue(win, paired, qapp):
    queued = []
    win._start_queue = lambda items, **kw: queued.extend(items)
    win.input.context_note.setPlainText("ghi chú của video đang mở")
    paired.say(SHARE)
    _poll(win, qapp)
    (item,) = queued
    assert item["url"] == "https://v.douyin.com/iRNBho6u/" and item["series"] == "auto"
    assert item["_remote"] == "telegram" and item["file"] is None and item["translator"]
    assert "context_note" not in item
    assert paired.texts()[-1].startswith("📥 Đã nhận link")
    assert telegram.load()["offset"] == paired.uid + 1


def test_a_stranger_gets_nothing(win, paired, qapp):
    queued = []
    win._start_queue = lambda items, **kw: queued.extend(items)
    paired.say(SHARE, chat=STRANGER)
    paired.say("/trangthai", chat=STRANGER)
    _poll(win, qapp)
    assert queued == [] and paired.sent == []


def test_the_press_approves_after_saving_the_open_editor(win, paired, storage, qapp):
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True,
                                     "platforms": {"folder": {"on": True, "kinds": ["long"]}}})
    job = _episode(storage)
    ok = _gate_button(win, paired, job)
    saved, after = [], []
    win.job = job
    win.editor.save_now = lambda: saved.append(1)            # nothing unsaved
    win.editor.refresh_approval = lambda: None
    win._autopilot_after_approve = lambda j: after.append(j.id)
    paired.press(ok)
    _poll(win, qapp)
    assert saved == [1] and approval.is_approved(job) and after == [job.id]
    assert paired.texts()[-1].startswith("✅ Đã duyệt") and "Đang dựng trên máy" in paired.texts()[-1]


def test_unsaved_edits_in_the_open_editor_block_the_press(win, paired, storage, qapp):
    job = _episode(storage)
    ok = _gate_button(win, paired, job)
    after = []
    win.job = job

    def save_now():                                          # the owner was mid-edit at the PC
        (job.work_dir / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nXin chào bạn.\n",
                                             encoding="utf-8")

    win.editor.save_now = save_now
    win._autopilot_after_approve = lambda j: after.append(j.id)
    paired.press(ok)
    _poll(win, qapp)
    assert not approval.is_approved(job) and after == []
    refusal, fresh = paired.texts()
    assert "CHƯA duyệt" in refusal and fresh.startswith("🟡 Chờ duyệt")   # the saved version
    new_ok = paired.buttons(paired.sent[-1])[0]
    assert new_ok == f"ok:{job.id}:{approval.revision(job)[:8]}" != ok


def test_a_job_still_running_cannot_be_approved(win, paired, storage, qapp):
    job = _episode(storage)
    ok = _gate_button(win, paired, job)
    win._batch_job = job
    paired.press(ok)
    _poll(win, qapp)
    win._batch_job = None
    assert not approval.is_approved(job) and "đang dựng" in paired.texts()[-1]


def test_reaching_the_gate_sends_the_approval_message_once(win, paired, storage, qapp):
    job = _episode(storage)
    win._on_queued_gate(job, {"_remote": "telegram"})
    win._on_queued_gate(job, {"_remote": "telegram"})
    assert win.remote.outbox.wait()
    (msg,) = paired.sent
    assert msg["text"].startswith("🟡 Chờ duyệt") and paired.buttons(msg)[1] == f"ed:{job.id}"
    telegram.set_notify("gate", False)
    (job.work_dir / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nKhác.\n",
                                         encoding="utf-8")
    win._on_queued_gate(job, {})
    assert win.remote.outbox.wait() and len(paired.sent) == 1


def test_milestones_and_failures_of_a_sent_link(win, paired, storage, qapp):
    job = _episode(storage)
    job.metadata = {"title": "第7集 | 《末日校车》09", "platform": "douyin", "duration": 125}
    win._queue_job, win._queue_opts = job, {"_remote": "telegram"}
    win._on_stage("subtitle")
    win._on_stage("translate")
    win._queue_job = win._queue_opts = None
    win._on_queued_failed(job, {"_remote": "telegram"}, "yt-dlp: HTTP 403")
    win._on_queued_failed(job, {"_remote": "telegram"}, "Đã hủy.")
    assert win.remote.outbox.wait()
    sub, tr, err = paired.texts()
    assert sub.startswith("⬇️ Đã tải xong") and "02:05 · Douyin" in sub
    assert tr.startswith("📝") and err.startswith("❌") and "403" in err


def test_planned_and_published_posts_are_reported(win, paired, storage, qapp):
    job = _episode(storage)
    (job.work_dir / "output.mp4").write_bytes(b"mp4")
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": True, "auto_short": False,
                                     "platforms": {"folder": {"on": True, "kinds": ["long"]}}})
    win._autopilot_rendered({"id": job.id, "title": "ep9", "autopilot": "long"})
    assert win.remote.outbox.wait()
    assert paired.texts()[-1].startswith("📅 Đã lên lịch đăng")
    posts = autopilot.load_posts()
    posts[0]["slot"] = time.time() - 5
    autopilot.save_posts(posts)
    win._post_due()
    assert _wait(qapp, lambda: not win._posting) and win.remote.outbox.wait()
    assert paired.texts()[-1].startswith("✅ Gói đăng (thư mục): đã đăng")


def test_fast_approve_never_runs_the_after_step_twice(win, paired, storage, qapp, monkeypatch):
    """The Telegram ✅ landed while the desktop «Duyệt nhanh» dialog was open."""
    from desktop.widgets import autopilot_dialogs

    job = _episode(storage)

    class Dialog:
        edit_requested = False

        def __init__(self, *a, **kw):
            self.kw = kw

        def exec(self):
            approval.approve(job)                            # the phone was quicker
            return True

    monkeypatch.setattr(autopilot_dialogs, "FastApproveDialog", Dialog)
    after = []
    win._autopilot_after_approve = lambda j: after.append(j.id)
    win._fast_approve(job.id)
    assert after == [] and "Đã duyệt rồi" in win.status.text.text()


def test_the_editor_and_the_bot_flag_the_same_lines(storage):
    from app.pipeline import quickreview
    from desktop import cuemodel, store

    job = _episode(storage)
    (job.work_dir / "vi.srt").write_text(
        "1\n00:00:00,000 --> 00:00:00,300\nNgắn quá.\n\n2\n00:00:01,000 --> 00:00:03,000\nỔn.\n",
        encoding="utf-8")
    rows = store.flagged_lines(job)
    assert rows == quickreview.flagged_lines(job) and rows and rows[0]["n"] == 1
    c = cuemodel.load_pair(job)[0]
    assert cuemodel.fingerprint(c) == quickreview.fingerprint(c.start, c.end, c.vi)
    cuemodel.mark_reviewed(c)
    cues = cuemodel.load_pair(job)
    cues[0].reviewed = c.reviewed
    cuemodel.save_vi(job, cues)
    assert [r["n"] for r in quickreview.flagged_lines(job)] == [r["n"] for r in rows[1:]]


# --- the Settings card ------------------------------------------------------------------------

@pytest.fixture
def view(qapp, fake):
    from desktop.settings_dialog import SettingsView

    v = SettingsView()

    def sync(fn, on_done):
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 - what the real _bg hands over
            res = exc
        on_done(res)

    v._bg = sync
    v.show_group("accounts")
    yield v
    v.shutdown()
    v.close()


def test_the_card_connects_pairs_toggles_and_unpairs(view, fake, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    tg = view._tg
    assert tg["badge"].text() == "Chưa nối" and not tg["drop"].isVisible()
    tg["token"].setText("not a token")
    tg["connect"].click()
    assert "đúng dạng" in tg["status"].text() and not telegram.configured()
    tg["token"].setText(TOKEN)
    tg["connect"].click()
    code = telegram.status()["code"]
    assert tg["token"].text() == "" and f"/start {code}" in tg["status"].text()
    assert "@vizsup_test_bot" in tg["status"].text() and tg["badge"].text() == "Chờ ghép"
    assert TOKEN not in tg["status"].text() and TOKEN not in tg["token"].placeholderText()
    assert tg["timer"].isActive()
    telegram.TelegramChannel().accept(fake.say(f"/start {code}"))     # the owner, on the phone
    view.refresh_telegram()
    assert tg["badge"].text() == "Đã ghép" and "Bách" in tg["status"].text()
    assert not tg["timer"].isActive() and tg["connect"].text() == "Lấy mã ghép mới"
    tg["checks"]["posted"].setChecked(False)
    assert telegram.notify_on("posted") is False and telegram.notify_on("gate") is True
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    tg["drop"].click()
    assert not telegram.configured() and tg["badge"].text() == "Chưa nối"
    assert any("gỡ kết nối" in t for t in fake.texts())


def test_a_refused_token_is_shown(view, fake):
    fake.token = "999:other"                                   # Telegram does not know ours
    view._tg["token"].setText(TOKEN)
    view._tg["connect"].click()
    assert "không nhận token" in view._tg["status"].text() and not telegram.configured()
