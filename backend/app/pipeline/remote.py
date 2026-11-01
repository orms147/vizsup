"""Điều khiển qua Telegram (docs/plan/J-publish-setup.md § Telegram): the owner's phone drives
the autopilot loop — send a link → it runs to the edit gate → the bot sends the «Duyệt nhanh»
content with [✅ Duyệt] [✏️ Để sửa trên máy] → after the press, the existing autopilot path
renders, cuts the Short, plans and publishes the posts, and the bot reports each step. The
same rules for the open app (desktop/remote_bridge.py) and for the scheduled task while the
app is closed (``CliSession``, run by ``vizsup autopilot --once``).

THE GATE STAYS THE OWNER'S CLICK. An episode is approved only by a [✅ Duyệt] press from the
paired chat; its payload carries the job id and the first 8 characters of
``approval.revision(job)`` taken when the message was built. ``handle`` refuses — and says
why — when the project is already approved, when the revision changed since (and sends the
new version instead), or when the host says no (the desktop: the episode is being processed;
the open editor shows it and saving it changed the text). Approval goes through
``approval.approve`` — the function the desktop uses — then the host's after-approve step.
Nothing here approves anything else, auto-approves, or renders an unapproved revision (the
render itself re-checks: app/pipeline/render.py).

More from the phone (none of it approves anything):
- **a page link** (Douyin 合集 / creator page, Bilibili space / list — batch_import.is_page):
  the page is listed off the GUI thread (``Host.work``), the videos NOT on the machine yet
  (batch_import.load) are offered with [▶️ Dịch N tập mới] [Bỏ qua]; the offer is kept in
  ``_remote/pending.json`` (a button carries only its key) and the press queues them through
  the host's link queue (``Host.queue_batch``) with the series the start page's picker would
  pick (batch_import.suggested_series, else "auto"). A single video link is one video, as before.
- **/cho**: the episodes waiting at the gate (``waiting``, minus the ones the host is still
  processing), oldest first, five per call — each its usual approval message + [✅ Duyệt].
- **a photo**: "which video?" with buttons (recent approved / rendered projects) → that
  project's cover (thumbnail.use_image: the owner's choice, never replaced automatically); a
  video already on YouTube gets it there too (``PublishProvider.set_thumbnail``).
- after a render, the planned posts come with the cover picture (``planned_outs``).

Only ONE process reads the bot's updates (Telegram answers a second getUpdates with 409):
``take_poll`` — the open app holds it, the scheduled task only polls when the app does not.
Each update is committed (offset saved) BEFORE it is acted on: processed once, at most.
Network sends never run on the GUI thread: ``handle`` returns ``Out`` items, ``deliver`` (or
the desktop's ``Outbox`` thread) sends them; blocking work (a page listing, a photo download)
goes through ``Host.work``.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.fsutil import atomic_write_text
from app.models import Job
from app.remote import Button, Event

STATE = Path("_remote") / "state.json"       # approval messages sent (job → revision); no secrets
PENDING = Path("_remote") / "pending.json"   # page offers + photos waiting for a press
PHOTOS = Path("_remote") / "photos"          # a photo from the phone, while it becomes a cover
POLL = Path("_remote") / "poll.json"         # who reads the bot's updates now
POLL_STALE_S = 120
REV = 8                                      # revision prefix carried by the ✅ button
CAP = 40                                     # flagged lines shown in one approval message
WAITING_DAYS = 14
CHO_MAX = 5                                  # approval messages per /cho
CHO_ROUND_S = 3600                           # /cho again within this: the next ones
LIST_MAX = 10                                # new videos of a page listed by name
OFFER_TTL_S = 3 * 86400
PHOTO_TTL_S = 86400
COVER_CHOICES = 8                            # projects offered for a photo
POLL_WAIT_S = 120                            # the task waits this long for a lost connection
RENDER_WAIT_S = 3 * 3600
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
_LINK = re.compile(r"https?://[^\s]*(?:douyin\.com|iesdouyin\.com|bilibili\.com|b23\.tv)[^\s]*",
                   re.I)
_TRAIL = ".,;)」】>\"'，。！？、"
PLATFORMS = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok",
             "folder": "Gói đăng (thư mục)"}
KINDS = {"long": "video dài", "short": "Short"}
POSTED = ("done", "published", "scheduled", "private", "draft")
CHANGED = ("Phụ đề đã thay đổi sau khi gửi tin này nên CHƯA duyệt — bản mới ở tin ngay dưới, "
           "đọc lại rồi bấm Duyệt ở đó.")
HELP = ("🤖 vizsup — điều khiển từ điện thoại\n\n"
        "• Gửi link một video Douyin / Bilibili (dán nguyên đoạn chia sẻ “长按复制…” cũng được): "
        "máy tải, làm phụ đề, dịch rồi gửi lại để bạn duyệt.\n"
        "• Gửi link 合集 / trang người đăng Douyin / danh sách Bilibili: bot kể các tập CHƯA có "
        "trên máy, bấm “Dịch N tập mới” là máy dịch lần lượt.\n"
        "• Tập tới bước duyệt: bot gửi các câu cần xem + nút ✅ Duyệt (duyệt là việc của bạn — "
        "không bao giờ tự duyệt) hoặc ✏️ Để sửa trên máy.\n"
        "• Gửi một tấm ảnh: chọn video để làm ảnh bìa (video đã lên YouTube thì đổi luôn ở đó).\n"
        "• Duyệt xong: bộ phim bật Tự động đăng thì máy tự dựng, cắt Short, lên lịch và đăng; "
        "bot gửi ảnh bìa + lịch đăng, rồi báo từng bài.\n\n"
        "/cho — video đang chờ duyệt (mỗi lần 5 video, kèm nút ✅ Duyệt)\n"
        "/trangthai — tập chờ duyệt, hàng dựng, lịch đăng 24 giờ, bài vừa đăng, lỗi\n"
        "/lich — kế hoạch đăng + các bài sắp đăng\n"
        "/gio 19:00 21:00 — đổi giờ đăng (mọi bộ phim đang tự đăng; bài chờ được xếp lại)\n"
        "/moingay 2 — số bài mỗi ngày cho mỗi nơi đăng\n"
        "/dangthu bat | tat — đăng thử: video để riêng tư\n"
        "/tamdung · /tieptuc — tạm dừng / tiếp tục đăng\n"
        "/help — hướng dẫn này")
# the "/" menu on the phone (setMyCommands) — the same commands, one line each
COMMANDS = (("cho", "Video chờ duyệt (kèm nút ✅ Duyệt)"),
            ("trangthai", "Chờ duyệt, đang dựng, lịch đăng, lỗi"),
            ("lich", "Kế hoạch đăng + bài sắp đăng"),
            ("gio", "Đổi giờ đăng, vd. /gio 19:00 21:00"),
            ("moingay", "Số bài mỗi ngày, vd. /moingay 2"),
            ("dangthu", "Đăng thử: /dangthu bat hoặc tat"),
            ("tamdung", "Tạm dừng đăng"),
            ("tieptuc", "Tiếp tục đăng"),
            ("help", "Hướng dẫn dùng bot"))
PAGE_NAMES = {"douyin_user": "Trang Douyin", "bilibili_space": "Trang Bilibili",
              "bilibili_season": "Tuyển tập Bilibili", "bilibili_series": "Danh sách Bilibili"}


# --- small files -------------------------------------------------------------------------------

def _root() -> Path:
    return Path(settings.vizsup_storage_dir)


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


_FILES = threading.Lock()      # the desktop changes these files from the GUI thread AND Host.work


def _change(rel: Path, fn: Callable[[dict], object]):
    """Read ``rel`` (under storage), let ``fn`` change the dict, write it back; ``fn``'s result."""
    with _FILES:
        d = _read(_root() / rel)
        res = fn(d)
        _write(_root() / rel, d)
        return res


def _stash(kind: str, item: dict, ttl: float, keep: int = 10) -> str:
    """Keep ``item`` under a new short key in pending.json[kind] (older than ``ttl`` dropped,
    the newest ``keep`` kept) → the key a button carries."""
    key = secrets.token_hex(3)

    def put(d: dict) -> None:
        now = time.time()
        items = {k: v for k, v in (d.get(kind) or {}).items()
                 if isinstance(v, dict) and now - float(v.get("at") or 0) < ttl}
        items[key] = {**item, "at": now}
        d[kind] = dict(sorted(items.items(), key=lambda kv: kv[1]["at"])[-keep:])

    _change(PENDING, put)
    return key


