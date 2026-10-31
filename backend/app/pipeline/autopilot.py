"""Autopilot (docs/plan/J-autopilot.md): after the owner's ONE approval of an episode, the app
renders it, cuts a Short from it, and posts both on the connected platforms at the series'
posting slots — so the owner's loop is "paste a link (or let the watcher find it) → approve".

- **Plan** per series (``<storage>/_autopilot/plans.json``, keyed by series slug): which
  platforms take which kinds ("long" episode / "short" 9:16), daily posting times, how many
  posts per platform per day, whether to cut a Short automatically, title templates.
- **Posts** (``<storage>/_autopilot/posts.json``): one job per (project, kind, platform) with
  its slot, status and result. ``due()`` lists what to publish now; ``record()`` stores results.
- **The gate stays.** A Short cut automatically inherits the episode's approval ONLY when every
  line of it is, word for word, an approved line of the episode (``carry_approval``) — the
  owner's click approved those words; the Short's hook is one of those lines. Anything else
  waits for its own approval in the editor.

Pure helpers + small JSON files; the desktop schedules renders and runs publishers.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.config import settings
from app.fsutil import atomic_write_text
from app.models import Job

PLANS = Path("_autopilot") / "plans.json"
POSTS = Path("_autopilot") / "posts.json"
KINDS = ("long", "short")
DEFAULT_PLAN = {
    "on": False,
    "platforms": {},                     # {"youtube": {"on": True, "kinds": ["long", "short"]}}
    "times": ["19:00"],                  # local posting times
    "per_day": 1,                        # posts per platform per kind per day
    "auto_short": True,
    "series_name": "",                   # the series' name in posts (Vietnamese), else its 《name》
    "title": "{series} – Tập {n} | Vietsub + Lồng tiếng",
    "short_title": "{hook} | {series} tập {n}",
    "tags": ["#vietsub", "#longtieng", "#hoathinh"],
}


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


# --- plans ------------------------------------------------------------------------------------

def load_plan(slug: str) -> dict:
    saved = _read(_root() / PLANS).get(slug) or {}
    return {**DEFAULT_PLAN, **(saved if isinstance(saved, dict) else {})}


def save_plan(slug: str, plan: dict) -> None:
    all_ = _read(_root() / PLANS)
    all_[slug] = {k: v for k, v in plan.items() if k in DEFAULT_PLAN}
    _write(_root() / PLANS, all_)


def targets(plan: dict, kind: str) -> list[str]:
    """Platforms that take ``kind`` under ``plan`` (on, and listing the kind). Pure."""
    if not plan.get("on"):
        return []
    return [name for name, cfg in (plan.get("platforms") or {}).items()
            if isinstance(cfg, dict) and cfg.get("on") and kind in (cfg.get("kinds") or KINDS)]


# --- slots ------------------------------------------------------------------------------------

def _times(times: list[str]) -> list[tuple[int, int]]:
    out = []
    for t in times or ["19:00"]:
        try:
            h, m = (int(x) for x in str(t).split(":")[:2])
        except ValueError:
            continue
        if 0 <= h < 24 and 0 <= m < 60:
            out.append((h, m))
    return sorted(set(out)) or [(19, 0)]


def next_slot(taken: list[float], times: list[str], per_day: int, now: float) -> float:
    """The first posting time after ``now`` (local clock, at one of ``times``) that is not
    taken and keeps the day at ≤ ``per_day`` posts. Pure."""
    hm = _times(times)
    per_day = max(1, int(per_day or 1))
    taken_set = {round(t) for t in taken}
    start = datetime.fromtimestamp(now)
    for day in range(0, 366):
        d = (start + timedelta(days=day)).date()
        on_day = sum(1 for t in taken if datetime.fromtimestamp(t).date() == d)
        if on_day >= per_day:
            continue
        for h, m in hm:
            ts = datetime(d.year, d.month, d.day, h, m).timestamp()
            if ts > now + 60 and round(ts) not in taken_set:
                return ts
    return now + 3600


# --- post jobs --------------------------------------------------------------------------------

def load_posts() -> list[dict]:
    return list(_read(_root() / POSTS).get("posts") or [])


def save_posts(posts: list[dict]) -> None:
    _write(_root() / POSTS, {"posts": posts})


def _series_of(job: Job) -> tuple[str, str] | None:
    from app.pipeline import series

    p = series.profile_of(job)
    return (p.slug, p.name) if p is not None else None


def episode_label(job: Job) -> str:
    from app.pipeline import compilation

    r = compilation.episode_range(compilation._job_title(job.work_dir))
    if not r:
        return ""
    return f"{r[0]}–{r[1]}" if r[1] > r[0] else str(r[0])


def post_meta(job: Job, kind: str, plan: dict, *, publish_at: float | None = None):
    """Title / description / tags for one post — the series' templates, the post description
    the render page writes (source credit, AI voice note, rights), the series' tags."""
    from app.pipeline import clipshort, disclosure
    from app.providers.base import PostMeta

    ser = _series_of(job)
    name = (plan.get("series_name") or "").strip() or (ser[1] if ser else "")
    n = episode_label(job)
    hook = ((clipshort.info(job) or {}).get("hook") or "") if kind == "short" else ""
    tmpl = plan.get("short_title" if kind == "short" else "title") or DEFAULT_PLAN["title"]
    title = tmpl.format(series=name, n=n, hook=hook).strip(" |–-") or job.id
    title = " ".join(title.split())[:100]
    desc = disclosure.post_description(job, ai_voice=True, title=title)
    tags = [t for t in (plan.get("tags") or []) if str(t).startswith("#")]
    if kind == "short":
        tags = [*tags, "#shorts"]
    thumb = job.work_dir / "thumb.jpg"
    return PostMeta(title=title, description=desc, tags=tags, kind=kind, publish_at=publish_at,
                    thumbnail=thumb if thumb.exists() else None, playlist=name)


