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

Only ONE process reads the bot's updates (Telegram answers a second getUpdates with 409):
``take_poll`` — the open app holds it, the scheduled task only polls when the app does not.
Each update is committed (offset saved) BEFORE it is acted on: processed once, at most.
Network sends never run on the GUI thread: ``handle`` returns ``Out`` items, ``deliver`` (or
the desktop's ``Outbox`` thread) sends them.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.fsutil import atomic_write_text
from app.models import Job
from app.remote import Button, Event

STATE = Path("_remote") / "state.json"       # approval messages sent (job → revision); no secrets
POLL = Path("_remote") / "poll.json"         # who reads the bot's updates now
POLL_STALE_S = 120
REV = 8                                      # revision prefix carried by the ✅ button
CAP = 40                                     # flagged lines shown in one approval message
WAITING_DAYS = 14
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
        "• Gửi link video Douyin / Bilibili (dán nguyên đoạn chia sẻ “长按复制…” cũng được): "
        "máy tải, làm phụ đề, dịch rồi gửi lại để bạn duyệt.\n"
        "• Tập tới bước duyệt: bot gửi các câu cần xem + nút ✅ Duyệt (duyệt là việc của bạn — "
        "không bao giờ tự duyệt) hoặc ✏️ Để sửa trên máy.\n"
        "• Duyệt xong: bộ phim bật Tự động đăng thì máy tự dựng, cắt Short, lên lịch và đăng; "
        "bot báo từng bước.\n\n"
        "/trangthai — tập chờ duyệt, hàng dựng, lịch đăng 24 giờ, bài vừa đăng, lỗi\n"
        "/help — hướng dẫn này")


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
    st = _read(_root() / STATE)
    sent = st.get("sent") or {}
    cutoff = time.time() - 60 * 86400
    sent = {k: v for k, v in sent.items() if float((v or {}).get("at") or 0) > cutoff}
    sent[job_id] = {"rev": rev, "at": time.time(), "msg": message_id}
    st["sent"] = sent
    _write(_root() / STATE, st)


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


def title_of(job: Job) -> str:
    from app.pipeline.compilation import _job_title, clean_title

    return clean_title(_job_title(job.work_dir), 80)


def rev_of(job: Job) -> str:
    from app.pipeline import approval

    return approval.revision(job)[:REV]


def find_link(text: str) -> str | None:
    """The first Douyin / Bilibili link in a message — a bare link or the apps' share text
    ("7.43 复制打开抖音… https://v.douyin.com/iR4x/ …"). Pure."""
    m = _LINK.search(text or "")
    return m.group(0).rstrip(_TRAIL) if m else None


def project_for_url(url: str) -> Job | None:
    """The newest project made from ``url`` (job.json's link), else None."""
    want = (url or "").strip()
    best: tuple[float, str] | None = None
    root = _root()
    if not want or not root.exists():
        return None
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        jf = d / "job.json"
        if not jf.exists():
            continue
        data = _read(jf)
        if want not in (str(data.get("url") or "").strip(),
                        str((data.get("opts") or {}).get("url") or "").strip()):
            continue
        t = jf.stat().st_mtime
        if best is None or t > best[0]:
            best = (t, d.name)
    if best is None:
        return None
    d = root / best[1]
    job = Job(id=d.name, work_dir=d)
    job.metadata = _read(d / "metadata.json")
    return job


# --- outgoing ------------------------------------------------------------------------------------

@dataclass
class Out:
    """One thing to send: "send" a message (``note`` = the notification toggle it belongs
    to, None = a direct answer, always sent), "ack" a button press, "drop" its buttons."""
    kind: str
    text: str = ""
    buttons: list | None = None
    event: Event | None = None
    note: str | None = None
    preview: bool = False
    sent: Callable[[int | None], None] | None = field(default=None, repr=False)


def _say(text: str, note: str | None = None, **kw) -> Out:
    return Out("send", text, note=note, **kw)