def _take(kind: str, key: str, ttl: float, *, keep: bool = False) -> dict | None:
    """The item a button's key names (None when unknown / expired); removed unless ``keep``."""
    def get(d: dict):
        items = d.get(kind) or {}
        item = items.get(key) if isinstance(items, dict) else None
        if not keep and isinstance(items, dict):
            items.pop(key, None)
        if not isinstance(item, dict) or time.time() - float(item.get("at") or 0) > ttl:
            return None
        return item

    return _change(PENDING, get) if re.fullmatch(r"[0-9a-f]{6}", key or "") else None


def channel():
    from app.remote import channel as _channel

    return _channel()


def paired() -> bool:
    from app.remote import telegram

    return telegram.paired()


# --- one reader of the bot's updates (the open app, else the scheduled task) ------------------

def poll_holder(now: float | None = None) -> dict | None:
    d = _read(_root() / POLL)
    now = time.time() if now is None else now
    return d if d and now - float(d.get("at") or 0) < POLL_STALE_S else None


def take_poll(who: str) -> bool:
    """True when this process may call getUpdates now (and marks it so). False while
    another live process holds it (its mark is refreshed at least every POLL_STALE_S)."""
    held = poll_holder()
    if held and held.get("pid") != os.getpid():
        return False
    _write(_root() / POLL, {"pid": os.getpid(), "who": who, "at": time.time()})
    return _read(_root() / POLL).get("pid") == os.getpid()


def release_poll() -> None:
    if _read(_root() / POLL).get("pid") == os.getpid():
        (_root() / POLL).unlink(missing_ok=True)


# --- which approval messages went out ----------------------------------------------------------

def was_sent(job_id: str, rev: str) -> bool:
    return (_read(_root() / STATE).get("sent") or {}).get(job_id, {}).get("rev") == rev


def mark_sent(job_id: str, rev: str, message_id) -> None:
    def put(st: dict) -> None:
        cutoff = time.time() - 60 * 86400
        sent = {k: v for k, v in (st.get("sent") or {}).items()
                if float((v or {}).get("at") or 0) > cutoff}
        sent[job_id] = {"rev": rev, "at": time.time(), "msg": message_id}
        st["sent"] = sent

    _change(STATE, put)


# --- projects ------------------------------------------------------------------------------------

def load_job(job_id: str) -> Job | None:
    """A project with subtitles, by id — None for anything else (never creates a folder:
    the id comes from a button payload)."""
    if not _JOB_ID.match(job_id or ""):
        return None
    d = _root() / job_id
    if not d.is_dir() or not (d / "vi.srt").exists():
        return None
    job = Job(id=job_id, work_dir=d)
    job.metadata = _read(d / "metadata.json")
    job.url = _read(d / "job.json").get("url")
    return job


def display_title(d: Path, limit: int = 80) -> str:
    """A project's name in the bot: "Chuyến Xe Tận Thế · Tập 9" when its series has a
    Vietnamese name (autopilot.friendly_title), else its title without hashtags."""
    from app.pipeline import autopilot
    from app.pipeline.compilation import _job_title, clean_title

    raw = _job_title(Path(d))
    return clean_title(autopilot.friendly_title(Path(d), raw) or raw, limit)


def title_of(job: Job) -> str:
    return display_title(job.work_dir, 80)


def rev_of(job: Job) -> str:
    from app.pipeline import approval

    return approval.revision(job)[:REV]


def find_link(text: str) -> str | None:
    """The first Douyin / Bilibili link in a message — a bare link or the apps' share text
    ("7.43 复制打开抖音… https://v.douyin.com/iR4x/ …"). Pure."""
    m = _LINK.search(text or "")
    return m.group(0).rstrip(_TRAIL) if m else None


def _projects():
    """(job.json's time, folder, job.json) of every project folder."""
    root = _root()
    if not root.exists():
        return
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        jf = d / "job.json"
        if jf.exists():
            yield jf.stat().st_mtime, d, _read(jf)


def _links(data: dict) -> set[str]:
    """The links a project was made from: job.json's ``url`` and its options' ``url``."""
    return {u for u in (str(data.get("url") or "").strip(),
                        str((data.get("opts") or {}).get("url") or "").strip()) if u}


def project_for_url(url: str) -> Job | None:
    """The newest project made from ``url`` (job.json's link), else None."""
    want = (url or "").strip()
    best: tuple[float, Path] | None = None
    if not want:
        return None
    for t, d, data in _projects():
        if want in _links(data) and (best is None or t > best[0]):
            best = (t, d)
    if best is None:
        return None
    d = best[1]
    job = Job(id=d.name, work_dir=d)
    job.metadata = _read(d / "metadata.json")
    return job


def project_index() -> dict[str, str]:
    """``project_for_url`` for a whole page of links at once (Dịch một loạt,
    pipeline/batch_import.py): every project's links, plus the Douyin / Bilibili video ids in
    them and in its metadata.json (a share link and a page's link of the same video), → the
    newest such project's folder name. One pass over storage."""
    from app.pipeline.watch import ids_in

    found: dict[str, tuple[float, str]] = {}
    for t, d, data in _projects():
        md = _read(d / "metadata.json")
        keys = _links(data)
        for v in (*keys, md.get("webpage_url"), md.get("video_id")):
            keys |= ids_in(v)
        for k in keys:
            if k not in found or t > found[k][0]:
                found[k] = (t, d.name)
    return {k: name for k, (_t, name) in found.items()}


# --- outgoing ------------------------------------------------------------------------------------

@dataclass
class Out:
    """One thing to send: "send" a message (``note`` = the notification toggle it belongs
    to, None = a direct answer, always sent), "photo" a picture (``photo``) with ``text`` as
    its caption — the plain message when the picture can't go out, "ack" a button press,
    "drop" its buttons, "commands" the "/" menu (COMMANDS)."""
    kind: str
    text: str = ""
    buttons: list | None = None
    event: Event | None = None
    note: str | None = None
    preview: bool = False
    sent: Callable[[int | None], None] | None = field(default=None, repr=False)
    photo: Path | None = None


def _say(text: str, note: str | None = None, **kw) -> Out:
    return Out("send", text, note=note, **kw)


def _send_one(ch, o: Out) -> None:
    if o.kind == "photo":
        if o.photo is not None and Path(o.photo).is_file() and hasattr(ch, "send_photo"):
            mid = ch.send_photo(Path(o.photo), o.text, o.buttons)
        else:                                    # the picture is gone: the words still go
            mid = ch.send(o.text, o.buttons, preview=o.preview)
        if o.sent is not None:
            o.sent(mid)
    elif o.kind == "send":
        mid = ch.send(o.text, o.buttons, preview=o.preview)
        if o.sent is not None:
            o.sent(mid)
    elif o.kind == "ack" and o.event is not None:
        ch.ack(o.event, o.text)
    elif o.kind == "drop" and o.event is not None:
        ch.drop_buttons(o.event)
    elif o.kind == "commands" and hasattr(ch, "set_commands"):
        ch.set_commands(list(COMMANDS))
        if o.sent is not None:
            o.sent(None)


def deliver(outs: list[Out], ch=None, *, log: Callable[[str], None] | None = None,
            sleep: Callable[[float], None] = time.sleep) -> int:
    """Send ``outs`` in order on the paired channel; returns how many went out. A refusal is
    logged and skipped (a refused picture goes out as its text); a connection error is
    retried twice, Telegram's flood limit waited out (≤ 30 s). Blocking — never call it on
    the GUI thread."""
    from app import net
    from app.remote.telegram import TelegramError, scrub

    say = log or (lambda _m: None)
    ch = ch if ch is not None else channel()
    if ch is None or not outs:
        return 0
    if not ch.paired():
        return 0
    n = 0
    for o in outs:
        if o.note and not ch.notify_on(o.note):
            continue
        for attempt in range(3):
            try:
                _send_one(ch, o)
                n += 1
                break
            except TelegramError as exc:
                if exc.retry_after and attempt < 2:
                    sleep(min(30.0, exc.retry_after))
                    continue
                say(f"Telegram từ chối ({o.kind}): {scrub(exc)}")
                if o.kind == "photo":            # the schedule / confirmation still matters
                    o = Out("send", o.text, o.buttons, note=o.note, sent=o.sent)
                    continue
                break
            except OSError as exc:               # the picture could not be read
                if o.kind == "photo" and not net.looks_like_network_error(exc):
                    say(f"Telegram: không đọc được ảnh ({type(exc).__name__})")
                    o = Out("send", o.text, o.buttons, note=o.note, sent=o.sent)
                    continue
                if attempt < 2 and net.looks_like_network_error(exc):
                    sleep(2.0 + 3.0 * attempt)
                    continue
                say(f"Telegram: không gửi được ({type(exc).__name__})")
                break
            except Exception as exc:  # noqa: BLE001 - a notification never breaks a run
                if attempt < 2 and net.looks_like_network_error(exc):
                    sleep(2.0 + 3.0 * attempt)
                    continue
                say(f"Telegram: không gửi được ({type(exc).__name__})")
                break
    return n