def plan_posts(job: Job, kind: str, *, now: float | None = None) -> list[dict]:
    """Queue ``job`` (a rendered episode, or a rendered Short with kind "short") on every
    platform its series' plan sends that kind to; one job per platform, at the next free slot.
    Already-queued (project, kind, platform) pairs are left alone. Returns the new jobs."""
    ser = _series_of(job)
    if ser is None or not (job.work_dir / "output.mp4").exists():
        return []
    plan = load_plan(ser[0])
    now = now if now is not None else time.time()
    posts = load_posts()
    have = {(p["job"], p["kind"], p["platform"]) for p in posts}
    new = []
    for platform in targets(plan, kind):
        if (job.id, kind, platform) in have:
            continue
        taken = [p["slot"] for p in posts if p["platform"] == platform and p["kind"] == kind
                 and p.get("status") in ("pending", "done", "scheduled", "private", "draft")]
        slot = next_slot(taken, plan.get("times") or [], plan.get("per_day") or 1, now)
        item = {"id": f"{job.id}:{kind}:{platform}", "job": job.id, "kind": kind,
                "platform": platform, "series": ser[0], "slot": slot, "status": "pending",
                "added": now}
        posts.append(item)
        new.append(item)
    if new:
        save_posts(posts)
    return new


def due(now: float | None = None) -> list[dict]:
    """Pending posts whose slot has come (oldest first)."""
    now = now if now is not None else time.time()

    def ready(p: dict) -> bool:
        if p.get("status") == "running":           # a publisher that never came back
            return time.time() - float(p.get("claimed_at") or 0) > RUN_STALE_S
        return p.get("status") == "pending"

    return sorted((p for p in load_posts() if ready(p) and p["slot"] <= now),
                  key=lambda p: p["slot"])


def record(post_id: str, result) -> dict | None:
    """Store a publisher's PostResult on the post job. Failed posts go back to "pending" one hour
    later (three attempts), then stay "failed"."""
    with _PostsLock():
        return _record(post_id, result)