def deliver(outs: list[Out], ch=None, *, log: Callable[[str], None] | None = None,
            sleep: Callable[[float], None] = time.sleep) -> int:
    """Send ``outs`` in order on the paired channel; returns how many went out. A refusal is
    logged and skipped; a connection error is retried twice, Telegram's flood limit waited
    out (≤ 30 s). Blocking — never call it on the GUI thread."""
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
                if o.kind == "send":
                    mid = ch.send(o.text, o.buttons, preview=o.preview)
                    if o.sent is not None:
                        o.sent(mid)
                elif o.kind == "ack" and o.event is not None:
                    ch.ack(o.event, o.text)
                elif o.kind == "drop" and o.event is not None:
                    ch.drop_buttons(o.event)
                n += 1
                break
            except TelegramError as exc:
                if exc.retry_after and attempt < 2:
                    sleep(min(30.0, exc.retry_after))
                    continue
                say(f"Telegram từ chối ({o.kind}): {scrub(exc)}")
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
    if not posts:
        return []
    rows = "\n".join(f"• {PLATFORMS.get(p['platform'], p['platform'])} "
                     f"({KINDS.get(p['kind'], p['kind'])}) lúc {_when(p['slot'])}"
                     for p in sorted(posts, key=lambda p: p["slot"]))
    return [_say(f"📅 Đã lên lịch đăng: {title_of(job)}\n{rows}", note="planned")]


def posted_outs(post: dict | None) -> list[Out]:
    """A publisher's result (the post job after ``autopilot.record``)."""
    if not post:
        return []
    res = post.get("result") or {}
    name = PLATFORMS.get(post.get("platform") or "", post.get("platform") or "")
    kind = KINDS.get(post.get("kind") or "", post.get("kind") or "")
    d = _root() / str(post.get("job") or "")
    from app.pipeline.compilation import _job_title, clean_title

    title = clean_title(_job_title(d), 80) if d.is_dir() else str(post.get("job"))
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


def status_text(renders: list[str] | None = None, *, now: float | None = None) -> str:
    """/trangthai: episodes waiting for approval, the render queue, the next 24 h of posts,
    the last five published, failures."""
    from app.pipeline import autopilot
    from app.pipeline.compilation import _job_title, clean_title

    now = time.time() if now is None else now
    lines = [f"📊 vizsup · {time.strftime('%H:%M %d/%m', time.localtime(now))}"]
    wait = waiting(now)
    lines.append("")
    if wait:
        lines.append(f"🟡 Chờ duyệt ({len(wait)}):")
        lines += [f"• {title_of(j)}" for j in wait[:8]]
        if len(wait) > 8:
            lines.append(f"… và {len(wait) - 8} tập nữa")
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
        return clean_title(_job_title(d), 60) if d.is_dir() else str(p.get("job"))

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

    def link_busy(self, url: str) -> str | None:
        return None

    def before_approve(self, job: Job) -> str | None:
        """Why ``job`` must not be approved now (None = go on). The desktop saves the open
        editor here; the revision is checked again right after."""
        return None

    def after_approve(self, job: Job) -> str:                           # pragma: no cover
        raise NotImplementedError

    def renders(self) -> list[str]:
        return []


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


def handle(ev: Event, host: Host) -> list[Out]:
    if ev.kind == "paired":
        who = f" của {ev.who}" if ev.who else ""
        return [_say(f"✅ Đã ghép bot với vizsup trên máy{who}. Từ giờ bot chỉ trả lời đoạn chat "
                     f"này.\n\n{HELP}")]
    if ev.kind == "button":
        return _button(ev, host)
    if ev.kind == "text":
        return _text(ev, host)
    return []


def _text(ev: Event, host: Host) -> list[Out]:
    t = (ev.text or "").strip()
    cmd = t.split()[0].lower().split("@")[0] if t.startswith("/") else ""
    if cmd in ("/start", "/help", "/trogiup", "/huongdan"):
        return [_say(HELP)]
    if cmd in ("/trangthai", "/status"):
        return [_say(status_text(host.renders()))]
    url = find_link(t)
    if url:
        return _link(url, host)
    return [_say("Mình chưa hiểu tin này. Gửi link video Douyin / Bilibili (dán nguyên đoạn chia "
                 "sẻ cũng được), hoặc /trangthai, /help.")]