class Outbox:
    """The desktop's sender: one daemon thread delivering in order, so the GUI thread never
    waits on the network and messages keep their order."""

    def __init__(self, log: Callable[[str], None] | None = None, ch_factory=None) -> None:
        self._q: queue.Queue = queue.Queue()
        self._log = log
        self._factory = ch_factory or channel
        self._thread: threading.Thread | None = None
        self._closed = False

    def put(self, outs: list[Out]) -> None:
        if not outs or self._closed:
            return
        self._q.put(list(outs))
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="vizsup-remote-out",
                                            daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            outs = self._q.get()
            try:
                if outs is None:
                    return
                deliver(outs, self._factory(), log=self._log)
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                if self._log:
                    self._log(f"Telegram outbox: {type(exc).__name__}")
            finally:
                self._q.task_done()

    def wait(self, timeout: float = 10.0) -> bool:
        """True once everything put so far was delivered (tests)."""
        end = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < end:
            time.sleep(0.01)
        return not self._q.unfinished_tasks

    def close(self) -> None:
        self._closed = True
        self._q.put(None)


# --- messages (pure, given the files) -------------------------------------------------------------

def _mmss(sec: float) -> str:
    s = max(0, int(sec or 0))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _when(ts: float) -> str:
    return time.strftime("%H:%M %d/%m", time.localtime(ts))


def approval_text(title: str, cues: int, flagged: list[dict], after: str, *,
                  cap: int = CAP) -> str:
    """The «Duyệt nhanh» dialog as a message: title, line count, the flagged lines (time,
    Chinese, Vietnamese, why) — at most ``cap`` — and what approval sets off. Pure."""
    lines = [f"🟡 Chờ duyệt: {title}"]
    if flagged:
        lines.append(f"{cues} câu · {len(flagged)} câu được đánh dấu cần xem:")
        for row in flagged[:cap]:
            lines.append("")
            lines.append(f"#{row['n']} · {_mmss(row['start'])} · {', '.join(row['flags'])}")
            if row.get("zh"):
                lines.append(f"  Trung: {row['zh']}")
            lines.append(f"  Việt: {row.get('vi') or '(trống)'}")
        if len(flagged) > cap:
            lines += ["", f"… còn {len(flagged) - cap} câu nữa — mở trên máy để xem hết."]
    else:
        lines.append(f"{cues} câu · không có câu nào bị đánh dấu.")
    lines += ["", f"Sau khi duyệt: {after}"]
    return "\n".join(lines)


def approval_outs(job: Job, *, intro: str = "") -> list[Out]:
    """The approval message for ``job`` with its [✅ Duyệt] (job id + revision prefix NOW)
    and [✏️ Để sửa trên máy] buttons."""
    from app.pipeline import autopilot, quickreview
    from app.pipeline.srt import parse_srt

    try:
        flagged = quickreview.flagged_lines(job)
    except Exception:  # noqa: BLE001 - flags are a help; the owner can still open the editor
        flagged = []
    rev = rev_of(job)
    text = approval_text(title_of(job), len(parse_srt(job.vi_srt)), flagged,
                         autopilot.after_approve_text(job))
    buttons = [[Button("✅ Duyệt", f"ok:{job.id}:{rev}"),
                Button("✏️ Để sửa trên máy", f"ed:{job.id}")]]
    return [_say((intro + "\n\n" if intro else "") + text, buttons=buttons,
                 sent=lambda mid, j=job.id, r=rev: mark_sent(j, r, mid))]


def gate_outs(job: Job | None, *, force: bool = False, intro: str = "") -> list[Out]:
    """The approval message for an episode that reached the gate — once per revision (the
    toggle "Tập chờ duyệt"); nothing for an approved one."""
    from app.pipeline import approval

    if job is None or not job.vi_srt.exists() or approval.is_approved(job):
        return []
    if not force and was_sent(job.id, rev_of(job)):
        return []
    outs = approval_outs(job, intro=intro)
    for o in outs:
        o.note = "gate"
    return outs


def stage_outs(stage: str, job: Job | None) -> list[Out]:
    """Milestones of a link the owner sent (always sent — they asked)."""
    from app.pipeline.compilation import clean_title

    md = dict(job.metadata) if job is not None else {}
    title = clean_title(str(md.get("title")), 80) if md.get("title") else "video"
    if stage == "subtitle":
        plat = {"douyin": "Douyin", "bilibili": "Bilibili"}.get(md.get("platform") or "", "")
        dur = _mmss(float(md["duration"])) if md.get("duration") else ""
        info = " · ".join(x for x in (dur, plat) if x)
        return [_say(f"⬇️ Đã tải xong: {title}" + (f" ({info})" if info else "")
                     + " — đang lấy phụ đề tiếng Trung…")]
    if stage == "translate":
        return [_say(f"📝 Đã có phụ đề tiếng Trung: {title} — đang dịch sang tiếng Việt…")]
    if stage == "split":
        return [_say(f"✂️ {title}: video dài — đang chia thành các phần ~30 phút, phần nào xong "
                     f"sẽ gửi để bạn duyệt.")]
    return []


def failed_outs(what: str, msg: str, *, asked: bool = False) -> list[Out]:
    """A run that stopped: a direct answer when the owner sent that link, else the "Lỗi"
    notification."""
    from app.remote.telegram import scrub

    return [_say(f"❌ {what}: {scrub(msg)[:600]}", note=None if asked else "error")]


def planned_outs(job: Job, posts: list[dict]) -> list[Out]:
    """A render finished and its posts were queued: the cover (thumb.jpg) with the schedule as
    its caption — the plain message when there is no cover."""
    from app.pipeline import thumbnail

    if not posts:
        return []
    rows = "\n".join(f"• {PLATFORMS.get(p['platform'], p['platform'])} "
                     f"({KINDS.get(p['kind'], p['kind'])}) lúc {_when(p['slot'])}"
                     for p in sorted(posts, key=lambda p: p["slot"]))
    text = f"📅 Đã lên lịch đăng: {title_of(job)}\n{rows}"
    try:
        cover = thumbnail.cover(job.work_dir)
    except Exception:  # noqa: BLE001 - the schedule matters, the picture is a bonus
        cover = None
    if cover is not None:
        return [Out("photo", text + "\n(Ảnh bìa ở trên — muốn đổi thì gửi ảnh khác cho bot.)",
                    photo=cover, note="planned")]
    return [_say(text, note="planned")]


def posted_outs(post: dict | None) -> list[Out]:
    """A publisher's result (the post job after ``autopilot.record``)."""
    if not post:
        return []
    res = post.get("result") or {}
    name = PLATFORMS.get(post.get("platform") or "", post.get("platform") or "")
    kind = KINDS.get(post.get("kind") or "", post.get("kind") or "")
    d = _root() / str(post.get("job") or "")
    title = display_title(d, 80) if post.get("job") and d.is_dir() else str(post.get("job"))
    if post.get("status") == "scheduled":
        when = time.strftime("%H:%M %d/%m", time.localtime(float(post.get("slot") or 0)))
        text = f"⏰ {name}: đã tải lên {title} ({kind}) — tự công khai lúc {when}"
        if res.get("url"):
            text += f"\n{res['url']}"
        return [_say(text, note="posted", preview=True)]
    if post.get("status") == "private":
        text = f"🔒 {name}: đã tải lên {title} ({kind}) ở chế độ riêng tư"
        if res.get("url"):
            text += f"\n{res['url']}"
        if res.get("message"):
            text += f"\n{res['message']}"
        return [_say(text, note="posted", preview=True)]
    if post.get("status") in POSTED:
        text = f"✅ {name}: đã đăng {title} ({kind})"
        if res.get("url"):
            text += f"\n{res['url']}"
        if res.get("message"):
            text += f"\n{res['message']}"
        return [_say(text, note="posted", preview=True)]
    again = " — thử lại sau 1 giờ" if post.get("status") == "pending" else ""
    return [_say(f"❌ {name}: chưa đăng được {title} ({kind}): "
                 f"{res.get('message') or 'lỗi không rõ'}{again}", note="error")]