def _record(post_id: str, result) -> dict | None:
    posts = load_posts()
    for p in posts:
        if p["id"] != post_id:
            continue
        p["tries"] = int(p.get("tries") or 0) + 1
        p["result"] = {"status": result.status, "url": result.url, "id": result.remote_id,
                       "message": result.message, "at": time.time()}
        if result.ok:
            p["status"] = result.status or "done"
        elif p["tries"] < 3:
            p["slot"] = time.time() + 3600
            p["status"] = "pending"
        else:
            p["status"] = "failed"
        save_posts(posts)
        return p
    return None


def publish(post: dict, *, progress=None):
    """Run the platform's publisher for one post job (blocking; call it off the GUI thread)."""
    from app.providers.base import PostResult
    from app.providers.registry import get_publisher

    d = _root() / post["job"]
    job = Job(id=post["job"], work_dir=d)
    video = d / "output.mp4"
    if not video.exists():
        return PostResult(False, "failed", message="Video đã dựng không còn trên máy.")
    try:
        pub = get_publisher(post["platform"])
    except KeyError:
        return PostResult(False, "failed", message=f"Chưa có nơi đăng “{post['platform']}”.")
    if not pub.connected():
        return PostResult(False, "failed", message=f"{pub.display_name}: chưa kết nối tài khoản.")
    meta = post_meta(job, post["kind"], load_plan(post.get("series") or ""))
    try:
        return pub.publish(video, meta, progress=progress)
    except Exception as exc:  # noqa: BLE001 - a post must never crash the app
        return PostResult(False, "failed", message=f"{type(exc).__name__}: {exc}")



# --- one publisher at a time across processes (the open app + the scheduled task) ---------------

RUN_STALE_S = 2 * 3600           # a "running" post older than this was cut off — retry it


class _PostsLock:
    """A short exclusive section around posts.json read-modify-write (O_EXCL lock file)."""

    def __init__(self) -> None:
        self.path = _root() / "_autopilot" / "posts.lock"

    def __enter__(self):
        import os

        self.path.parent.mkdir(parents=True, exist_ok=True)
        end = time.time() + 10
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 60:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                if time.time() > end:
                    raise TimeoutError("posts.json đang bận") from None
                time.sleep(0.05)

    def __exit__(self, *_exc) -> None:
        self.path.unlink(missing_ok=True)


def claim(post_id: str) -> bool:
    """Mark a due post "running" for this process; False when another process (the app or the
    scheduled task) already has it, or it is no longer pending."""
    import os

    with _PostsLock():
        posts = load_posts()
        for p in posts:
            if p["id"] != post_id:
                continue
            stale = p.get("status") == "running" and \
                time.time() - float(p.get("claimed_at") or 0) > RUN_STALE_S
            if p.get("status") != "pending" and not stale:
                return False
            p.update(status="running", claimed_at=time.time(), claimed_by=os.getpid())
            save_posts(posts)
            return True
    return False


def run_due(*, log=None, now: float | None = None) -> list[dict]:
    """Publish every due post (claimed one at a time) — the scheduled task's path while the
    app is closed. Returns the updated post jobs."""
    say = log or (lambda _m: None)
    done = []
    for post in due(now):
        if not claim(post["id"]):
            continue
        res = publish(post)
        p = record(post["id"], res)
        say(("✓ " if res.ok else "✗ ") + f"{post['platform']} · {post['kind']} · {post['job']}: "
            + (res.message or res.url or res.status))
        if p:
            done.append(p)
    return done

# --- the Short cut after approval ------------------------------------------------------------

def _range_texts(job: Job, start: float, end: float) -> list[str]:
    from app.pipeline.srt import parse_srt

    return [c.text for c in parse_srt(job.vi_srt) if start <= (c.start + c.end) / 2 < end]


