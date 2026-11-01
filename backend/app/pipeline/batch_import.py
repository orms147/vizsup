"""Dịch một loạt video (desktop/widgets/batch_import_dialog.py): pick many videos of one page
and queue them all to the edit gate.

The page is read by ``watch.list_videos`` — the series watcher's lister: a Douyin 合集 (its
link, or any episode's link → its 合集), a Douyin creator page, a Bilibili space / season /
series. Each video is marked with the project already made from it (``remote.project_index``:
its link or its video id), so only the new ones start ticked. The picked links go to the start
page's queue (E-9) with the start page's options and ONE series for the whole batch (an existing
profile, a new name that creates one, "auto" = each video's 《…》, "none") — so the series'
glossary, brief, style and Tự động đăng plan apply to every one. Every video still stops at the
edit gate: nothing here approves, renders or posts anything.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.config import settings

LIMIT = 100        # a page's newest videos (a 合集 is read whole: watch.MIX_LIMIT)
PAGE_KINDS = ("douyin_mix", "douyin_user", "bilibili_space", "bilibili_season", "bilibili_series")
STATES = {"rendered": "đã dựng", "approved": "đã duyệt", "gate": "chờ duyệt",
          "unfinished": "chưa xong"}


def is_page(url: str | None) -> bool:
    """A link to a page / 合集 of many videos (not one video) — the start page opens the
    picker for it instead of downloading. A Douyin video link is one video (the picker can
    still read its 合集). Pure."""
    from app.pipeline import watch

    urls = (url or "").split()
    return len(urls) == 1 and watch.source_kind(urls[0]) in PAGE_KINDS


def project_state(job_id: str) -> str:
    """"rendered" | "approved" | "gate" | "unfinished" for a project folder."""
    from app.models import Job
    from app.pipeline import approval

    d = Path(settings.vizsup_storage_dir) / job_id
    if (d / "output.mp4").exists():
        return "rendered"
    if not (d / "vi.srt").exists():
        return "unfinished"
    try:
        return "approved" if approval.is_approved(Job(id=job_id, work_dir=d)) else "gate"
    except Exception:  # noqa: BLE001 - a broken approval file reads as "not approved"
        return "gate"


def mark_local(videos: list[dict], index: dict[str, str]) -> list[dict]:
    """``videos`` with ``have``: the folder of a project already made from each (by its link or
    its video id in ``index``, ``remote.project_index``), else None. Pure."""
    from app.pipeline.watch import ids_in

    out = []
    for v in videos:
        url = str(v.get("url") or "").strip()
        keys = [url, str(v.get("id") or "").strip(), *sorted(ids_in(url))]
        out.append({**v, "have": next((index[k] for k in keys if k and k in index), None)})
    return out


def oldest_first(videos: list[dict]) -> list[dict]:
    """The order to run them in: by publish time when every video has one, else the page's
    order reversed (pages list the newest first) — episode 1 reaches the gate first and each
    episode's translation sees the one before it. Pure."""
    if videos and all(v.get("published") for v in videos):
        return sorted(videos, key=lambda v: v["published"])
    return list(reversed(videos))


def queue_items(base: dict, videos: list[dict], series: str) -> list[dict]:
    """The start page's queue items for the picked ``videos``: its options (``base``, from
    ``InputView._collect_opts``), each video's link, the batch's ``series`` (a slug, "auto" or
    "none"); oldest first, each link once. A picked .srt belongs to one video, so a batch finds
    its subtitles itself. Pure."""
    opts = {k: v for k, v in (base or {}).items() if k not in ("url", "file", "srt")}
    if opts.get("subtitle_source") == "srt":
        opts["subtitle_source"] = "auto"
    out, seen = [], set()
    for v in oldest_first(videos):
        url = str(v.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append({**opts, "url": url, "file": None, "series": series or "auto"})
    return out


def series_slug(choice: str | None) -> str:
    """"auto" / "none" / an existing profile's slug as they are; any other text is the name of
    a new series — its profile is created (or the one with that name's slug) → its slug."""
    from app.pipeline import series

    s = " ".join((choice or "").split())
    if not s or s in ("auto", "none"):
        return s or "auto"
    if series.load(s) is not None:
        return s
    return series.create(s).slug


def suggested_series(videos: list[dict]) -> str | None:
    """An existing series the whole page belongs to: the 合集's name, else the 《…》 name most
    of the titles carry — when a profile has it. None otherwise."""
    from app.pipeline import series

    mix = next((v["mix"] for v in videos if isinstance(v.get("mix"), dict)), None)
    names = [str(mix.get("name") or "")] if mix else []
    common = Counter(n for v in videos if (n := series.detect_name(v.get("title"))))
    if common:
        name, count = common.most_common(1)[0]
        if count * 2 >= len(videos):
            names.append(name)
    for name in names:
        name = name.strip().strip("《》").strip()
        if name:
            p = series.find_for_title(f"《{name}》")
            if p is not None:
                return p.slug
    return None


def friendly_error(exc: BaseException) -> str:
    """One readable Vietnamese line for a page that could not be read: ``list_videos`` already
    speaks Vietnamese (ValueError / RuntimeError); anything else is explained by its kind."""
    from app import net

    msg = " ".join(str(exc).split())
    if isinstance(exc, (ValueError, RuntimeError)) and msg and not net.looks_like_network_error(exc):
        return msg
    if net.looks_like_network_error(exc):
        return "Không kết nối được — kiểm tra mạng rồi bấm Tải lại."
    if isinstance(exc, ImportError):
        return f"Máy thiếu thành phần để đọc trang này ({exc.name or msg})."
    return f"Không đọc được danh sách video ({type(exc).__name__}: {msg[:200]})."


def load(url: str, *, lister=None, limit: int = LIMIT) -> dict:
    """List ``url`` and mark what the library has → {"kind", "videos": [video + have + state],
    "mix": {id, name, url} | None, "error": ""}. Never raises — a failure is the "error" text.
    Blocking (a Douyin page opens the embedded browser): never on the GUI thread."""
    from app.pipeline import remote, watch

    lister = lister or watch.list_videos
    url = (url or "").strip()
    kind = watch.source_kind(url)
    try:
        videos = [v for v in lister(url, limit=limit) if isinstance(v, dict) and v.get("url")]
    except Exception as exc:  # noqa: BLE001 - shown in the dialog, never a crash
        return {"kind": kind, "videos": [], "mix": None, "error": friendly_error(exc)}
    if not videos:
        return {"kind": kind, "videos": [], "mix": None,
                "error": "Trang này không có video nào đọc được."}
    try:
        index = remote.project_index()
    except OSError:
        index = {}
    rows = mark_local(videos, index)
    for r in rows:
        r["state"] = project_state(r["have"]) if r["have"] else ""
    mix = next((v["mix"] for v in videos if isinstance(v.get("mix"), dict)), None)
    return {"kind": kind, "videos": rows, "mix": mix, "error": ""}
