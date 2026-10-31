"""Theo dõi tập mới: list a followed creator's latest videos and pick a series' new episodes.

The owner translates Chinese AI-anime series episode by episode from a few creators. A
series profile (``series.json``) names the pages to follow; ``list_videos`` reads one:

- **Douyin 合集** ``https://www.douyin.com/collection/<mix_id>``, or the link of any episode in
  it (``douyin.com/video/<id>``: its 合集 is looked up) — the surest source: a 合集 lists every
  episode in order (``app.douyin_capture --mix``), while a creator page leaves some posts out
  (2026-10-31: 《末日校车》09 was public and in its 合集 but not on 阿熊动漫's page).
- **Douyin** creator page ``https://www.douyin.com/user/<sec_uid>`` — Douyin blocks yt-dlp's
  API, so the page is opened in the embedded browser (``app.douyin_capture --list``, its own
  process, the same persistent profile as the video capture). Without a login Douyin shows
  the newest 18–36 posts (pinned one included), then may ask to log in — plenty for a check
  every few hours; the login is never worked around.
- **Bilibili** space ``https://space.bilibili.com/<mid>`` (newest first), a season /
  collection ``…/lists/<sid>?type=season`` · ``…/channel/collectiondetail?sid=<sid>`` (the
  uploader's order, so the LAST ``limit`` items are taken) or a series ``…?type=series`` /
  ``…/channel/seriesdetail?sid=`` — yt-dlp's flat listing (it signs the space API); titles and
  publish times come from the API pages yt-dlp downloads for that listing (its flat entries
  carry only the id). Checked 2026-10-31: all three kinds list in ~1 s anonymously; the space
  API answers 412 for a while after many quick calls (rate limit).

``new_episodes`` keeps a series' single numbered episodes (the 《…》 name, then
``compilation.episode_range`` — a "1-5" compilation, a "后续" teaser or an unnumbered post is
skipped) whose ids were not seen yet, oldest first. What was seen / tried and when each
series was checked lives in ``<storage>/_autopilot/watch.json``.

``check_all`` looks at every series whose watch is on and due (``every_h``) and hands each new
episode to a ``run(url, slug)`` the caller supplies — ``vizsup autopilot --once`` runs the
normal pipeline to the edit gate. Nothing here approves or renders anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import settings
from app.fsutil import atomic_write_text

DIR = "_autopilot"
FILE = "watch.json"
LIMIT = 30
MIX_LIMIT = 300            # a 合集 is read whole (its order is the episode order)
LIST_TIMEOUT_S = 150       # the browser process gives up by itself after ~90 s
MIX_TIMEOUT_S = 180        # … a 合集 after ~120 s
MAX_TRIES = 3              # a video whose run failed this often is not tried again
BILI_RETRY_S = 20.0        # Bilibili's 412 (too many calls) passes in a while: one more try

_DOUYIN_USER = re.compile(r"/(?:share/)?user/([A-Za-z0-9_\-.]{10,})")
_BILI_LIST = re.compile(r"/lists/(\d+)")
_BILI_SPACE = re.compile(r"/\d+(?:/upload)?(?:/video)?/?")
_BV = re.compile(r"BV[0-9A-Za-z]{10}")
_DOUYIN_VIDEO = re.compile(r"/(?:video|note)/(\d{8,})")
_DOUYIN_MIX = re.compile(r"/(?:collection|mix/detail)/(\d{8,})")
_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")


# --- sources ---------------------------------------------------------------------------------

def source_kind(url: str) -> str:
    """"douyin_mix" | "douyin_video" (its 合集 is read) | "douyin_user" | "bilibili_space" |
    "bilibili_season" | "bilibili_series" | "" (not a page this watcher can read). Pure."""
    u = urlparse((url or "").strip())
    host = (u.hostname or "").lower()
    douyin = host.endswith(_DOUYIN_HOSTS)
    if douyin and _DOUYIN_MIX.search(u.path):
        return "douyin_mix"
    if douyin and _DOUYIN_USER.search(u.path) and "/user/self" not in u.path:
        return "douyin_user"
    if douyin and _DOUYIN_VIDEO.search(u.path):
        return "douyin_video"
    if host == "space.bilibili.com":
        kind = (parse_qs(u.query).get("type") or [""])[0]
        if "seriesdetail" in u.path or (_BILI_LIST.search(u.path) and kind == "series"):
            return "bilibili_series"
        if "collectiondetail" in u.path or _BILI_LIST.search(u.path):
            return "bilibili_season"
        if _BILI_SPACE.fullmatch(u.path or "/"):
            return "bilibili_space"
    return ""


def normalize_source(url: str) -> str:
    """A Douyin link without its tracking query (a creator's default tab = their posts). Pure."""
    url = (url or "").strip()
    kind, path = source_kind(url), urlparse(url).path
    if kind == "douyin_user":
        return f"https://www.douyin.com/user/{_DOUYIN_USER.search(path).group(1)}"
    if kind == "douyin_mix":
        return mix_url(_DOUYIN_MIX.search(path).group(1))
    if kind == "douyin_video":
        return f"https://www.douyin.com/video/{_DOUYIN_VIDEO.search(path).group(1)}"
    return url


def mix_url(mix_id: str) -> str:
    return f"https://www.douyin.com/collection/{mix_id}"


def _resolve_short(url: str) -> str:
    """v.douyin.com/… → where it leads (a creator's share link is one of those)."""
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.geturl()
    except Exception:  # noqa: BLE001
        return url


def list_videos(source_url: str, *, limit: int = LIMIT) -> list[dict]:
    """The latest videos of a followed page → [{id, url, title, published (epoch or None)}],
    the page's order. Raises RuntimeError / ValueError with a Vietnamese message."""
    url = (source_url or "").strip()
    host = (urlparse(url).hostname or "").lower()
    if not source_kind(url) and host in ("v.douyin.com", "www.iesdouyin.com", "iesdouyin.com"):
        url = _resolve_short(url)
    kind = source_kind(url)
    if kind == "douyin_user":
        return _douyin_list(normalize_source(url), limit)
    if kind in ("douyin_mix", "douyin_video"):
        return _douyin_mix(normalize_source(url), max(limit, MIX_LIMIT))
    if kind.startswith("bilibili_"):
        return _bilibili_list(url, limit, kind)
    raise ValueError("Không đọc được link này — cần 合集 Douyin (douyin.com/collection/… hoặc "
                     "link một tập trong 合集), trang người đăng Douyin (douyin.com/user/…) "
                     "hoặc Bilibili (space.bilibili.com/…, danh sách / tuyển tập).")


def _child_env() -> dict:
    """The browser process imports the same ``app`` this process runs (an editable install
    may point at another checkout)."""
    import app

    env = dict(os.environ)
    backend = str(Path(app.__file__).resolve().parents[1])
    rest = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and p != backend]
    env["PYTHONPATH"] = os.pathsep.join([backend, *rest])
    return env


