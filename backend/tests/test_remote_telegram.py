"""Điều khiển qua Telegram (app/remote/telegram.py, app/pipeline/remote.py): message
formatting, pairing, the paired-chat-only rule, the revision-guarded [✅ Duyệt], links,
offset persistence, /trangthai, and `vizsup autopilot --once` answering the bot while the app
is closed. The Bot API is faked (tests/telegram_fake.py) — nothing reaches Telegram."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from app.config import settings
from app.models import Job
from app.pipeline import approval, autopilot, remote
from app.remote import Event, telegram

from telegram_fake import OWNER, STRANGER, TOKEN, FakeTelegram, pair

SHARE = ("7.43 复制打开抖音，看看【末日校车的作品】第7集 # 末日校车 https://v.douyin.com/iRNBho6u/ "
         "ZzA:/ 12/31 b@a.Ab")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vizsup_storage_dir", tmp_path)
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


def _series(storage: Path, plan_on: bool = True) -> None:
    sd = storage / "_series" / "末日校车"
    sd.mkdir(parents=True)
    (sd / "series.json").write_text(json.dumps({
        "name": "末日校车", "match": "末日校车",
        "render": {"voice": "vi-VN-NamMinhNeural", "rate": "+30%", "frame": "source"}}),
        encoding="utf-8")
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": plan_on, "auto_short": False,
                                     "platforms": {"folder": {"on": True, "kinds": ["long"]}}})


def _episode(storage: Path, jid="ep9a1b2c3d4", url="https://v.douyin.com/iRNBho6u/",
             lines=("Xin chào.", "Đi thôi!")) -> Job:
    d = storage / jid
    d.mkdir()
    srt = "".join(f"{i}\n00:00:0{2 * i - 2},000 --> 00:00:0{2 * i - 1},000\n{t}\n\n"
                  for i, t in enumerate(lines, 1))
    (d / "vi.srt").write_text(srt, encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": "第7集 | 《末日校车》09"}), encoding="utf-8")
    (d / "job.json").write_text(json.dumps({"id": jid, "url": url, "opts": {"url": url}}),
                                encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": 9}),
                                        encoding="utf-8")
    return Job(id=jid, work_dir=d)


class Host(remote.Host):
    def __init__(self, before=None):
        self.links, self.approved, self._before = [], [], before

    def queue_link(self, url, job_id):
        self.links.append((url, job_id))
        return "📥 Đã nhận link"

    def before_approve(self, job):
        return self._before(job) if self._before else None

    def after_approve(self, job):
        self.approved.append(job.id)
        return "Đang dựng."


def _run(fake: FakeTelegram, host) -> list:
    ch = telegram.TelegramChannel()
    outs = remote.process(ch.poll(0), host, ch)
    remote.deliver(outs, ch, sleep=lambda _s: None)
    return outs


# --- pure formatting ---------------------------------------------------------------------------

def test_split_text_keeps_lines_and_the_limit():
    text = "\n".join(f"dòng {i} " + "x" * 50 for i in range(200))
    parts = telegram.split_text(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts) and len(parts) > 5
    assert "\n".join(parts).split("\n") == text.split("\n")          # nothing lost, no line cut
    assert telegram.split_text("a" * 2500, limit=1000) == ["a" * 1000, "a" * 1000, "a" * 500]
    assert telegram.split_text("") == ["…"]


def test_links_masks_and_scrubbing():
    assert remote.find_link(SHARE) == "https://v.douyin.com/iRNBho6u/"
    assert remote.find_link("xem https://www.bilibili.com/video/BV1xx411c7mD?p=2。") == \
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
    assert remote.find_link("【标题】 https://b23.tv/AbC123") == "https://b23.tv/AbC123"
    assert remote.find_link("https://youtube.com/watch?v=1") is None
    assert telegram.mask(TOKEN) == "…3456" and TOKEN not in telegram.mask(TOKEN)
    assert TOKEN not in telegram.scrub(f"POST https://api.telegram.org/bot{TOKEN}/getUpdates")


def test_the_token_never_reaches_a_log_line(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    rec = logging.LogRecord("httpx", logging.INFO, "x", 1, 'HTTP Request: %s %s "%s"',
                            ("POST", f"https://api.telegram.org/bot{TOKEN}/getUpdates", "200"), None)
    for f in logging.getLogger("httpx").filters:
        f.filter(rec)
    assert TOKEN not in rec.getMessage()


def test_approval_text_caps_the_flagged_lines():
    rows = [{"n": i, "start": 7.7 + i, "zh": "你倒是会挑。", "vi": "Cô biết chọn ghê.",
             "flags": ["AI nghi sai"]} for i in range(1, 46)]
    text = remote.approval_text("Tập 9", 65, rows, "Duyệt xong app tự dựng …")
    assert text.startswith("🟡 Chờ duyệt: Tập 9") and "65 câu · 45 câu" in text
    assert "#1 · 00:08 · AI nghi sai" in text and "#40 ·" in text and "#41 ·" not in text
    assert "… còn 5 câu nữa — mở trên máy để xem hết." in text
    assert text.rstrip().endswith("Sau khi duyệt: Duyệt xong app tự dựng …")
    assert "không có câu nào bị đánh dấu" in remote.approval_text("Tập 9", 3, [], "x")


# --- pairing / auth ------------------------------------------------------------------------------

def test_connect_checks_the_token_and_pairing_takes_the_right_code(fake):
    with pytest.raises(telegram.TelegramError, match="đúng dạng"):
        telegram.connect("abc")
    res = telegram.connect(TOKEN)
    acc = json.loads((settings.vizsup_storage_dir / "_accounts" / "telegram.json")
                     .read_text(encoding="utf-8"))
    assert acc["token"] == TOKEN and acc["bot"] == "vizsup_test_bot" and "chat_id" not in acc
    assert res["bot"] == "vizsup_test_bot" and len(res["code"]) == 6 and res["code"].isdigit()
    assert telegram.status()["code"] == res["code"] and TOKEN not in json.dumps(telegram.status())
    ch = telegram.TelegramChannel()
    assert ch.accept(fake.say("hello", chat=STRANGER)) is None
    wrong = "000000" if res["code"] != "000000" else "111111"
    assert ch.accept(fake.say(f"/start {wrong}", chat=STRANGER)) is None
    assert not telegram.paired() and telegram.load()["pair"]["tries"] == 1
    ev = ch.accept(fake.say(f"/start {res['code']}", chat=OWNER, first="Bách"))
    assert ev.kind == "paired" and ev.who == "Bách"
    assert telegram.paired() and telegram.load()["chat_id"] == OWNER and telegram.pairing() is None
    # the code is spent: the stranger can't take over with it
    assert ch.accept(fake.say(f"/start {res['code']}", chat=STRANGER)) is None
    assert telegram.load()["chat_id"] == OWNER
    # "Lấy mã ghép mới" from the paired chat itself: the code is used up, still that chat
    code = telegram.new_pairing()
    assert ch.accept(fake.say(f"/start {code}", chat=OWNER)).kind == "paired"
    assert telegram.pairing() is None and telegram.load()["chat_id"] == OWNER
    assert ch.accept(fake.say("/start", chat=OWNER)).kind == "text"


def test_a_pairing_code_dies_after_five_wrong_tries_or_30_minutes(fake):
    code = telegram.connect(TOKEN)["code"]
    ch = telegram.TelegramChannel()
    wrong = f"{(int(code) + 1) % 10 ** 6:06d}"
    for _ in range(telegram.PAIR_TRIES):
        ch.accept(fake.say(f"/start {wrong}", chat=STRANGER))
    assert telegram.pairing() is None
    assert ch.accept(fake.say(f"/start {code}")) is None and not telegram.paired()
    code = telegram.new_pairing(now=time.time() - telegram.PAIR_TTL_S - 1)
    assert ch.accept(fake.say(f"/start {code}")) is None and not telegram.paired()


def test_updates_from_other_chats_are_ignored_without_a_reply(paired, storage):
    _episode(storage)
    host = Host()
    paired.say(SHARE, chat=STRANGER)
    paired.say("/trangthai", chat=STRANGER)
    paired.press("ok:ep9a1b2c3d4:" + "0" * 8, chat=STRANGER)
    u = paired.say("/help", chat=OWNER)
    u["message"]["chat"]["type"] = "group"                      # the owner, but in a group
    outs = _run(paired, host)
    assert outs == [] and paired.sent == [] and paired.acks == [] and host.links == []
    assert not approval.is_approved(Job(id="ep9a1b2c3d4", work_dir=storage / "ep9a1b2c3d4"))
    assert telegram.load()["offset"] == paired.uid + 1


def test_the_offset_is_saved_so_each_update_is_handled_once(paired):
    host = Host()
    paired.say(SHARE)
    _run(paired, host)
    assert host.links == [("https://v.douyin.com/iRNBho6u/", None)]
    assert paired.texts() == ["📥 Đã nhận link"]
    before = int(telegram.load()["offset"])
    _run(paired, host)                                          # Telegram would resend nothing
    assert host.links == [("https://v.douyin.com/iRNBho6u/", None)] and len(paired.sent) == 1
    gets = [p for m, p in paired.calls if m == "getUpdates"]
    assert gets[-1]["offset"] == before == paired.uid + 1


def test_help_status_and_unknown_text(paired, storage):
    paired.say("/help")
    paired.say("/trangthai")
    paired.say("chào bot")
    _run(paired, Host())
    help_, status, unknown = paired.texts()
    assert "/trangthai" in help_ and "không bao giờ tự duyệt" in help_
    assert status.startswith("📊 vizsup") and "Không có tập nào chờ duyệt" in status
    assert "chưa hiểu" in unknown


# --- the approve guard ----------------------------------------------------------------------------

def _approve_button(fake: FakeTelegram, job: Job) -> str:
    fake.sent.clear()
    remote.deliver(remote.gate_outs(job), telegram.TelegramChannel())
    (msg,) = fake.sent
    ok, edit = fake.buttons(msg)
    assert edit == f"ed:{job.id}" and ok == f"ok:{job.id}:{approval.revision(job)[:8]}"
    fake.sent.clear()
    return ok


def test_approve_press_approves_exactly_that_revision_once(paired, storage):
    _series(storage)
    job = _episode(storage)
    ok = _approve_button(paired, job)
    assert remote.gate_outs(job) == []                          # sent once per revision
    host = Host()
    paired.press(ok)
    _run(paired, host)
    assert approval.is_approved(job) and host.approved == [job.id]
    assert paired.acks[-1]["text"] == "Đã duyệt" and paired.edits     # buttons removed
    assert paired.texts()[-1].startswith("✅ Đã duyệt") and "Đang dựng." in paired.texts()[-1]
    paired.press(ok)                                            # a second press: refused
    _run(paired, host)
    assert host.approved == [job.id] and "đã được duyệt rồi" in paired.texts()[-1]


def test_a_changed_revision_is_refused_and_the_new_version_sent(paired, storage):
    job = _episode(storage)
    ok = _approve_button(paired, job)
    (job.work_dir / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nĐã sửa.\n",
                                         encoding="utf-8")
    host = Host()
    paired.press(ok)
    _run(paired, host)
    assert not approval.is_approved(job) and host.approved == []
    refusal, fresh = paired.texts()
    assert refusal.startswith("⛔") and "CHƯA duyệt" in refusal
    new_ok = paired.buttons(paired.sent[-1])[0]
    assert new_ok == f"ok:{job.id}:{approval.revision(job)[:8]}" != ok
    assert "🟡 Chờ duyệt" in fresh


def test_the_open_editor_saving_edits_blocks_the_press(paired, storage):
    """The desktop's before_approve saves the open editor first; text it changes is a new
    revision the owner has not seen on the phone → refused."""
    job = _episode(storage)
    ok = _approve_button(paired, job)

    def editor_saves(j):
        (j.work_dir / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nSửa dở.\n",
                                           encoding="utf-8")

    host = Host(before=lambda j: editor_saves(j))
    paired.press(ok)
    _run(paired, host)
    assert not approval.is_approved(job) and host.approved == []
    assert "CHƯA duyệt" in paired.texts()[0]
    busy = Host(before=lambda j: "Tập này đang dựng trên máy — đợi xong rồi duyệt.")
    paired.press(paired.buttons(paired.sent[-1])[0])
    _run(paired, busy)
    assert not approval.is_approved(job) and "đang dựng" in paired.texts()[-1]


def test_a_failing_after_step_still_says_it_was_approved(paired, storage):
    job = _episode(storage)
    ok = _approve_button(paired, job)

    class Broken(Host):
        def after_approve(self, job):
            raise RuntimeError("hàng dựng hỏng")

    paired.press(ok)
    _run(paired, Broken())
    assert approval.is_approved(job)
    assert paired.texts()[-1].startswith("✅ Đã duyệt") and "chưa tự dựng được" in paired.texts()[-1]


def test_unknown_or_forged_buttons_never_approve(paired, storage):
    job = _episode(storage)
    host = Host()
    for data in ("ok:../../etc:12345678", "ok:nosuchjob1:12345678", f"ok:{job.id}:", "ok:x",
                 "hack", f"ed:{job.id}"):
        paired.press(data)
    _run(paired, host)
    assert host.approved == [] and not approval.is_approved(job)
    assert not (storage.parent / "etc").exists() and not (storage / "nosuchjob1").exists()
    assert any("Để dành" in t for t in paired.texts())


# --- links ----------------------------------------------------------------------------------------

def test_a_link_already_at_the_gate_resends_its_approval_message(paired, storage):
    job = _episode(storage, url="https://v.douyin.com/iRNBho6u/")
    host = Host()
    paired.say(SHARE)
    _run(paired, host)
    assert host.links == []
    (msg,) = paired.sent
    assert "đã có trên máy" in msg["text"] and paired.buttons(msg)[0].startswith(f"ok:{job.id}:")
    approval.approve(job)
    paired.say(SHARE)
    _run(paired, host)
    assert "đã làm rồi" in paired.texts()[-1] and host.links == []


def test_status_lists_waiting_queue_posts_and_failures(paired, storage):
    _episode(storage)
    now = time.time()
    autopilot.save_posts([
        {"id": "a", "job": "ep9a1b2c3d4", "kind": "long", "platform": "youtube", "slot": now + 3600,
         "status": "pending"},
        {"id": "b", "job": "ep9a1b2c3d4", "kind": "short", "platform": "tiktok", "slot": now - 60,
         "status": "private", "result": {"url": "https://tiktok.com/@me/video/1", "at": now}},
        {"id": "c", "job": "ep9a1b2c3d4", "kind": "long", "platform": "facebook", "slot": now - 60,
         "status": "failed", "result": {"message": "Hết hạn mức", "at": now}}])
    text = remote.status_text(["Tập 8 (đang dựng)"], now=now)
    assert "🟡 Chờ duyệt (1):" in text and "第7集" in text
    assert "🎬 Đang dựng / chờ dựng (1):" in text and "Tập 8" in text
    assert "📅 Sắp đăng trong 24 giờ (1):" in text and "YouTube · video dài" in text
    assert "https://tiktok.com/@me/video/1" in text and "Hết hạn mức" in text


def test_post_results_and_plans_follow_the_notification_toggles(paired, storage):
    job = _episode(storage)
    post = {"id": "x", "job": job.id, "kind": "long", "platform": "youtube", "status": "done",
            "result": {"url": "https://youtu.be/1", "message": ""}}
    ch = telegram.TelegramChannel()
    remote.deliver(remote.posted_outs(post), ch)
    assert "✅ YouTube: đã đăng" in paired.texts()[-1] and "https://youtu.be/1" in paired.texts()[-1]
    telegram.set_notify("posted", False)
    remote.deliver(remote.posted_outs(post), ch)
    assert len(paired.sent) == 1
    remote.deliver(remote.planned_outs(job, [{**post, "slot": time.time() + 60}]), ch)
    assert paired.texts()[-1].startswith("📅 Đã lên lịch đăng")


def test_unpair_forgets_everything(paired):
    remote.unpair()
    assert "gỡ kết nối" in paired.texts()[-1]
    assert not telegram.configured() and not (settings.vizsup_storage_dir / "_accounts" /
                                              "telegram.json").exists()


# --- the scheduled task (app closed) --------------------------------------------------------------

@pytest.fixture
def cli_env(paired, storage, monkeypatch):
    """`vizsup autopilot --once` with the pipeline, the providers and the render faked."""
    from app import cli
    from app.providers import registry

    _series(storage)
    monkeypatch.setattr(registry, "list_providers", lambda: {
        "translation": [{"name": "passthrough"}, {"name": "openrouter", "can_refine": True}],
        "asr": [{"name": "audio_llm"}], "ocr": [{"name": "rapidocr"}], "tts": [{"name": "edge"}]})
    (storage / "ui_state.json").write_text(json.dumps({"translator": "openrouter"}),
                                           encoding="utf-8")
    runs, renders = [], []

    def fake_run_one(args, url):
        runs.append(url)
        wd = Path(args.workdir)
        (wd / "metadata.json").write_text(json.dumps({"title": "第7集 | 《末日校车》09",
                                                      "platform": "douyin"}), encoding="utf-8")
        (wd / "vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào.\n",
                                   encoding="utf-8")
        (wd / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": 9}),
                                             encoding="utf-8")
        return 0

    def fake_render(job, kind="long", log=None):
        assert approval.is_approved(job), "rendered an unapproved revision"
        renders.append((job.id, kind))
        (job.work_dir / "output.mp4").write_bytes(b"mp4")
        return job.work_dir / "output.mp4"

    monkeypatch.setattr(cli, "_run_one", fake_run_one)
    monkeypatch.setattr(remote, "render_headless", fake_render)
    return cli, runs, renders


def test_the_task_runs_a_sent_link_then_renders_only_after_the_press(cli_env, paired):
    cli, runs, renders = cli_env
    paired.say(SHARE)
    assert cli.main(["autopilot", "--once"]) == 0
    assert runs == ["https://v.douyin.com/iRNBho6u/"] and renders == []
    texts = paired.texts()
    assert texts[0].startswith("📥 Đã nhận link") and "🟡 Chờ duyệt" in texts[1]
    jid = Path(next(settings.vizsup_storage_dir.glob("*/vi.srt")).parent).name
    job = remote.load_job(jid)
    assert not approval.is_approved(job)                       # the task never approves by itself
    ok = paired.buttons(paired.sent[1])[0]
    assert ok == f"ok:{jid}:{approval.revision(job)[:8]}"
    paired.sent.clear()
    paired.press(ok)
    assert cli.main(["autopilot", "--once"]) == 0
    assert approval.is_approved(job) and renders == [(jid, "long")]
    texts = paired.texts()
    assert texts[0].startswith("✅ Đã duyệt") and "tác vụ nền đang dựng" in texts[0]
    assert texts[1].startswith("📅 Đã lên lịch đăng") and "Gói đăng" in texts[1]
    (post,) = autopilot.load_posts()
    assert post["job"] == jid and post["status"] == "pending"
    assert remote.poll_holder() is None                         # handed back
    paired.sent.clear()
    assert cli.main(["autopilot", "--once"]) == 0               # nothing new: nothing sent
    assert paired.sent == [] and renders == [(jid, "long")]


def test_the_task_only_sends_while_the_open_app_reads_the_bot(cli_env, paired, storage):
    cli, runs, _renders = cli_env
    (storage / "_remote").mkdir(exist_ok=True)
    (storage / "_remote" / "poll.json").write_text(json.dumps(
        {"pid": os.getpid() + 1, "who": "app", "at": time.time()}), encoding="utf-8")
    paired.say(SHARE)
    assert cli.main(["autopilot", "--once"]) == 0
    assert runs == [] and not [m for m, _p in paired.calls if m == "getUpdates"]
    job = _episode(storage, jid="ep10ffff0000")
    remote.CliSession().send(remote.gate_outs(job))             # sending still works
    assert "🟡 Chờ duyệt" in paired.texts()[-1]


def test_a_due_post_is_reported_by_the_task(cli_env, paired, storage):
    cli, _runs, _renders = cli_env
    job = _episode(storage)
    (job.work_dir / "output.mp4").write_bytes(b"mp4")
    autopilot.save_posts([{"id": f"{job.id}:long:folder", "job": job.id, "kind": "long",
                           "platform": "folder", "series": "末日校车", "slot": time.time() - 5,
                           "status": "pending"}])
    assert cli.main(["autopilot", "--once"]) == 0
    assert any(t.startswith("✅ Gói đăng (thư mục): đã đăng") for t in paired.texts())


def test_the_task_install_repeats_every_15_minutes_once_paired(paired, monkeypatch):
    import subprocess

    from app import schedtask

    xmls, calls = [], []

    def run(cmd, **kw):
        calls.append(cmd)
        if "/XML" in cmd:
            xmls.append(Path(cmd[cmd.index("/XML") + 1]).read_bytes().decode("utf-16"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(schedtask.subprocess, "run", run)
    monkeypatch.setattr(schedtask, "supported", lambda: True)
    msg = schedtask.install_task(6)
    assert "<Interval>PT15M</Interval>" in xmls[-1] and "mỗi 15 phút" in msg
    assert "<Interval>PT6H</Interval>" in schedtask.task_xml(6)
    assert "<Interval>PT20M</Interval>" in schedtask.task_xml(6, every_min=20)
    remote.unpair()
    assert "mỗi 6 giờ" in schedtask.install_task(6)


def test_the_render_body_refuses_an_unapproved_revision(storage):
    from app.pipeline import render

    job = _episode(storage)
    with pytest.raises(RuntimeError, match="chưa được duyệt"):
        render.render_job(job, {"tts": "edge"})
    with pytest.raises(InterruptedError):
        render.render_job(job, {}, cancel=lambda: True)


def test_after_approve_text_is_the_dialogs(storage):
    job = _episode(storage)
    assert "chưa bật Tự động đăng" in autopilot.after_approve_text(job)
    _series(storage)
    text = autopilot.after_approve_text(job)
    assert text.startswith("Duyệt xong app tự dựng") and "Gói đăng" in text


def test_events_are_plain_dataclasses():
    ev = Event("text", text="hi")
    assert ev.kind == "text" and ev.data == "" and ev.message_id is None