def waiting(now: float | None = None) -> list[Job]:
    """Projects at the gate (subtitles, not approved), newest first, from the last
    WAITING_DAYS days."""
    from app.pipeline import approval

    root = _root()
    now = time.time() if now is None else now
    out: list[tuple[float, Job]] = []
    if not root.exists():
        return []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith(("_", ".")) or not (d / "job.json").exists():
            continue
        vi = d / "vi.srt"
        if not vi.exists() or (d / "group.json").exists() or (d / "compilation.json").exists():
            continue
        t = vi.stat().st_mtime
        if now - t > WAITING_DAYS * 86400:
            continue
        job = load_job(d.name)
        if job is not None and not approval.is_approved(job):
            out.append((t, job))
    return [j for _t, j in sorted(out, key=lambda x: x[0], reverse=True)]


# --- /cho: the gate, from the phone ----------------------------------------------------------------

def waiting_outs(host: Host, *, now: float | None = None) -> list[Out]:
    """/cho: the episodes waiting at the gate — oldest first (they are posted in the order they
    are approved), the host's running ones left out — each with its approval message and
    [✅ Duyệt]; CHO_MAX per call, the next call within CHO_ROUND_S goes on with the rest."""
    now = time.time() if now is None else now
    wait = [j for j in reversed(waiting(now)) if not host.busy(j.id)]
    if not wait:
        return [_say("🟢 Không có video nào chờ duyệt. Gửi link video hoặc link 合集 để dịch "
                     "thêm.")]
    st = _read(_root() / STATE).get("cho") or {}
    shown = set(st.get("shown") or []) if now - float(st.get("at") or 0) < CHO_ROUND_S else set()
    rest = [j for j in wait if j.id not in shown]
    if not rest:                                   # everything was shown: from the start again
        rest, shown = wait, set()
    batch = rest[:CHO_MAX]
    more = len(rest) - len(batch)

    def put(d: dict) -> None:
        d["cho"] = {"shown": sorted(shown | {j.id for j in batch}), "at": now}

    _change(STATE, put)
    head = (f"🟡 {len(wait)} video chờ duyệt" + (f" — đây là {len(batch)} video:" if more or shown
                                                 else ":")
            + "\nĐọc từng tin rồi bấm ✅ Duyệt (hoặc ✏️ để sửa trên máy).")
    outs = [_say(head)]
    for job in batch:
        outs += approval_outs(job)
    if more:
        outs.append(_say(f"… còn {more} video, gửi /cho lần nữa."))
    return outs


# --- a page of videos (合集 / creator page / Bilibili list) ----------------------------------------

def page_outs(url: str, res: dict) -> list[Out]:
    """What ``batch_import.load(url)`` found, as the offer: the page, how many are on the
    machine, the NEW ones (oldest first, LIST_MAX by name), the series they will join, and
    [▶️ Dịch N tập mới] [Bỏ qua]. The offer (its videos in the page's order, the series) is
    kept in pending.json — the button carries its key."""
    from app.pipeline import autopilot, batch_import, series
    from app.pipeline.compilation import clean_title

    if res.get("error"):
        return [_say(f"⚠ Chưa đọc được danh sách video: {res['error']}")]
    videos = [v for v in res.get("videos") or [] if v.get("url")]
    new = [v for v in videos if not v.get("have")]
    mix = res.get("mix") or {}
    head = (f"📋 合集 “{mix['name']}”" if mix.get("name")
            else "📋 " + PAGE_NAMES.get(res.get("kind") or "", "Trang này"))
    head += f": {len(videos)} video"
    if len(videos) - len(new):
        head += f" · {len(videos) - len(new)} đã có trên máy"
    if not new:
        return [_say(head + "\n\nKhông có tập mới — mọi video đều đã có trên máy. Gửi /cho để "
                            "xem video chờ duyệt.")]
    order = batch_import.oldest_first(new)
    lines = [head, "", f"🆕 {len(new)} tập mới (dịch từ tập cũ nhất):"]
    lines += [f"• {clean_title(str(v.get('title') or v.get('id') or v['url']), 70)}"
              for v in order[:LIST_MAX]]
    if len(order) > LIST_MAX:
        lines.append(f"… và {len(order) - LIST_MAX} tập nữa")
    try:
        slug = batch_import.suggested_series(videos) or "auto"
        prof = series.load(slug) if slug != "auto" else None
    except Exception:  # noqa: BLE001 - a series is a help: each video finds its own
        slug, prof = "auto", None
    lines.append("")
    if prof is not None:
        lines.append(f"Bộ phim: 《{prof.name}》 · {autopilot.summary(prof.slug)}")
    else:
        lines.append("Bộ phim: mỗi video tự nhận theo tên 《…》 trong tiêu đề.")
    if res.get("kind") == "douyin_user":
        lines.append("(Trang người đăng Douyin chỉ hiện ~18–36 video mới nhất khi chưa đăng nhập "
                     "— gửi link 合集 để có đủ các tập.)")
    lines.append("Bấm Dịch: máy tải và dịch lần lượt, tập nào xong sẽ gửi để bạn duyệt — không "
                 "tự duyệt tập nào.")
    keep = ("url", "id", "title", "published")
    key = _stash("offers", {"url": url, "series": slug,
                            "videos": [{k: v.get(k) for k in keep} for v in new]}, OFFER_TTL_S)
    return [_say("\n".join(lines), buttons=[[Button(f"▶️ Dịch {len(new)} tập mới", f"bt:{key}"),
                                             Button("Bỏ qua", f"bx:{key}")]])]


def list_page(url: str, lister=None) -> list[Out]:
    """List ``url`` and build the offer (blocking: a Douyin page opens the embedded browser
    for a minute or two — call it through ``Host.work``). Never raises."""
    from app.pipeline import batch_import

    try:
        return page_outs(url, batch_import.load(url, lister=lister))
    except Exception as exc:  # noqa: BLE001 - the owner gets a sentence, not a traceback
        from app.remote.telegram import scrub

        return [_say(f"⚠ Chưa đọc được danh sách video ({type(exc).__name__}: "
                     f"{scrub(exc)[:200]}).")]


# --- the owner's cover from the phone ---------------------------------------------------------------

def _fold(text: str) -> set[str]:
    """The words of ``text`` without accents, lower case ("Tập 9" → {"tap", "9"}). Pure."""
    t = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return set(re.findall(r"\w+", t))


def _youtube_posts(job_id: str, posts: list[dict] | None = None) -> list[dict]:
    """This project's posts already on a platform that can change a posted video's cover."""
    return [p for p in (posts if posts is not None else _load_posts())
            if p.get("job") == job_id and p.get("status") in POSTED
            and (p.get("result") or {}).get("id") and _cover_settable(p.get("platform"))]


def _load_posts() -> list[dict]:
    from app.pipeline import autopilot

    try:
        return autopilot.load_posts()
    except Exception:  # noqa: BLE001 - a broken posts file: nothing posted as far as we know
        return []


def _cover_settable(platform) -> bool:
    from app.providers.registry import get_publisher

    try:
        return bool(getattr(get_publisher(str(platform or "")), "can_set_thumbnail", False))
    except KeyError:
        return False


def cover_choices(caption: str = "", *, limit: int = COVER_CHOICES) -> list[tuple[Job, bool]]:
    """(project, already on YouTube) the owner may give a cover: approved or rendered ones,
    newest first — the ones whose name shares words with the photo's ``caption`` first."""
    rows: list[tuple[float, Path]] = []
    for _t, d, _data in _projects():
        if (d / "group.json").exists() or (d / "compilation.json").exists():
            continue
        stamps = [p for p in (d / "output.mp4", d / "approved.json") if p.exists()]
        if stamps and (d / "vi.srt").exists():
            rows.append((max(p.stat().st_mtime for p in stamps), d))
    rows.sort(key=lambda r: r[0], reverse=True)
    rows = rows[:40]
    want = _fold(caption)
    if want:
        from app.pipeline.compilation import _job_title

        rows.sort(key=lambda r: -len(want & _fold(f"{display_title(r[1], 200)} {_job_title(r[1])}")))
    posts = _load_posts()
    out = []
    for _t, d in rows[:limit]:
        job = load_job(d.name)
        if job is not None:
            out.append((job, bool(_youtube_posts(job.id, posts))))
    return out