def _browse(mode: str, url: str, limit: int, timeout: float) -> dict:
    """Run app.douyin_capture ``mode`` on ``url`` in its own process → the JSON it wrote."""
    from app.pipeline import download

    if not download.web_available():
        raise RuntimeError("Cần trình duyệt nhúng (PySide6 QtWebEngine) để đọc trang Douyin.")
    d = download._browser_dir()
    out = d / f"list-{hashlib.sha1(url.encode()).hexdigest()[:10]}.json"
    out.unlink(missing_ok=True)                       # never read an older listing
    try:
        r = subprocess.run([sys.executable, "-m", "app.douyin_capture", mode, url, str(out),
                            str(d / "profile"), str(limit)], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace",
                           env=_child_env())
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Trang Douyin mở quá lâu — thử lại sau.") from exc
    if not out.exists():
        raise RuntimeError(f"Trình duyệt nhúng không chạy được (mã {r.returncode}).")
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Trình duyệt nhúng trả về dữ liệu hỏng.") from exc
    return data if isinstance(data, dict) else {}


def _douyin_list(url: str, limit: int) -> list[dict]:
    data = _browse("--list", url, limit, LIST_TIMEOUT_S)
    videos = [_clean(v) for v in data.get("videos") or [] if isinstance(v, dict)]
    videos = [v for v in videos if v][:limit]
    if not videos:
        if data.get("captcha"):
            raise RuntimeError("Douyin đòi xác minh (captcha) — thử lại sau.")
        raise RuntimeError("Trang Douyin không hiện video nào (tài khoản riêng tư / đã xóa, "
                           "hoặc Douyin đòi đăng nhập).")
    return videos


