"""More of the Telegram remote (app/pipeline/remote.py, app/remote/telegram.py): a 合集 / page
link → the NEW videos offered with [▶️ Dịch N tập mới]; /cho → the gate, five at a time; a
photo → a project's cover (and the YouTube video's, when it is up); the cover with the
schedule after a render; the "/" command menu; the app-closed task doing the same. The Bot
API, the page lister and the publishers are faked — nothing reaches Telegram, Douyin,
Bilibili or YouTube."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from app.config import settings
from app.models import Job
from app.pipeline import approval, autopilot, remote, thumbnail, watch
from app.providers.base import PostResult
from app.remote import Event, telegram

from telegram_fake import OWNER, STRANGER, TOKEN, FakeTelegram, pair

MIX = "https://www.douyin.com/collection/7699176142011385907"


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


def _video(n: int, published: int | None = None) -> dict:
    vid = f"76999197545212{n:05d}"
    return {"id": vid, "url": f"https://www.douyin.com/video/{vid}",
            "title": f"第{n}集 《末日校车》{n:02d} #短剧 #末日", "published": published}


PAGE = [_video(n, 1792800000 + n * 86400) for n in range(14, 0, -1)]   # newest first


@pytest.fixture
def lister(monkeypatch):
    """The page lister is always a fake here (the real one opens Douyin / calls yt-dlp)."""
    calls: list[str] = []
    page = list(PAGE)

    def fake(url, limit=100):
        calls.append(url)
        return list(page)

    monkeypatch.setattr(watch, "list_videos", fake)
    return calls, page


def _series(storage: Path, *, plan_on=True, name="Chuyến Xe Tận Thế") -> None:
    sd = storage / "_series" / "末日校车"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "series.json").write_text(json.dumps({
        "name": "末日校车", "match": "末日校车",
        "render": {"voice": "vi-VN-NamMinhNeural", "rate": "+30%", "frame": "source"}}),
        encoding="utf-8")
    autopilot.save_plan("末日校车", {**autopilot.DEFAULT_PLAN, "on": plan_on, "auto_short": False,
                                     "series_name": name,
                                     "platforms": {"folder": {"on": True, "kinds": ["long"]}}})


def _episode(storage: Path, n: int, *, url: str | None = None, approved=False, rendered=False,
             age: float = 0.0) -> Job:
    jid = f"ep{n:02d}aa11bb22"
    d = storage / jid
    d.mkdir()
    (d / "vi.srt").write_text(f"1\n00:00:00,000 --> 00:00:01,000\nTập {n} đây.\n",
                              encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps({"title": f"第{n}集 《末日校车》{n:02d}"}),
                                     encoding="utf-8")
    url = url or f"https://www.douyin.com/video/76999197545212{n:05d}"
    (d / "job.json").write_text(json.dumps({"id": jid, "url": url, "opts": {"url": url}}),
                                encoding="utf-8")
    (d / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": n}),
                                        encoding="utf-8")
    job = Job(id=jid, work_dir=d)
    if approved:
        approval.approve(job)
    if rendered:
        (d / "output.mp4").write_bytes(b"mp4")
    if age:
        import os

        t = time.time() - age
        for f in d.iterdir():
            os.utime(f, (t, t))
    return job


class Host(remote.Host):
    def __init__(self, busy=()):
        self.links, self.batches, self.approved, self._busy = [], [], [], set(busy)

    def queue_link(self, url, job_id):
        self.links.append(url)
        return "📥 Đã nhận link"

    def queue_batch(self, videos, series):
        self.batches.append(([v["url"] for v in videos], series))
        return f"📥 Đã xếp {len(videos)} tập"

    def busy(self, job_id):
        return "đang xử lý" if job_id in self._busy else None

    def after_approve(self, job):
        self.approved.append(job.id)
        return "Đang dựng."


def _run(host) -> list:
    ch = telegram.TelegramChannel()
    outs = remote.process(ch.poll(0), host, ch)
    remote.deliver(outs, ch, sleep=lambda _s: None)
    return outs


def _jpeg(size=(800, 600), color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


# --- the Telegram client --------------------------------------------------------------------------

def test_a_photo_from_the_owner_is_an_event_from_anyone_else_nothing(paired):
    ch = telegram.TelegramChannel()
    ev = ch.accept(paired.photo(b"img", caption="tập 9"))
    assert ev.kind == "photo" and ev.file == f"F{paired.uid}" and ev.text == "tập 9"
    assert ev.size == 3                                       # the largest size, not the first
    ev = ch.accept(paired.photo(b"png!", as_file=True))
    assert ev.kind == "photo" and ev.file == f"F{paired.uid}"
    assert ch.accept(paired.photo(b"img", chat=STRANGER)) is None
    doc = paired.say("")
    doc["message"]["document"] = {"file_id": "D1", "mime_type": "application/pdf"}
    assert ch.accept(doc).kind == "text"                      # not a picture
    assert telegram.picture({"photo": []}) is None


def test_send_photo_download_and_the_command_menu(paired, storage):
    ch = telegram.TelegramChannel()
    pic = storage / "bia.jpg"
    pic.write_bytes(_jpeg())
    ch.send_photo(pic, "📅 Đã lên lịch", [[remote.Button("A", "a:1")]])
    (sent,) = paired.photos
    assert sent["caption"] == "📅 Đã lên lịch" and paired.buttons(sent) == ["a:1"]
    assert sent["photo"][1] == pic.read_bytes() and str(OWNER) == sent["chat_id"]
    ch.send_photo(pic, "x" * 1500)                            # too long for a caption
    assert "caption" not in paired.photos[-1] and paired.texts()[-1] == "x" * 1500
    paired.files["F9"] = b"the owner's picture"
    out = ch.download("F9", storage / "_remote" / "photos" / "a.img")
    assert out.read_bytes() == b"the owner's picture" and paired.downloads == ["photos/F9.jpg"]
    with pytest.raises(telegram.TelegramError) as err:
        ch.download("nope", storage / "b.img")
    assert TOKEN not in str(err.value)
    paired.files["BIG"] = b"x" * 50
    with pytest.raises(telegram.TelegramError, match="lớn quá"):
        ch.download("BIG", storage / "c.img", max_bytes=10)
    ch.set_commands(list(remote.COMMANDS))
    names = [c["command"] for c in paired.commands[-1]]
    assert names[0] == "cho" and "trangthai" in names and "help" in names


def test_the_command_menu_is_set_once_and_again_on_pairing(paired):
    paired.say("/help")
    _run(Host())
    assert len(paired.commands) == 1
    paired.say("/help")
    _run(Host())
    assert len(paired.commands) == 1                          # same menu: not sent again
    code = telegram.new_pairing()
    paired.say(f"/start {code}")
    _run(Host())
    assert len(paired.commands) == 2
    assert all(len(c) <= 32 and c.islower() for c, _d in remote.COMMANDS)
    assert all(f"/{c}" in remote.HELP for c, _d in remote.COMMANDS)


# --- a page link: the new videos, one press -------------------------------------------------------

def test_a_page_link_offers_only_the_new_videos(paired, storage, lister):
    _series(storage)
    _episode(storage, 1)                                      # already on the machine
    ep2 = _episode(storage, 2, url="https://v.douyin.com/iRNBho6u/")   # a share link …
    (ep2.work_dir / "metadata.json").write_text(json.dumps(          # … its video id inside
        {"title": "第2集 《末日校车》02", "video_id": PAGE[-2]["id"]}), encoding="utf-8")
    host = Host()
    paired.say(f"xem hết nè {MIX}")
    _run(host)
    reading, offer = paired.sent
    assert reading["text"].startswith("📋 Đang đọc") and "1–2 phút" in reading["text"]
    text = offer["text"]
    assert "14 video · 2 đã có trên máy" in text and "🆕 12 tập mới" in text
    assert "第3集" in text and "第12集" in text and "第13集" not in text   # oldest first, 10 named
    assert "… và 2 tập nữa" in text and "#短剧" not in text
    assert "Bộ phim: 《末日校车》" in text and "không tự duyệt" in text
    go, skip = paired.buttons(offer)
    assert go.startswith("bt:") and skip.startswith("bx:") and len(go) <= 64
    assert paired.labels(offer)[0] == "▶️ Dịch 12 tập mới"
    assert host.links == [] and host.batches == [] and lister[0] == [MIX]
    # meanwhile episode 14 was made on the machine (the watcher): it is not queued twice
    _episode(storage, 14)
    paired.press(go)
    _run(host)
    ((urls, series),) = host.batches
    assert series == "末日校车" and len(urls) == 11
    assert urls[0] == PAGE[0 + 1]["url"] and PAGE[-1]["url"] not in urls     # page order kept
    assert paired.acks[-1]["text"] == "Đã xếp 11 tập" and paired.edits
    assert paired.texts()[-1] == "📥 Đã xếp 11 tập"
    paired.press(go)                                          # pressed again: nothing twice
    _run(host)
    assert len(host.batches) == 1 and "hết hạn hoặc đã bấm rồi" in paired.texts()[-1]
    assert not any(approval.is_approved(Job(id=d.name, work_dir=d))
                   for d in storage.iterdir() if (d / "vi.srt").exists())


def test_skip_a_page_with_nothing_new_and_a_page_that_cannot_be_read(paired, storage, lister,
                                                                    monkeypatch):
    host = Host()
    paired.say(MIX)
    _run(host)
    skip = paired.buttons(paired.sent[-1])[1]
    paired.press(skip)
    _run(host)
    assert paired.acks[-1]["text"] == "Đã bỏ qua" and host.batches == []
    paired.press(paired.buttons(paired.sent[-1])[0])         # the old offer is gone
    _run(host)
    assert host.batches == [] and "hết hạn" in paired.texts()[-1]
    lister[1][:] = PAGE[:2]
    for n in (14, 13):
        _episode(storage, n)
    paired.say("https://space.bilibili.com/12345")
    _run(host)
    assert "Trang Bilibili: 2 video · 2 đã có trên máy" in paired.texts()[-1]
    assert "Không có tập mới" in paired.texts()[-1] and not paired.buttons(paired.sent[-1])

    def broken(url, limit=100):
        raise RuntimeError("Trang Douyin mở quá lâu — thử lại sau.")

    monkeypatch.setattr(watch, "list_videos", broken)
    paired.say(MIX)
    _run(host)
    assert paired.texts()[-1] == "⚠ Chưa đọc được danh sách video: Trang Douyin mở quá lâu — thử lại sau."


def test_a_single_video_link_is_one_video_as_before(paired, storage, lister):
    host = Host()
    paired.say("https://www.douyin.com/video/7699919082623110419")
    _run(host)
    assert host.links == ["https://www.douyin.com/video/7699919082623110419"]
    assert lister[0] == [] and paired.texts() == ["📥 Đã nhận link"]


def test_without_a_known_series_each_video_finds_its_own(paired, storage, lister):
    host = Host()
    paired.say(MIX)
    _run(host)
    assert "mỗi video tự nhận theo tên 《…》" in paired.texts()[-1]
    paired.press(paired.buttons(paired.sent[-1])[0])
    _run(host)
    assert host.batches[0][1] == "auto" and len(host.batches[0][0]) == 14


# --- /cho -------------------------------------------------------------------------------------------

def test_cho_sends_the_gate_five_at_a_time_oldest_first(paired, storage):
    _series(storage)
    eps = [_episode(storage, n, age=(10 - n) * 60) for n in range(1, 8)]
    _episode(storage, 8, approved=True)                       # not waiting
    host = Host(busy={eps[6].id})                             # still being processed
    paired.say("/cho")
    _run(host)
    head, *msgs, tail = paired.sent
    assert head["text"].startswith("🟡 6 video chờ duyệt — đây là 5 video:")
    assert [m["text"].split("\n")[0] for m in msgs] == [
        f"🟡 Chờ duyệt: Chuyến Xe Tận Thế · Tập {n}" for n in range(1, 6)]
    for m, job in zip(msgs, eps):
        assert paired.buttons(m)[0] == f"ok:{job.id}:{approval.revision(job)[:8]}"
    assert tail["text"] == "… còn 1 video, gửi /cho lần nữa."
    paired.sent.clear()
    paired.say("/choduyet")
    _run(host)
    head, msg = paired.sent
    assert "Tập 6" in msg["text"] and "còn" not in head["text"]
    assert not any(approval.is_approved(j) for j in eps)     # /cho never approves
    paired.press(paired.buttons(msg)[0])                      # the owner's press does
    _run(host)
    assert approval.is_approved(eps[5]) and host.approved == [eps[5].id]
    for j in eps[:5]:
        approval.approve(j)
    paired.say("/cho")
    _run(host)
    assert paired.texts()[-1].startswith("🟢 Không có video nào chờ duyệt")


# --- a photo → a cover ------------------------------------------------------------------------------

class FakeYouTube:
    name, display_name, can_set_thumbnail = "youtube", "YouTube", True

    def __init__(self, ok=True, connected=True):
        self.ok, self._connected, self.set = ok, connected, []

    def connected(self):
        return self._connected

    def set_thumbnail(self, remote_id, image):
        self.set.append((remote_id, Path(image).read_bytes()[:2]))
        return PostResult(self.ok, "done" if self.ok else "failed", remote_id=remote_id,
                          message="đã đặt ảnh bìa" if self.ok else "kênh cần xác minh")


@pytest.fixture
def youtube(monkeypatch):
    from app.providers import registry

    yt = FakeYouTube()
    real = registry.get_publisher

    def get(name):
        return yt if name == "youtube" else real(name)

    monkeypatch.setattr(registry, "get_publisher", get)
    return yt


def _posts(job: Job, *, uploaded=True, waiting=False) -> None:
    now = time.time()
    posts = []
    if uploaded:
        posts.append({"id": f"{job.id}:long:youtube", "job": job.id, "kind": "long",
                      "platform": "youtube", "slot": now + 3600, "status": "scheduled",
                      "result": {"id": "abcDEF12345", "url": "https://youtu.be/abcDEF12345"}})
    if waiting:
        posts.append({"id": f"{job.id}:long:folder", "job": job.id, "kind": "long",
                      "platform": "folder", "slot": now + 7200, "status": "pending"})
    autopilot.save_posts(posts)


def test_a_photo_becomes_the_picked_videos_cover_and_youtubes(paired, storage, youtube):
    _series(storage)
    _episode(storage, 7, approved=True, rendered=True)
    ep8 = _episode(storage, 8, approved=True, rendered=True)
    _episode(storage, 9)                                      # waiting at the gate: no cover yet
    _posts(ep8)
    paired.photo(_jpeg(), caption="bìa tập 8 nè")
    _run(Host())
    (ask,) = paired.sent
    assert ask["text"].startswith("🖼 Ảnh này làm ảnh bìa cho video nào?") and "📺" in ask["text"]
    labels, datas = paired.labels(ask), paired.buttons(ask)
    assert labels[0] == "📺 Chuyến Xe Tận Thế · Tập 8"       # the caption's video first
    assert labels[1:] == ["Chuyến Xe Tận Thế · Tập 7", "Bỏ qua"]
    assert all(len(d) <= 64 for d in datas) and datas[0].endswith(f":{ep8.id}")
    assert not (ep8.work_dir / "thumb.jpg").exists() and paired.downloads == []
    paired.press(datas[0])
    _run(Host())
    assert paired.acks[-1]["text"] == "Đang đặt ảnh bìa…"
    assert thumbnail.user_chosen(ep8.work_dir) and thumbnail.load_info(ep8.work_dir)["auto"] is False
    from PIL import Image

    with Image.open(ep8.work_dir / "thumb.jpg") as im:
        assert im.size == (1280, 720)
    assert youtube.set == [("abcDEF12345", b"\xff\xd8")]
    (conf,) = paired.photos
    assert conf["caption"].startswith("🖼 Đã đổi ảnh bìa: Chuyến Xe Tận Thế · Tập 8")
    assert "✅ YouTube: đã thay ảnh bìa (video dài) — https://youtu.be/abcDEF12345" in conf["caption"]
    assert not list((storage / "_remote" / "photos").glob("*"))   # the download is not kept
    assert thumbnail.make_auto(ep8.work_dir) is None          # never replaced automatically
    paired.press(datas[0])                                    # the photo was used: once
    _run(Host())
    assert "quá hạn hoặc đã dùng rồi" in paired.texts()[-1] and len(youtube.set) == 1


def test_a_cover_for_a_video_not_posted_yet_and_youtube_failures(paired, storage, youtube):
    _series(storage)
    ep = _episode(storage, 5, approved=True)
    _posts(ep, uploaded=False, waiting=True)
    paired.photo(_jpeg((600, 900)), as_file=True)
    _run(Host())
    paired.press(paired.buttons(paired.sent[-1])[0])
    _run(Host())
    cap = paired.photos[-1]["caption"]
    assert "Ảnh sẽ dùng khi đăng: Gói đăng (thư mục) lúc" in cap and youtube.set == []
    autopilot.save_posts([])
    youtube.ok = False
    _posts(ep)
    paired.photo(_jpeg())
    _run(Host())
    paired.press(paired.buttons(paired.sent[-1])[0])
    _run(Host())
    assert "⚠ YouTube: kênh cần xác minh" in paired.photos[-1]["caption"]
    youtube._connected = False
    paired.photo(_jpeg())
    _run(Host())
    paired.press(paired.buttons(paired.sent[-1])[0])
    _run(Host())
    assert "chưa kết nối trên máy" in paired.photos[-1]["caption"]
    autopilot.save_posts([])
    paired.photo(_jpeg())
    _run(Host())
    paired.press(paired.buttons(paired.sent[-1])[0])
    _run(Host())
    assert paired.photos[-1]["caption"].endswith("Ảnh sẽ dùng cho lần đăng tới.")


def test_photo_edge_cases(paired, storage):
    paired.photo(_jpeg())
    _run(Host())
    assert "chưa có video nào đã duyệt hoặc đã dựng" in paired.texts()[-1]
    _series(storage)
    ep = _episode(storage, 3, approved=True)
    paired.photo(b"not a picture at all")
    _run(Host())
    cover, skip = paired.buttons(paired.sent[-1])
    paired.press(skip)
    _run(Host())
    assert paired.acks[-1]["text"] == "Đã bỏ qua ảnh này"
    paired.press(cover)
    _run(Host())
    assert "quá hạn" in paired.texts()[-1] and not (ep.work_dir / "thumb.jpg").exists()
    paired.photo(b"not a picture at all")
    _run(Host())
    paired.press(paired.buttons(paired.sent[-1])[0])
    _run(Host())
    assert "Không mở được ảnh này" in paired.texts()[-1] and not (ep.work_dir / "thumb.jpg").exists()
    big = paired.photo(b"x")
    big["message"]["photo"][1]["file_size"] = 30 * 1024 * 1024
    _run(Host())
    assert "lớn quá" in paired.texts()[-1]
    paired.photo(_jpeg(), chat=STRANGER)                      # a stranger: nothing at all
    n = len(paired.sent)
    _run(Host())
    assert len(paired.sent) == n and len(paired.downloads) == 1   # only the owner's press


# --- the cover with the schedule ---------------------------------------------------------------------

def test_the_schedule_comes_with_the_cover(paired, storage, monkeypatch):
    _series(storage)
    job = _episode(storage, 9, approved=True, rendered=True)
    posts = [{"platform": "youtube", "kind": "long", "slot": time.time() + 3600}]
    ch = telegram.TelegramChannel()
    remote.deliver(remote.planned_outs(job, posts), ch)
    assert paired.photos == [] and paired.texts()[-1].startswith("📅 Đã lên lịch đăng")
    from PIL import Image

    pic = storage / "own.png"
    Image.new("RGB", (1280, 720), (0, 90, 200)).save(pic)
    thumbnail.use_image(job.work_dir, pic)
    remote.deliver(remote.planned_outs(job, posts), ch)
    (photo,) = paired.photos
    assert photo["caption"].startswith("📅 Đã lên lịch đăng: Chuyến Xe Tận Thế · Tập 9")
    assert "YouTube (video dài) lúc" in photo["caption"] and photo["photo"][0] == "thumb.jpg"
    telegram.set_notify("planned", False)
    remote.deliver(remote.planned_outs(job, posts), ch)
    assert len(paired.photos) == 1
    telegram.set_notify("planned", True)

    def refuse(*_a, **_k):
        raise telegram.TelegramError("Bad Request: IMAGE_PROCESS_FAILED")

    monkeypatch.setattr(telegram.TelegramChannel, "send_photo", refuse)
    n = len(paired.sent)
    remote.deliver(remote.planned_outs(job, posts), ch)
    assert len(paired.sent) == n + 1 and paired.texts()[-1].startswith("📅 Đã lên lịch đăng")


def test_titles_are_the_series_vietnamese_name(storage):
    _series(storage)
    job = _episode(storage, 9)
    assert remote.title_of(job) == "Chuyến Xe Tận Thế · Tập 9"
    md = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
    md["title"] += " · Short 1"
    (job.work_dir / "metadata.json").write_text(json.dumps(md), encoding="utf-8")
    assert remote.title_of(job) == "Chuyến Xe Tận Thế · Tập 9 · Short 1"
    autopilot.save_plan("末日校车", {**autopilot.load_plan("末日校车"), "series_name": ""})
    assert remote.title_of(job).startswith("第9集")


def test_unknown_commands_and_empty_messages_get_a_hint(paired):
    paired.say("/xoahet")
    paired.say("")
    _run(Host())
    cmd, empty = paired.texts()
    assert "chưa biết lệnh /xoahet" in cmd and "/help" in cmd
    assert "chữ, link và ảnh" in empty


# --- the scheduled task (app closed) ----------------------------------------------------------------

@pytest.fixture
def cli_env(paired, storage, monkeypatch, lister):
    from app import cli
    from app.providers import registry

    _series(storage)
    monkeypatch.setattr(registry, "list_providers", lambda: {
        "translation": [{"name": "passthrough"}, {"name": "openrouter", "can_refine": True}],
        "asr": [{"name": "audio_llm"}], "ocr": [{"name": "rapidocr"}], "tts": [{"name": "edge"}]})
    (storage / "ui_state.json").write_text(json.dumps({"translator": "openrouter"}),
                                           encoding="utf-8")
    runs: list[tuple[str, str]] = []
    during: list = []

    def fake_run_one(args, url):
        runs.append((url, args.series))
        if during:
            during.pop(0)()
        wd = Path(args.workdir)
        n = 14 - [v["url"] for v in PAGE].index(url)
        (wd / "metadata.json").write_text(json.dumps({"title": f"第{n}集 《末日校车》{n:02d}"}),
                                          encoding="utf-8")
        (wd / "vi.srt").write_text(f"1\n00:00:00,000 --> 00:00:01,000\nTập {n}.\n",
                                   encoding="utf-8")
        (wd / "series_link.json").write_text(json.dumps({"slug": "末日校车", "episode": n}),
                                             encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "_run_one", fake_run_one)
    monkeypatch.setattr(remote, "render_headless", lambda *a, **k: None)
    return cli, runs, during


def test_the_task_offers_a_page_then_runs_the_new_videos_on_the_press(cli_env, paired, storage,
                                                                      lister):
    cli, runs, during = cli_env
    lister[1][:] = PAGE[:3]                                   # episodes 14, 13, 12
    paired.say(MIX)
    assert cli.main(["autopilot", "--once"]) == 0
    reading, offer = paired.texts()
    assert reading.startswith("📋 Đang đọc") and "🆕 3 tập mới" in offer and runs == []
    go = paired.buttons(paired.sent[-1])[0]
    paired.sent.clear()
    ep = _episode(storage, 5)                                 # waiting at the gate already
    # the owner approves episode 5 while the first of the three runs
    during.append(lambda: paired.press(f"ok:{ep.id}:{approval.revision(ep)[:8]}"))
    paired.press(go)
    assert cli.main(["autopilot", "--once"]) == 0
    assert runs == [(PAGE[2]["url"], "末日校车"), (PAGE[1]["url"], "末日校车"),
                    (PAGE[0]["url"], "末日校车")]             # oldest first, the series
    texts = paired.texts()
    assert texts[0].startswith("📥 Đã nhận 3 tập") and "lần lượt" in texts[0]
    # the ✅ pressed while episode 12 ran is answered right after it, not after the batch
    done = next(i for i, t in enumerate(texts) if t.startswith("✅ Đã duyệt"))
    gates = [i for i, t in enumerate(texts) if t.startswith("🟡 Chờ duyệt")]
    assert approval.is_approved(ep) and len(gates) == 3 and gates[0] < done < gates[1]
    for d in storage.iterdir():
        if (d / "vi.srt").exists() and d.name != ep.id:
            assert not approval.is_approved(Job(id=d.name, work_dir=d))


def test_the_task_sets_a_cover_and_answers_cho(cli_env, paired, storage, youtube):
    cli, _runs, _during = cli_env
    ep = _episode(storage, 8, approved=True, rendered=True)
    _episode(storage, 9)
    _posts(ep)
    paired.photo(_jpeg())
    paired.say("/cho")
    assert cli.main(["autopilot", "--once"]) == 0
    ask = next(m for m in paired.sent if m["text"].startswith("🖼"))
    assert any(t.startswith("🟡 Chờ duyệt: Chuyến Xe Tận Thế · Tập 9") for t in paired.texts())
    paired.press(paired.buttons(ask)[0])
    assert cli.main(["autopilot", "--once"]) == 0
    assert thumbnail.user_chosen(ep.work_dir) and youtube.set[0][0] == "abcDEF12345"
    assert paired.photos[-1]["caption"].startswith("🖼 Đã đổi ảnh bìa")


def test_an_approved_short_is_rendered_as_a_short(storage, monkeypatch):
    """The bot sends a cut Short for its own approval when its lines differ; after the press
    it is rendered with the Short preset (not as a long episode)."""
    _series(storage)
    job = _episode(storage, 9)
    md = json.loads((job.work_dir / "metadata.json").read_text(encoding="utf-8"))
    md["short"] = {"parent": "ep09parent", "start": 10.0, "end": 50.0}
    (job.work_dir / "metadata.json").write_text(json.dumps(md), encoding="utf-8")
    assert remote.render_kind(job) == "short"
    monkeypatch.setattr(autopilot, "render_opts", lambda j, kind="long": {"kind": kind})
    host = remote.CliHost()
    approval.approve(job)
    assert "đang dựng" in host.after_approve(job) and host.to_render == [job.id]
    kinds = []

    def fake_render(j, kind="long", log=None):
        kinds.append(kind)
        (j.work_dir / "output.mp4").write_bytes(b"mp4")
        return j.work_dir / "output.mp4"

    class Off:
        def configured(self):
            return False

    remote.CliSession(ch=Off(), render=fake_render)._render(job.id)
    assert kinds == ["short"]
    assert [p["kind"] for p in autopilot.load_posts()] == [] or \
        all(p["kind"] == "short" for p in autopilot.load_posts())


def test_events_carry_a_file_for_photos():
    ev = Event("photo", file="F1", size=10)
    assert ev.kind == "photo" and ev.text == "" and ev.data == ""