def photo_outs(ev: Event) -> list[Out]:
    """A photo from the owner: which project gets it as its cover (buttons)."""
    from app.remote.telegram import MAX_FILE

    if ev.size and ev.size > MAX_FILE:
        return [_say("Ảnh này lớn quá (trên 20 MB) — Telegram không cho bot tải. Gửi lại dạng "
                     "ảnh thường (không chọn “gửi dạng tệp”).")]
    choices = cover_choices(ev.text)
    if not choices:
        return [_say("🖼 Đã nhận ảnh, nhưng trên máy chưa có video nào đã duyệt hoặc đã dựng để "
                     "làm ảnh bìa.")]
    key = _stash("photos", {"file": ev.file}, PHOTO_TTL_S, keep=5)
    rows = [[Button(("📺 " if on_yt else "") + display_title(job.work_dir, 48),
                    f"cv:{key}:{job.id}")] for job, on_yt in choices]
    rows.append([Button("Bỏ qua", f"cx:{key}")])
    text = "🖼 Ảnh này làm ảnh bìa cho video nào?"
    if any(on_yt for _j, on_yt in choices):
        text += "\n📺 = đã lên YouTube — ảnh bìa ở đó cũng được đổi luôn."
    return [_say(text, buttons=rows)]


def set_cover(job: Job, file_id: str, ch=None) -> list[Out]:
    """Download the owner's photo, make it ``job``'s cover (the owner's choice: never replaced
    by the automatic one) and put it on the YouTube video when it is already up. Blocking —
    through ``Host.work``. Never raises: every failure is a sentence."""
    from app.pipeline import thumbnail
    from app.remote.telegram import TelegramError, scrub

    ch = ch if ch is not None else channel()
    if ch is None:
        return [_say("⚠ Bot chưa kết nối trên máy — chưa đặt được ảnh bìa.")]
    tmp = _root() / PHOTOS / f"telegram-{secrets.token_hex(4)}.img"
    try:
        ch.download(file_id, tmp)
        thumb = thumbnail.use_image(job.work_dir, tmp)
    except TelegramError as exc:
        return [_say(f"⚠ Chưa tải được ảnh từ Telegram ({scrub(exc)[:160]}) — gửi lại ảnh nhé.")]
    except ImportError:
        return [_say("⚠ Máy thiếu thư viện xử lý ảnh (Pillow) — chưa đặt được ảnh bìa.")]
    except Exception as exc:  # noqa: BLE001 - offline, not a picture Pillow knows …
        from app import net

        if net.looks_like_network_error(exc):
            return [_say("⚠ Mất kết nối khi tải ảnh — gửi lại ảnh khi mạng ổn nhé.")]
        return [_say(f"⚠ Không mở được ảnh này ({type(exc).__name__}) — gửi ảnh JPG / PNG "
                     f"khác nhé.")]
    finally:
        tmp.unlink(missing_ok=True)
    lines = [f"🖼 Đã đổi ảnh bìa: {title_of(job)}", *cover_notes(job, thumb)]
    return [Out("photo", "\n".join(lines), photo=thumb)]


def cover_notes(job: Job, thumb: Path) -> list[str]:
    """Where the new cover goes: set on the videos already up (when the platform can), the
    posts still waiting, or "the next post"."""
    from app.providers.registry import get_publisher

    posts = _load_posts()
    lines = []
    for p in _youtube_posts(job.id, posts):
        name = PLATFORMS.get(p["platform"], p["platform"])
        res = p.get("result") or {}
        try:
            pub = get_publisher(p["platform"])
            if not pub.connected():
                lines.append(f"{name}: chưa kết nối trên máy — chưa đổi được ảnh bìa của video "
                             f"đã tải lên; ảnh sẽ dùng cho lần đăng tới.")
                continue
            got = pub.set_thumbnail(str(res.get("id")), thumb)
        except Exception as exc:  # noqa: BLE001 - the cover is set here anyway
            lines.append(f"⚠ {name}: chưa đổi được ảnh bìa ({type(exc).__name__}).")
            continue
        url = res.get("url") or ""
        if got.ok:
            lines.append(f"✅ {name}: đã thay ảnh bìa ({KINDS.get(p.get('kind'), p.get('kind'))})"
                         + (f" — {url}" if url else ""))
        else:
            lines.append(f"⚠ {name}: {got.message or 'chưa đổi được ảnh bìa'}")
    later = sorted((p for p in posts if p.get("job") == job.id
                    and p.get("status") in ("pending", "running")), key=lambda p: p["slot"])
    if later:
        lines.append("Ảnh sẽ dùng khi đăng: " + ", ".join(
            f"{PLATFORMS.get(p['platform'], p['platform'])} lúc {_when(p['slot'])}"
            for p in later[:4]) + ".")
    if not lines:
        lines.append("Ảnh sẽ dùng cho lần đăng tới.")
    return lines


def schedule_text(*, now: float | None = None) -> str:
    """/lich: each series' posting plan and the next posts (7 days)."""
    from app.pipeline import autopilot

    now = time.time() if now is None else now
    lines = ["📅 Lịch đăng" + (" — ⏸ ĐANG TẠM DỪNG (/tieptuc để đăng lại)"
                               if autopilot.paused() else "")]
    plans = autopilot.plans_on()
    if not plans:
        lines.append("Chưa bộ phim nào bật Tự động đăng (trên máy: Thư viện → Tự động đăng).")
    for slug, plan in plans.items():
        name = (plan.get("series_name") or "").strip() or slug
        lines.append(f"• {name}: {autopilot.summary(slug).removeprefix('Tự đăng: ')}")
    soon = sorted((p for p in autopilot.load_posts()
                   if p.get("status") in ("pending", "running", "scheduled")
                   and now - 3600 <= float(p.get("slot") or 0) <= now + 7 * 86400),
                  key=lambda p: p["slot"])
    lines.append("")
    if soon:
        lines.append(f"Sắp đăng ({len(soon)}):")
        for p in soon[:12]:
            d = _root() / str(p.get("job") or "")
            title = display_title(d, 50) if d.is_dir() else str(p.get("job"))
            mark = "⏰ đã tải lên" if p.get("status") == "scheduled" else "chờ"
            lines.append(f"• {_when(p['slot'])} · {PLATFORMS.get(p['platform'], p['platform'])} "
                         f"· {KINDS.get(p['kind'], p['kind'])} · {title} ({mark})")
    else:
        lines.append("Chưa có bài nào sắp đăng.")
    return "\n".join(lines)


def schedule_command(cmd: str, args: list[str]) -> str:
    """/gio /moingay /dangthu /tamdung /tieptuc — change the posting schedule from the phone.
    Never approves or publishes anything by itself."""
    from app.pipeline import autopilot

    if cmd == "/lich":
        return schedule_text()
    if cmd == "/tamdung":
        autopilot.set_paused(True)
        return ("⏸ Đã tạm dừng đăng. Bài đã tải lên YouTube/Facebook ở chế độ hẹn giờ vẫn tự "
                "công khai đúng giờ (đổi trong YouTube Studio nếu cần). /tieptuc để đăng lại.")
    if cmd == "/tieptuc":
        autopilot.set_paused(False)
        moved = autopilot.reschedule()
        return "▶️ Đăng lại bình thường" + (f" — xếp lại {moved} bài chờ vào giờ tới." if moved
                                            else ".")
    if not autopilot.plans_on():
        return "Chưa bộ phim nào bật Tự động đăng (trên máy: Thư viện → Tự động đăng)."
    if cmd == "/gio":
        times = autopilot.parse_times(args)
        if not times:
            return "Gửi giờ theo dạng: /gio 19:00 hoặc /gio 12:00 19:30"
        names = autopilot.change_plans(times=times)
        return f"🕖 Giờ đăng mới: {', '.join(times)} — {', '.join(names)}. Bài chờ đã xếp lại."
    if cmd == "/moingay":
        if not args or not args[0].isdigit() or not 1 <= int(args[0]) <= 6:
            return "Gửi số bài mỗi ngày (1–6), vd. /moingay 2"
        names = autopilot.change_plans(per_day=int(args[0]))
        return f"📆 {args[0]} bài/ngày cho mỗi nơi đăng — {', '.join(names)}. Bài chờ đã xếp lại."
    if cmd == "/dangthu":
        word = (args[0].lower() if args else "")
        if word not in ("bat", "bật", "on", "tat", "tắt", "off"):
            return "Gửi /dangthu bat (video để riêng tư) hoặc /dangthu tat (đăng công khai)"
        on = word in ("bat", "bật", "on")
        names = autopilot.change_plans(private=on)
        return ("🔒 Đăng thử BẬT — bài mới để riêng tư" if on else
                "🌐 Đăng thử TẮT — bài mới đăng công khai theo lịch") + f" — {', '.join(names)}."
    return HELP