def _douyin_mix(url: str, limit: int) -> list[dict]:
    """A 合集 (or the 合集 of a video) → its episodes, newest first; each carries ``mix``
    {id, name, url} so the owner sees which 合集 a video link led to."""
    data = _browse("--mix", url, limit, MIX_TIMEOUT_S)
    videos = [_clean(v) for v in data.get("videos") or [] if isinstance(v, dict)]
    videos = [v for v in videos if v]
    if not videos:
        err = data.get("error")
        if err == "captcha" or data.get("captcha"):
            raise RuntimeError("Douyin đòi xác minh (captcha) — thử lại sau.")
        if err == "no_mix":
            raise RuntimeError("Video này không nằm trong 合集 nào — dán link trang người đăng "
                               "(douyin.com/user/…) hoặc link 合集.")
        raise RuntimeError("Không đọc được danh sách 合集 (Douyin không trả lời) — thử lại sau.")
    mix = data.get("mix") if isinstance(data.get("mix"), dict) else {}
    info = None
    if re.fullmatch(r"\d{6,}", str(mix.get("id") or "")):
        info = {"id": str(mix["id"]), "name": str(mix.get("name") or "").strip(),
                "url": mix_url(str(mix["id"]))}
    videos.reverse()                                  # the 合集's order is oldest first
    return [{**v, "mix": info} if info else v for v in videos[:limit]]


def _clean(v: dict) -> dict | None:
    vid = str(v.get("id") or "").strip()
    if not vid:
        return None
    pub = v.get("published")
    return {"id": vid, "url": str(v.get("url") or "") or _video_url(vid),
            "title": " ".join(str(v.get("title") or "").split()),
            "published": int(pub) if isinstance(pub, (int, float)) and pub > 0 else None}


def _video_url(vid: str) -> str:
    return (f"https://www.bilibili.com/video/{vid}" if vid.startswith("BV")
            else f"https://www.douyin.com/video/{vid}")


_BILI_IES = ("BilibiliSpaceVideo", "BilibiliCollectionList", "BilibiliSeriesList")


def _record_pages(ydl, pages: list) -> None:
    """Keep every JSON page the Bilibili list extractors download (they hold the titles)."""
    for key in _BILI_IES:
        try:
            ie = ydl.get_info_extractor(key)
        except Exception:  # noqa: BLE001 - an older / newer yt-dlp without that extractor
            continue
        orig = ie._download_json

        def spy(*a, _orig=orig, **k):
            res = _orig(*a, **k)
            pages.append(res)
            return res

        ie._download_json = spy


def _page_items(page) -> list[dict]:
    """Archive rows of one API page: season / series → data.archives, space → data.list.vlist."""
    data = (page or {}).get("data") if isinstance(page, dict) else None
    if not isinstance(data, dict):
        return []
    rows = data.get("archives")
    if rows is None:
        rows = (data.get("list") or {}).get("vlist") if isinstance(data.get("list"), dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("bvid")]


def _bilibili_list(url: str, limit: int, kind: str) -> list[dict]:
    import yt_dlp

    pages: list = []
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "skip_download": True, "socket_timeout": 15, "noprogress": True}
    if kind == "bilibili_season":
        opts["playlist_items"] = f"-{limit}:"         # the uploader's order: newest are last
    else:
        opts["playlistend"] = limit                   # newest first
    for attempt in range(2):
        pages.clear()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                _record_pages(ydl, pages)
                info = ydl.extract_info(url, download=False)
                entries = [e for e in (info or {}).get("entries") or [] if isinstance(e, dict)]
            break
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "412" not in msg and "352" not in msg:
                raise RuntimeError(f"Không đọc được danh sách Bilibili: {msg[:200]}") from exc
            if attempt:
                raise RuntimeError("Bilibili tạm chặn vì gọi nhiều — thử lại sau ít phút.") from exc
            time.sleep(BILI_RETRY_S)
    meta: dict[str, dict] = {}
    for p in pages:
        for row in _page_items(p):
            meta.setdefault(str(row["bvid"]), row)
    out = []
    for e in entries:
        vid = str(e.get("id") or "")
        if not _BV.fullmatch(vid) or "/video/" not in str(e.get("url") or _video_url(vid)):
            continue                                  # a hidden collection, not a video
        row = meta.get(vid, {})
        pub = row.get("pubdate") or row.get("created") or e.get("timestamp")
        out.append({"id": vid, "url": _video_url(vid),
                    "title": " ".join(str(row.get("title") or e.get("title") or "").split()),
                    "published": int(pub) if isinstance(pub, (int, float)) and pub > 0 else None})
    if kind == "bilibili_season":
        out.reverse()                                 # newest first, like the other pages
    return out[:limit]