def carry_approval(parent: Job, child: Job) -> bool:
    """Approve a Short cut from an approved episode when its lines are exactly that episode's
    approved lines in the cut range (same words, same order) — nothing new to review. The
    approval notes where it came from (approved_from.json). False when anything differs."""
    from app.pipeline import approval, clipshort
    from app.pipeline.srt import parse_srt

    info = clipshort.info(child) or {}
    if not (approval.is_approved(parent) and info.get("parent") == parent.id):
        return False
    mine = [c.text for c in parse_srt(child.vi_srt)]
    theirs = _range_texts(parent, float(info.get("start", 0)), float(info.get("end", 0)))
    if not mine or mine != theirs:
        return False
    if (child.work_dir / "style_overrides.json").exists() or (child.work_dir / "commentary.json").exists():
        return False
    approval.approve(child, cues=len(mine))
    _write(child.work_dir / "approved_from.json",
           {"parent": parent.id, "parent_revision": approval.approved_revision(parent),
            "at": time.time(), "why": "Short tự cắt — mọi câu là câu đã duyệt của tập"})
    return True


def auto_short(parent: Job, *, log=None) -> Job | None:
    """Cut the best heuristic Short (free, no AI) from an approved episode and carry the
    approval when allowed. None when the episode is too short / has no good range."""
    from app.pipeline import clipshort
    from app.pipeline.srt import parse_srt

    cands = clipshort.propose(parse_srt(parent.vi_srt), n=1)
    if not cands:
        return None
    c = cands[0]
    child = clipshort.make(parent, c.start, c.end, c.hook, _root(), log=log)
    carried = carry_approval(parent, child)
    if log:
        log("✓ Short tự cắt " + ("— đã duyệt theo tập" if carried else "— cần bạn duyệt riêng"))
    return child


def summary(slug: str) -> str:
    """One line for the UI: "Tự đăng: YouTube (dài+Short), Facebook (Short) · 19:00 · 1/ngày"."""
    plan = load_plan(slug)
    if not plan.get("on"):
        return "Tự đăng: tắt"
    names = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok", "folder": "Gói đăng"}
    bits = []
    for name, cfg in (plan.get("platforms") or {}).items():
        if isinstance(cfg, dict) and cfg.get("on"):
            kinds = "+".join({"long": "dài", "short": "Short"}[k] for k in cfg.get("kinds") or KINDS
                             if k in KINDS)
            bits.append(f"{names.get(name, name)} ({kinds})")
    return (f"Tự đăng: {', '.join(bits) or 'chưa chọn nơi đăng'} · {', '.join(plan.get('times') or [])}"
            f" · {plan.get('per_day') or 1}/ngày")


# --- render settings ---------------------------------------------------------------------------

def _latest_rendered_in_series(job: Job) -> Path | None:
    from app.pipeline import series

    lk = series.link_of(job.work_dir)
    if not lk:
        return None
    best: tuple[float, Path] | None = None
    for d in _root().iterdir():
        if not d.is_dir() or d == job.work_dir or d.name.startswith("_"):
            continue
        if (series.link_of(d) or {}).get("slug") != lk.get("slug"):
            continue
        out = d / "output.mp4"
        if (d / "render_opts.json").exists() and out.exists():
            t = out.stat().st_mtime
            if best is None or t > best[0]:
                best = (t, d)
    return best[1] if best else None


def render_opts(job: Job, kind: str = "long") -> dict | None:
    """The settings to render ``job`` with, unattended: the series' learned render settings,
    else the latest rendered episode of the series; the project's own subtitle look wins
    (batch.opts_for). A Short adds the series' Short preset. None when the series has never
    been rendered (the owner renders one episode by hand first)."""
    from app.pipeline import batch, series, shorts

    p = series.profile_of(job)
    template = dict(p.render) if p is not None and p.render else None
    if not template:
        src = _latest_rendered_in_series(job)
        template = batch.template_from(src) if src else None
    if not template:
        return None
    opts = batch.opts_for(job.work_dir, template)
    if kind == "short":
        sh = shorts.settings_for(job.work_dir)
        opts.update({k: v for k, v in sh.items() if k != "platform"})
        opts.update({"safe_caption": sh.get("platform"), "short": True})
    return opts