def status_text(renders: list[str] | None = None, *, now: float | None = None) -> str:
    """/trangthai: episodes waiting for approval, the render queue, the next 24 h of posts,
    the last five published, failures."""
    from app.pipeline import autopilot
    from app.pipeline.compilation import clean_title

    now = time.time() if now is None else now
    lines = [f"📊 vizsup · {time.strftime('%H:%M %d/%m', time.localtime(now))}"]
    wait = waiting(now)
    lines.append("")
    if wait:
        lines.append(f"🟡 Chờ duyệt ({len(wait)}):")
        lines += [f"• {title_of(j)}" for j in wait[:8]]
        if len(wait) > 8:
            lines.append(f"… và {len(wait) - 8} tập nữa")
        lines.append("→ gửi /cho để duyệt ngay trên điện thoại")
    else:
        lines.append("🟡 Không có tập nào chờ duyệt.")
    lines.append("")
    if renders:
        lines.append(f"🎬 Đang dựng / chờ dựng ({len(renders)}):")
        lines += [f"• {clean_title(t, 80)}" for t in renders[:8]]
    else:
        lines.append("🎬 Không có video nào đang dựng.")

    def name(p: dict) -> str:
        d = _root() / str(p.get("job") or "")
        return display_title(d, 60) if d.is_dir() else str(p.get("job"))

    def where(p: dict) -> str:
        return (f"{PLATFORMS.get(p.get('platform'), p.get('platform'))} · "
                f"{KINDS.get(p.get('kind'), p.get('kind'))}")

    posts = autopilot.load_posts()
    soon = sorted((p for p in posts if p.get("status") in ("pending", "running")
                   and float(p.get("slot") or 0) <= now + 86400), key=lambda p: p["slot"])
    lines.append("")
    if soon:
        lines.append(f"📅 Sắp đăng trong 24 giờ ({len(soon)}):")
        lines += [f"• {_when(p['slot']) if p['slot'] > now else 'ngay bây giờ'} · {where(p)} · "
                  f"{name(p)}" for p in soon[:10]]
    else:
        lines.append("📅 Không có bài nào sắp đăng trong 24 giờ.")
    done = sorted((p for p in posts if p.get("status") in POSTED and p.get("result")),
                  key=lambda p: float((p.get("result") or {}).get("at") or 0), reverse=True)[:5]
    if done:
        lines += ["", "✅ Đăng gần đây:"]
        for p in done:
            url = (p.get("result") or {}).get("url") or ""
            lines.append(f"• {where(p)} · {name(p)}" + (f"\n  {url}" if url else ""))
    failed = [p for p in posts if p.get("status") == "failed"][-5:]
    if failed:
        lines += ["", "❌ Đăng lỗi:"]
        lines += [f"• {where(p)} · {name(p)}: {((p.get('result') or {}).get('message') or '')[:160]}"
                  for p in failed]
    return "\n".join(lines)


# --- what an owner's message means ------------------------------------------------------------

class Host:
    """What the app (or the scheduled task) does for the controller. The default answers
    are the scheduled task's (app closed)."""

    def queue_link(self, url: str, job_id: str | None) -> str:          # pragma: no cover
        raise NotImplementedError

    def queue_batch(self, videos: list[dict], series: str) -> str:      # pragma: no cover
        """Queue the new videos of a page (page order; the host runs them oldest first,
        batch_import.oldest_first) with ``series`` (a slug, "auto" or "none") → the answer."""
        raise NotImplementedError

    def link_busy(self, url: str) -> str | None:
        return None

    def busy(self, job_id: str) -> str | None:
        """Why the project is not ready to approve (being processed / rendered), else None."""
        return None

    def before_approve(self, job: Job) -> str | None:
        """Why ``job`` must not be approved now (None = go on). The desktop saves the open
        editor here; the revision is checked again right after."""
        return None

    def after_approve(self, job: Job) -> str:                           # pragma: no cover
        raise NotImplementedError

    def renders(self) -> list[str]:
        return []

    def work(self, fn: Callable[[], list[Out]]) -> list[Out]:
        """Blocking work (a page listing, a photo download) → its messages. Here: right
        away; the desktop runs it on a thread and sends what it returns when done ([] now)."""
        return fn()


def check_approve(job_id: str, rev: str) -> tuple[Job | None, str | None]:
    """(job, None) when a [✅ Duyệt] press for ``job_id`` at revision prefix ``rev`` may
    approve; (job or None, why) otherwise."""
    from app.pipeline import approval

    job = load_job(job_id)
    if job is None:
        return None, "Không tìm thấy tập này trên máy (đã xóa, hoặc chưa có phụ đề)."
    if approval.is_approved(job):
        return job, "Tập này đã được duyệt rồi — không duyệt lại."
    if not rev or rev_of(job) != rev:
        return job, CHANGED
    return job, None


def _commands_sig() -> str:
    return hashlib.sha1(json.dumps(COMMANDS, ensure_ascii=False).encode()).hexdigest()[:10]


def commands_outs(*, force: bool = False) -> list[Out]:
    """The "/" menu (setMyCommands) — once per version of COMMANDS, or ``force`` (pairing)."""
    sig = _commands_sig()
    if not force and _read(_root() / STATE).get("commands") == sig:
        return []

    def done(_mid, s=sig) -> None:
        _change(STATE, lambda d: d.__setitem__("commands", s))

    return [Out("commands", sent=done)]


def handle(ev: Event, host: Host) -> list[Out]:
    if ev.kind == "paired":
        who = f" của {ev.who}" if ev.who else ""
        return [*commands_outs(force=True),
                _say(f"✅ Đã ghép bot với vizsup trên máy{who}. Từ giờ bot chỉ trả lời đoạn chat "
                     f"này.\n\n{HELP}")]
    if ev.kind == "button":
        return _button(ev, host)
    if ev.kind == "photo":
        return photo_outs(ev)
    if ev.kind == "text":
        return _text(ev, host)
    return []


def _text(ev: Event, host: Host) -> list[Out]:
    t = (ev.text or "").strip()
    cmd = t.split()[0].lower().split("@")[0] if t.startswith("/") else ""
    if cmd in ("/start", "/help", "/trogiup", "/huongdan"):
        return [_say(HELP)]
    if cmd in ("/cho", "/choduyet"):
        return waiting_outs(host)
    if cmd in ("/trangthai", "/status"):
        return [_say(status_text(host.renders()))]
    if cmd in ("/lich", "/gio", "/moingay", "/dangthu", "/tamdung", "/tieptuc"):
        return [_say(schedule_command(cmd, t.split()[1:]))]
    url = find_link(t)
    if url:
        return _link(url, host)
    if cmd:
        return [_say(f"Bot chưa biết lệnh {cmd[:40]}. Gửi /help để xem các lệnh.")]
    if not t:
        return [_say("Bot chỉ hiểu chữ, link và ảnh. Gửi link video / 合集 Douyin, Bilibili, hoặc "
                     "/help.")]
    return [_say("Mình chưa hiểu tin này. Gửi link video Douyin / Bilibili (dán nguyên đoạn chia "
                 "sẻ cũng được), link 合集 để dịch nhiều tập, hoặc /cho, /trangthai, /help.")]


def _link(url: str, host: Host) -> list[Out]:
    from app.pipeline import approval, batch_import, watch

    if batch_import.is_page(url):              # a 合集 / creator page / Bilibili list
        lister = watch.list_videos             # picked here (the GUI thread), used by the work
        douyin = "douyin" in url.lower()
        return [_say("📋 Đang đọc danh sách video của trang này"
                     + (" (mở Douyin trong trình duyệt nhúng — 1–2 phút)" if douyin else "")
                     + "…"),
                *host.work(lambda: list_page(url, lister))]
    busy = host.link_busy(url)
    if busy:
        return [_say(busy)]
    job = project_for_url(url)
    if job is not None and job.vi_srt.exists():
        title = title_of(job)
        if approval.is_approved(job):
            done = " và đã dựng" if (job.work_dir / "output.mp4").exists() else ""
            return [_say(f"Link này đã làm rồi: {title} — đã duyệt{done}. /trangthai để xem lịch "
                         f"đăng.")]
        return approval_outs(job, intro=f"Link này đã có trên máy: {title} — đang chờ bạn duyệt.")
    return [_say(host.queue_link(url, job.id if job is not None else None))]