# --- which videos are new episodes -----------------------------------------------------------

_IN_NAME = re.compile(r"《[^》]*》")


def _nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


def belongs(profile, title: str | None) -> bool:
    """Is this title an episode of ``profile``'s series? The 《…》 name decides when there is
    one (same slug / name, or the profile's ``match`` inside it); a title without 《…》 needs
    ``match`` somewhere in it. Pure."""
    from app.pipeline import series

    t = _nfkc(title)
    match = _nfkc(getattr(profile, "match", "")).strip()
    name = series.detect_name(t)
    if name:
        return (series.slugify(name) == profile.slug or name == _nfkc(profile.name).strip()
                or (len(match) >= 2 and match in name))
    return len(match) >= 2 and match in t


def new_episodes(profile, videos: list[dict], seen, *, after: int | None = None) -> list[dict]:
    """The series' new single episodes among ``videos``: titles of this series with ONE
    episode number (``compilation.episode_range``), ids not in ``seen``, numbers above
    ``after`` (the last episode already in the library) — oldest first, one video per
    number (a re-upload: the newest). Each gets an ``episode`` key. Pure."""
    from app.pipeline.compilation import episode_range

    best: dict[int, dict] = {}
    for v in videos:
        vid = str(v.get("id") or "")
        if not vid or vid in seen or not belongs(profile, v.get("title")):
            continue
        # a number inside the 《…》 name is the name's ("《暖家奇遇第二部》" is no episode 2)
        r = episode_range(_IN_NAME.sub("《》", _nfkc(v.get("title"))))
        if r is None or r[0] != r[1] or (after is not None and r[0] <= after):
            continue                    # no number, a "1-5" compilation, or already done
        old = best.get(r[0])
        if old is None or (v.get("published") or 0) > (old.get("published") or 0):
            best[r[0]] = {**v, "episode": r[0]}
    return [best[n] for n in sorted(best)]


# --- state: <storage>/_autopilot/watch.json ----------------------------------------------------

def state_path() -> Path:
    return Path(settings.vizsup_storage_dir) / DIR / FILE