def _link(url: str, host: Host) -> list[Out]:
    from app.pipeline import approval

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
    if parts[0] == "ed" and len(parts) == 2:
        job = load_job(parts[1])
        title = title_of(job) if job is not None else "tập này"
        return [Out("ack", "Để dành sửa trên máy", event=ev), Out("drop", event=ev),
                _say(f"✏️ Để dành {title} sửa trên máy: mở vizsup → Thư viện (hoặc Tiếp tục dự "
                     f"án), sửa rồi bấm Duyệt và dựng. Tập này KHÔNG tự duyệt.")]
    return [Out("ack", "Nút này không còn dùng được.", event=ev)]


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
    for raw in updates:
        ch.commit(ch.update_id(raw))           # first: an update is acted on once, at most
        ev = ch.accept(raw)
        if ev is None:
            continue
        try:
            outs += handle(ev, host)
        except Exception as exc:  # noqa: BLE001 - one bad message must not stop the others
            say(f"Telegram: lỗi khi xử lý ({type(exc).__name__}: {scrub(exc)})")
            if ev.kind == "button":
                outs.append(Out("ack", "Có lỗi — chưa làm gì.", event=ev))
            outs.append(_say(f"⚠ Có lỗi khi xử lý tin này, chưa làm gì: {type(exc).__name__}: "
                             f"{scrub(exc)[:300]}"))
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


class CliHost(Host):
    """The scheduled task: links are run to the gate right after the inbox, approved
    episodes are rendered headless (the app is closed — nothing else would render them)."""

    def __init__(self) -> None:
        self.links: list[str] = []
        self.to_render: list[str] = []

    def queue_link(self, url: str, job_id: str | None) -> str:
        self.links.append(url)
        return ("📥 Đã nhận link. App vizsup trên máy đang đóng — tác vụ nền tải và dịch ngay "
                "bây giờ, xong sẽ gửi phụ đề để bạn duyệt.")

    def link_busy(self, url: str) -> str | None:
        return "Link này đang chờ xử lý trên máy." if url in self.links else None

    def after_approve(self, job: Job) -> str:
        from app.pipeline import autopilot

        _p, plan = autopilot.plan_of(job)
        if plan is None:
            return ("Bộ phim này chưa bật Tự động đăng — mở vizsup trên máy để dựng (tập đã ở "
                    "trạng thái đã duyệt).")
        if autopilot.render_opts(job) is None:
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

    def inbox(self, rounds: int = 8) -> int:
        """Read and answer the owner's messages; run the links they sent to the gate and
        render the episodes they approved — until nothing new comes in (``rounds`` max).
        Returns how many updates were handled."""
        if not (self.active and self.reading):
            return 0
        from app import net
        from app.remote.telegram import TelegramError, scrub

        handled = 0
        for _ in range(rounds):
            give_up = time.time() + POLL_WAIT_S
            try:
                updates = net.run_resilient(lambda: self.ch.poll(0), max_rounds=3,
                                            cancel=lambda g=give_up: time.time() > g)
            except TelegramError as exc:
                self.log(f"Telegram: {scrub(exc)}")
                break
            except Exception as exc:  # noqa: BLE001 - no connection: next run
                self.log(f"Telegram: không đọc được tin nhắn ({type(exc).__name__})")
                break
            handled += len(updates)
            self.send(process(updates, self.host, self.ch, log=self.log))
            worked = bool(self.host.links or self.host.to_render)
            while self.host.links:
                self._link(self.host.links.pop(0))
            while self.host.to_render:
                self._render(self.host.to_render.pop(0))
            if not updates and not worked:
                break
        return handled

    def _link(self, url: str) -> None:
        if self.run_link is None:
            return
        self.log(f"Telegram: link {url}")
        try:
            res = self.run_link(url, "auto")
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
        try:
            out = self.render(job, "long", log=self.log)
        except Exception as exc:  # noqa: BLE001 - reported, the task goes on
            self.send(failed_outs(f"Dựng lỗi {title}", f"{type(exc).__name__}: {exc} — mở vizsup "
                                  f"trên máy để dựng lại", asked=True))
            return
        if out is None:
            return
        self.send(planned_outs(job, autopilot.plan_posts(job, "long")))
        _p, plan = autopilot.plan_of(job)
        if plan is None or not plan.get("auto_short"):
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