def _button(ev: Event, host: Host) -> list[Out]:
    parts = (ev.data or "").split(":")
    if parts[0] == "ok" and len(parts) == 3:
        return _approve(ev, parts[1], parts[2], host)
    if parts[0] == "bt" and len(parts) == 2:
        return _batch(ev, parts[1], host)
    if parts[0] == "bx" and len(parts) == 2:
        _take("offers", parts[1], OFFER_TTL_S)
        return [Out("ack", "Đã bỏ qua", event=ev), Out("drop", event=ev)]
    if parts[0] == "cv" and len(parts) == 3:
        return _cover(ev, parts[1], parts[2], host)
    if parts[0] == "cx" and len(parts) == 2:
        _take("photos", parts[1], PHOTO_TTL_S)
        return [Out("ack", "Đã bỏ qua ảnh này", event=ev), Out("drop", event=ev)]
    if parts[0] == "ed" and len(parts) == 2:
        job = load_job(parts[1])
        title = title_of(job) if job is not None else "tập này"
        return [Out("ack", "Để dành sửa trên máy", event=ev), Out("drop", event=ev),
                _say(f"✏️ Để dành {title} sửa trên máy: mở vizsup → Thư viện (hoặc Tiếp tục dự "
                     f"án), sửa rồi bấm Duyệt và dựng. Tập này KHÔNG tự duyệt.")]
    return [Out("ack", "Nút này không còn dùng được.", event=ev)]


def _batch(ev: Event, key: str, host: Host) -> list[Out]:
    """[▶️ Dịch N tập mới]: the offer's videos still not on the machine (nor in the host's
    queue) go to the host's link queue with the offer's series. Nothing is approved."""
    from app.pipeline import batch_import, series

    offer = _take("offers", key, OFFER_TTL_S)
    if offer is None:
        return [Out("ack", "Danh sách này đã cũ", event=ev), Out("drop", event=ev),
                _say("Danh sách này đã hết hạn hoặc đã bấm rồi — gửi lại link trang để xem lại "
                     "các tập mới.")]
    try:
        index = project_index()
    except OSError:
        index = {}
    fresh = [v for v in batch_import.mark_local(list(offer.get("videos") or []), index)
             if v.get("url") and not v.get("have") and not host.link_busy(v["url"])]
    if not fresh:
        return [Out("ack", "Không còn tập mới", event=ev), Out("drop", event=ev),
                _say("Các tập này đã có trên máy hoặc đang chờ xử lý rồi — không cần dịch lại. "
                     "Gửi /cho để xem video chờ duyệt.")]
    slug = str(offer.get("series") or "auto")
    if slug not in ("auto", "none") and series.load(slug) is None:
        slug = "auto"                              # the series was deleted meanwhile
    videos = [{k: v.get(k) for k in ("url", "id", "title", "published")} for v in fresh]
    text = host.queue_batch(videos, slug)
    return [Out("ack", f"Đã xếp {len(fresh)} tập", event=ev), Out("drop", event=ev), _say(text)]


def _cover(ev: Event, key: str, job_id: str, host: Host) -> list[Out]:
    """A project picked for the photo: download it and make it the cover (through
    ``Host.work``: the network never runs on the GUI thread)."""
    job = load_job(job_id)
    if job is None:
        return [Out("ack", "Không thấy video này", event=ev),
                _say("Không tìm thấy video này trên máy (đã xóa?) — chọn video khác.")]
    photo = _take("photos", key, PHOTO_TTL_S)
    if photo is None or not photo.get("file"):
        return [Out("ack", "Ảnh đã cũ", event=ev), Out("drop", event=ev),
                _say("Ảnh này đã quá hạn hoặc đã dùng rồi — gửi lại ảnh nhé.")]
    file_id = str(photo["file"])
    return [Out("ack", "Đang đặt ảnh bìa…", event=ev), Out("drop", event=ev),
            *host.work(lambda: set_cover(job, file_id))]


def _approve(ev: Event, job_id: str, rev: str, host: Host) -> list[Out]:
    """The ONLY approval path of the remote: the owner's press, revision-guarded."""
    from app.pipeline import approval
    from app.pipeline.srt import parse_srt

    job, why = check_approve(job_id, rev)
    if why is None:
        why = host.before_approve(job)
        if why is None and rev_of(job) != rev:        # the open editor's edits were just saved
            why = CHANGED
    if why is not None:
        outs = [Out("ack", why[:190], event=ev), _say("⛔ " + why)]
        if job is not None and approval.is_approved(job):
            outs.append(Out("drop", event=ev))
        elif job is not None and why == CHANGED:
            outs += [Out("drop", event=ev), *approval_outs(job)]
        return outs
    approval.approve(job, cues=len(parse_srt(job.vi_srt)))
    try:
        after = host.after_approve(job)
    except Exception as exc:  # noqa: BLE001 - approved already: say so, never "nothing done"
        from app.remote.telegram import scrub

        after = (f"⚠ Nhưng chưa tự dựng được ({type(exc).__name__}: {scrub(exc)[:200]}) — mở "
                 f"vizsup trên máy để dựng.")
    return [Out("ack", "Đã duyệt", event=ev), Out("drop", event=ev),
            _say(f"✅ Đã duyệt: {title_of(job)}\n{after}")]


def process(updates: list[dict], host: Host, ch, *,
            log: Callable[[str], None] | None = None) -> list[Out]:
    """Commit, authenticate and handle a batch of raw updates; returns what to send."""
    from app.remote.telegram import scrub

    say = log or (lambda _m: None)
    outs: list[Out] = []
    owner = False
    for raw in updates:
        ch.commit(ch.update_id(raw))           # first: an update is acted on once, at most
        ev = ch.accept(raw)
        if ev is None:
            continue
        owner = True
        try:
            outs += handle(ev, host)
        except Exception as exc:  # noqa: BLE001 - one bad message must not stop the others
            say(f"Telegram: lỗi khi xử lý ({type(exc).__name__}: {scrub(exc)})")
            if ev.kind == "button":
                outs.append(Out("ack", "Có lỗi — chưa làm gì.", event=ev))
            outs.append(_say(f"⚠ Có lỗi khi xử lý tin này nên chưa làm gì ({type(exc).__name__}: "
                             f"{scrub(exc)[:300]}). Thử lại, hoặc mở vizsup trên máy."))
    if owner and not any(o.kind == "commands" for o in outs):
        try:
            outs = commands_outs() + outs      # a new command menu reaches an older pairing
        except OSError:
            pass
    return outs


# --- the scheduled task's turn (app closed) ------------------------------------------------------

def render_headless(job: Job, kind: str = "long", *, log: Callable[[str], None] | None = None
                    ) -> Path | None:
    """Render an APPROVED episode (or its Short) unattended with the series' settings
    (autopilot.render_opts) — what the app's render queue does. None when the series has
    no render settings yet. Raises on failure (render.render_job re-checks approval)."""
    from app import net, usage
    from app.pipeline import autopilot, render
    from app.pipeline.srt import parse_srt

    opts = autopilot.render_opts(job, kind)
    if opts is None:
        return None
    o = {**opts, "burn_cues": parse_srt(job.vi_srt)}
    give_up = time.time() + RENDER_WAIT_S        # offline that long: report it, next run retries
    with usage.track(job.work_dir, phase="render"):
        return net.run_resilient(lambda: render.render_job(job, o, log=log or (lambda _m: None)),
                                 cancel=lambda: time.time() > give_up)


def render_kind(job: Job) -> str:
    """"short" for a «Cắt Short» project (rendered with the series' 9:16 Short preset), else
    "long" (a native vertical video is rendered as it is and posted as a Short after)."""
    from app.pipeline import clipshort

    return "short" if clipshort.info(job) is not None else "long"


class CliHost(Host):
    """The scheduled task: links are run to the gate right after the inbox, approved
    episodes are rendered headless (the app is closed — nothing else would render them).
    ``links`` = (url, series) to run, ``tasks`` = Host.work items run after the answers went
    out (a page listing, a cover)."""

    def __init__(self) -> None:
        self.links: list[tuple[str, str]] = []
        self.to_render: list[str] = []
        self.tasks: list[Callable[[], list[Out]]] = []

    def queue_link(self, url: str, job_id: str | None) -> str:
        self.links.append((url, "auto"))
        return ("📥 Đã nhận link. App vizsup trên máy đang đóng — tác vụ nền tải và dịch ngay "
                "bây giờ, xong sẽ gửi phụ đề để bạn duyệt.")

    def queue_batch(self, videos: list[dict], series: str) -> str:
        from app.pipeline import batch_import

        urls = [str(v["url"]) for v in batch_import.oldest_first(videos) if v.get("url")]
        self.links += [(u, series or "auto") for u in dict.fromkeys(urls)
                       if not self.link_busy(u)]
        return (f"📥 Đã nhận {len(urls)} tập. App vizsup trên máy đang đóng — tác vụ nền tải và "
                f"dịch lần lượt từ tập cũ nhất; tập nào xong sẽ gửi để bạn duyệt.")

    def link_busy(self, url: str) -> str | None:
        return ("Link này đang chờ xử lý trên máy."
                if any(u == url for u, _s in self.links) else None)

    def work(self, fn: Callable[[], list[Out]]) -> list[Out]:
        self.tasks.append(fn)
        return []

    def after_approve(self, job: Job) -> str:
        from app.pipeline import autopilot

        _p, plan = autopilot.plan_of(job)
        if plan is None:
            return ("Bộ phim này chưa bật Tự động đăng — mở vizsup trên máy để dựng (tập đã ở "
                    "trạng thái đã duyệt).")
        if autopilot.render_opts(job, render_kind(job)) is None:
            return ("Chưa tự dựng được: máy chưa dựng video nào — dựng tay một tập trên máy "
                    "để app học cài đặt.")
        self.to_render.append(job.id)
        return ("App vizsup trên máy đang đóng — tác vụ nền đang dựng theo cài đặt của bộ phim; "
                "xong sẽ lên lịch đăng và báo bạn.")

    def renders(self) -> list[str]:
        out = []
        for jid in self.to_render:
            job = load_job(jid)
            out.append(title_of(job) if job is not None else jid)
        return out