def load_state() -> dict:
    """{"series": {slug: {"checked": epoch, "seen": {id: {...}}}}, "sources": {url: {...}}};
    a missing or broken file is a fresh state."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("series", "sources"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(state, ensure_ascii=False, indent=1))


def series_state(state: dict, slug: str) -> dict:
    s = state.setdefault("series", {}).setdefault(slug, {})
    if not isinstance(s.get("seen"), dict):
        s["seen"] = {}
    return s


def _done_with(entry) -> bool:
    """A seen entry that is not tried again (anything but a failure with tries left)."""
    if not isinstance(entry, dict):
        return True
    return entry.get("status") != "failed" or int(entry.get("tries") or 0) >= MAX_TRIES


def seen_ids(state: dict, slug: str) -> set[str]:
    seen = (state.get("series", {}).get(slug) or {}).get("seen") or {}
    return {vid for vid, e in seen.items() if _done_with(e)}


def last_checked(slug: str, state: dict | None = None) -> float | None:
    st = state if state is not None else load_state()
    t = (st.get("series", {}).get(slug) or {}).get("checked")
    return float(t) if isinstance(t, (int, float)) else None


def mark(state: dict, slug: str, video: dict, status: str, **extra) -> dict:
    """Record what happened to one video (``status``: done | failed | have)."""
    seen = series_state(state, slug)["seen"]
    old = seen.get(str(video["id"])) or {}
    tries = int(old.get("tries") or 0) + (1 if status in ("done", "failed") else 0)
    entry = {"status": status, "title": video.get("title") or old.get("title") or "",
             "episode": video.get("episode", old.get("episode")),
             "url": video.get("url") or old.get("url") or "", "at": time.time(),
             "tries": tries, **extra}
    seen[str(video["id"])] = entry
    return entry


# --- what the library already has ------------------------------------------------------------

def ids_in(text: str | None) -> set[str]:
    """Douyin / Bilibili video ids in a link or id string. Pure."""
    t = str(text or "")
    out = set(_BV.findall(t)) | set(_DOUYIN_VIDEO.findall(t))
    if re.fullmatch(r"\d{8,}", t.strip()):
        out.add(t.strip())
    return out


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def library(slug: str) -> dict:
    """{"last": highest episode of ``slug`` that reached the edit gate (or None),
    "ids": video ids already turned into a project}. A folder the watcher started itself
    counts only once it has subtitles (a failed run is tried again)."""
    from app.pipeline import series

    base = Path(settings.vizsup_storage_dir)
    last: int | None = None
    ids: set[str] = set()
    try:
        dirs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))]
    except OSError:
        dirs = []
    for d in dirs:
        ready = (d / "vi.srt").exists() or (d / "output.mp4").exists()
        job = _read_json(d / "job.json")
        if ready or (job and not (job.get("opts") or {}).get("autopilot")):
            meta = _read_json(d / "metadata.json")
            for v in (job.get("url"), (job.get("opts") or {}).get("url"), meta.get("webpage_url"),
                      meta.get("video_id")):
                ids |= ids_in(v)
        lk = series.link_of(d) if ready else None
        if lk and lk.get("slug") == slug:
            n = lk.get("last") if lk.get("last") is not None else lk.get("episode")
            if isinstance(n, int):
                last = n if last is None else max(last, n)
    return {"last": last, "ids": ids}


def suggested_source(slug: str) -> str | None:
    """A page to follow for a series that has none yet: the link of its newest Douyin episode
    in the library (the watcher reads that video's 合集). None without one."""
    from app.pipeline import series

    best: int | None = None
    try:
        dirs = [d for d in Path(settings.vizsup_storage_dir).iterdir()
                if d.is_dir() and not d.name.startswith(("_", "."))]
    except OSError:
        dirs = []
    for d in dirs:
        lk = series.link_of(d)
        if not lk or lk.get("slug") != slug:
            continue
        meta = _read_json(d / "metadata.json")
        for v in (meta.get("webpage_url"), meta.get("video_id")):
            for i in ids_in(v):
                if i.isdigit() and (best is None or int(i) > best):
                    best = int(i)                 # a Douyin id grows with its upload time
    return f"https://www.douyin.com/video/{best}" if best is not None else None


# --- one series: list its sources, keep the new episodes ----------------------------------------

def find_new(profile, sources: list[str], *, state: dict | None = None, log=None,
             limit: int = LIMIT, lister=None) -> dict:
    """List ``sources`` and keep ``profile``'s new episodes (not seen, not in the library,
    above its last episode). Changes nothing on disk. → {"sources": [{url, n, error, mix?}],
    "new": [video + episode], "last": int | None}."""
    lister = lister or list_videos
    state = state if state is not None else load_state()
    lib = library(profile.slug)
    rows, videos, ids = [], [], set()
    for src in sources:
        try:
            got = lister(src, limit=limit)
            mix = next((v["mix"] for v in got if v.get("mix")), None)
            rows.append({"url": src, "n": len(got), "error": "",
                         **({"mix": mix} if mix else {})})
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the others
            rows.append({"url": src, "n": 0, "error": str(exc)})
            if log:
                log(f"⚠ {src}: {exc}")
            continue
        for v in got:
            if v["id"] not in ids:
                ids.add(v["id"])
                videos.append(v)
    skip = seen_ids(state, profile.slug) | lib["ids"]
    new = new_episodes(profile, videos, skip, after=lib["last"])
    return {"sources": rows, "new": new, "last": lib["last"],
            "have": sorted(i for i in ids if i in lib["ids"])}


# --- the check: every followed series that is due ------------------------------------------------

LOCK = "watch.lock"
LOCK_STALE_S = 12 * 3600       # a lock this old belongs to a run that died
DUE_SLACK_S = 600              # a scheduled task fires every N h exactly; the last check ended later


def is_due(profile, state: dict, now: float | None = None) -> bool:
    from app.pipeline import series

    last = last_checked(profile.slug, state)
    now = time.time() if now is None else now
    return last is None or now - last >= series.autopilot_of(profile)["every_h"] * 3600 - DUE_SLACK_S


class _Lock:
    """One real check at a time (the app and the scheduled task may both start one)."""

    def __init__(self, active: bool = True) -> None:
        self.path = Path(settings.vizsup_storage_dir) / DIR / LOCK
        self.active = active          # a dry look takes no lock
        self.held = False

    def __enter__(self) -> _Lock:
        if not self.active:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    stale = time.time() - self.path.stat().st_mtime > LOCK_STALE_S
                except OSError:
                    stale = True
                if not stale:
                    return self
                self.path.unlink(missing_ok=True)
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
            break
        return self

    def touch(self) -> None:
        if self.held:
            try:
                os.utime(self.path)
            except OSError:
                pass

    def __exit__(self, *exc) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)


def _item(profile, v: dict, status: str, **extra) -> dict:
    return {"slug": profile.slug, "series": profile.name, "id": v["id"], "url": v["url"],
            "title": v.get("title") or "", "episode": v.get("episode"),
            "published": v.get("published"), "status": status, **extra}


def check_series(profile, *, run=None, log=None, state: dict | None = None,
                 now: float | None = None, lister=None, lock: _Lock | None = None) -> list[dict]:
    """One followed series: list its pages and hand its new episodes, oldest first, to
    ``run(url, slug)`` — at most ``max_per_check``, the rest wait for the next check. With
    ``run=None`` nothing runs and nothing is recorded (a dry look). An episode whose run
    raised is tried again at the next check, up to MAX_TRIES. → one dict per episode with
    ``status``: done | failed | new (dry) | later."""
    from app.pipeline import series

    say = log or (lambda _m: None)
    ap = series.autopilot_of(profile)
    st = state if state is not None else load_state()
    now = time.time() if now is None else now
    res = find_new(profile, ap["sources"], state=st, log=say, lister=lister)
    if run is not None:
        for src in res["sources"]:
            st["sources"][src["url"]] = {"at": now, "n": src["n"], "error": src["error"]}
        series_state(st, profile.slug)["checked"] = now
        save_state(st)
    if not res["new"]:
        say(f"《{profile.name}》: không có tập mới")
    out = []
    for i, v in enumerate(res["new"]):
        if i >= ap["max_per_check"]:
            out.append(_item(profile, v, "later"))
            continue
        if run is None:
            out.append(_item(profile, v, "new"))
            continue
        say(f"▶ 《{profile.name}》 tập {v['episode']}: {v['url']}")
        try:
            result = run(v["url"], profile.slug)
        except Exception as exc:  # noqa: BLE001 - one episode must not stop the others
            e = mark(st, profile.slug, v, "failed", error=f"{type(exc).__name__}: {exc}"[:300])
            out.append(_item(profile, v, "failed", error=e["error"], tries=e["tries"]))
        else:
            mark(st, profile.slug, v, "done", result="" if result is None else str(result))
            out.append(_item(profile, v, "done", result="" if result is None else str(result)))
        say(describe(out[-1]))
        save_state(st)
        if lock is not None:
            lock.touch()
    return out


def check_all(*, run=None, log=None, force: bool = False, now: float | None = None,
              lister=None) -> list[dict]:
    """Every series whose watch is on and whose check is due (``force``: all of them):
    find its new episodes and call ``run(url, series_slug)`` for each — the caller decides
    how to process one (the CLI runs the pipeline to the edit gate). ``run=None`` = a dry
    look: nothing runs, nothing is recorded. Returns what was found (``check_series``)."""
    from app.pipeline import series

    say = log or (lambda _m: None)
    with _Lock(active=run is not None) as lock:
        if run is not None and not lock.held:
            say("Một lần kiểm tra khác đang chạy — bỏ qua lần này.")
            return []
        st = load_state()
        now = time.time() if now is None else now
        out: list[dict] = []
        profiles = series.followed()
        if not profiles:
            say("Chưa có bộ phim nào bật “Theo dõi tập mới”.")
        for p in profiles:
            if not force and not is_due(p, st, now):
                last = last_checked(p.slug, st)
                say(f"《{p.name}》: chưa tới giờ kiểm tra (lần trước "
                    f"{time.strftime('%d/%m %H:%M', time.localtime(last))})")
                continue
            say(f"Kiểm tra 《{p.name}》…")
            out += check_series(p, run=run, log=say, state=st, now=now, lister=lister, lock=lock)
        if run is not None:
            st["last_run"] = {"at": now, "items": out}
            save_state(st)
        return out


def describe(item: dict) -> str:
    """One line about one found episode, for logs and summaries. Pure."""
    head = f"《{item.get('series')}》 tập {item.get('episode')}"
    status = item.get("status")
    if status == "done":
        return f"✓ {head}: đã tới bước duyệt — chờ bạn duyệt" + (
            f" ({item['result']})" if item.get("result") else "")
    if status == "failed":
        return f"✗ {head}: lỗi (lần {item.get('tries', 1)}/{MAX_TRIES}): {item.get('error', '')}"
    if status == "later":
        return f"… {head}: để lần kiểm tra sau"
    return f"• {head}: sẽ lấy — {item.get('url')}"