class _Beat(threading.Thread):
    """Keeps this process's poll mark fresh while it works (a render takes a while)."""

    def __init__(self) -> None:
        super().__init__(name="vizsup-remote-beat", daemon=True)
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(20.0):
            try:
                if (poll_holder() or {}).get("pid") == os.getpid():
                    take_poll("task")
            except OSError:
                pass


class CliSession:
    """``vizsup autopilot --once``'s Telegram side. Inactive (every call a no-op) when no bot
    is paired. While the app is open it only SENDS (approval messages for episodes the task
    ran, post results); it reads and answers the bot's updates only when the app does not."""

    def __init__(self, *, log: Callable[[str], None] | None = None, ch=None,
                 run_link: Callable[[str, str], str] | None = None,
                 render: Callable[..., Path | None] | None = None) -> None:
        self.log = log or (lambda _m: None)
        self.ch = ch if ch is not None else channel()
        self.active = self.ch is not None and self.ch.configured()
        self.reading = False
        self.host = CliHost()
        self.run_link = run_link
        self.render = render or render_headless
        self._beat: _Beat | None = None

    def __enter__(self) -> CliSession:
        if self.active:
            self.reading = take_poll("task")
            if self.reading:
                self._beat = _Beat()
                self._beat.start()
            else:
                self.log("Telegram: app vizsup đang mở và đọc tin nhắn — tác vụ nền chỉ gửi báo.")
        return self

    def __exit__(self, *_exc) -> None:
        if self._beat is not None:
            self._beat.stop.set()
        if self.reading:
            release_poll()

    def send(self, outs: list[Out]) -> None:
        if self.active and outs:
            deliver(outs, self.ch, log=self.log)

    def wrap(self, run: Callable[[str, str], str] | None):
        """``run(url, slug)`` for watch.check_all that also sends the approval message of
        each episode it brought to the gate."""
        if run is None or not self.active:
            return run

        def go(url: str, slug: str) -> str:
            res = run(url, slug)
            self.send(gate_outs(load_job(Path(str(res)).name) if res else None))
            return res

        return go

    def _read(self) -> int | None:
        """One getUpdates (no waiting) → answered; how many updates, None when unreadable."""
        from app import net
        from app.remote.telegram import TelegramError, scrub

        give_up = time.time() + POLL_WAIT_S
        try:
            updates = net.run_resilient(lambda: self.ch.poll(0), max_rounds=3,
                                        cancel=lambda g=give_up: time.time() > g)
        except TelegramError as exc:
            self.log(f"Telegram: {scrub(exc)}")
            return None
        except Exception as exc:  # noqa: BLE001 - no connection: next run
            self.log(f"Telegram: không đọc được tin nhắn ({type(exc).__name__})")
            return None
        self.send(process(updates, self.host, self.ch, log=self.log))
        return len(updates)

    def _pending(self) -> bool:
        h = self.host
        return bool(h.tasks or h.links or h.to_render)

    def _step(self) -> None:
        """One piece of work: a listing / cover first (quick, the owner waits for its
        answer), then a link to the gate, then a render."""
        h = self.host
        if h.tasks:
            fn = h.tasks.pop(0)
            try:
                outs = fn()
            except Exception as exc:  # noqa: BLE001 - reported, the task goes on
                outs = [_say(f"⚠ Có lỗi, chưa làm xong ({type(exc).__name__}).")]
            self.send(outs)
        elif h.links:
            self._link(*h.links.pop(0))
        elif h.to_render:
            self._render(h.to_render.pop(0))

    def inbox(self, rounds: int = 8) -> int:
        """Read and answer the owner's messages; run the links they sent to the gate and
        render the episodes they approved — until nothing new comes in (``rounds`` max).
        Between two pieces of work the bot is read again, so a [✅ Duyệt] pressed while a
        batch of links runs is answered at once. Returns how many updates were handled."""
        if not (self.active and self.reading):
            return 0
        handled = 0
        for _ in range(rounds):
            got = self._read()
            if got is None:
                break
            handled += got
            worked, reading = self._pending(), True
            while self._pending():
                self._step()
                if reading:                    # offline / refused: just finish the work
                    n = self._read()
                    reading = n is not None
                    handled += n or 0
            if not got and not worked:
                break
        return handled

    def _link(self, url: str, series: str = "auto") -> None:
        if self.run_link is None:
            return
        self.log(f"Telegram: link {url}")
        try:
            res = self.run_link(url, series or "auto")
        except Exception as exc:  # noqa: BLE001 - reported to the owner, the task goes on
            self.send(failed_outs("Chưa xử lý được link", f"{url}\n{type(exc).__name__}: {exc}",
                                  asked=True))
            return
        job = load_job(Path(str(res)).name) if res else None
        if job is None:
            self.send([_say(f"⚠ Đã chạy xong link nhưng không thấy phụ đề: {url}")])
            return
        self.send(gate_outs(job, force=True) or
                  [_say(f"Link này đã xong: {title_of(job)} — đã duyệt rồi.")])

    def _render(self, job_id: str) -> None:
        """Approved from the phone while the app is closed: render → posts → Short → posts."""
        from app.pipeline import approval, autopilot

        job = load_job(job_id)
        if job is None:
            return
        title = title_of(job)
        made = render_kind(job)                # a «Cắt Short» project: the 9:16 Short preset
        try:
            out = self.render(job, made, log=self.log)
        except Exception as exc:  # noqa: BLE001 - reported, the task goes on
            self.send(failed_outs(f"Dựng lỗi {title}", f"{type(exc).__name__}: {exc} — mở vizsup "
                                  f"trên máy để dựng lại", asked=True))
            return
        if out is None:
            self.send([_say(f"⚠ Chưa tự dựng được {title}: máy chưa có cài đặt dựng — dựng tay "
                            f"một tập trên máy để app học.")])
            return
        kind = autopilot.post_kind(job)        # a vertical video ≤ 3 min goes out as a Short
        self.send(planned_outs(job, autopilot.plan_posts(job, kind)))
        _p, plan = autopilot.plan_of(job)
        if kind != "long" or plan is None or not plan.get("auto_short"):
            return
        try:
            child = autopilot.auto_short(job, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.send(failed_outs(f"Chưa cắt được Short của {title}", f"{type(exc).__name__}: {exc}"))
            return
        if child is None:
            return
        child = load_job(child.id) or child
        if not approval.is_approved(child):
            self.send(gate_outs(child, intro="Short tự cắt có câu khác bản đã duyệt — duyệt riêng:"))
            return
        try:
            out = self.render(child, "short", log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.send(failed_outs(f"Dựng Short lỗi {title}", f"{type(exc).__name__}: {exc}"))
            return
        if out is not None:
            self.send(planned_outs(child, autopilot.plan_posts(child, "short")))

    def posted(self, posts: list[dict]) -> None:
        for p in posts or []:
            self.send(posted_outs(p))


def unpair(*, ch=None) -> None:
    """"Gỡ kết nối": say goodbye (best effort), then forget the token, chat and offset."""
    from app.remote import telegram

    ch = ch if ch is not None else channel()
    if ch is not None and ch.paired():
        try:
            ch.send("👋 vizsup đã gỡ kết nối bot này. Bot sẽ không trả lời nữa cho tới khi bạn kết "
                    "nối lại trong Cài đặt.")
        except Exception:  # noqa: BLE001 - offline / revoked token: forget anyway
            pass
    telegram.forget()
    (_root() / POLL).unlink(missing_ok=True)
