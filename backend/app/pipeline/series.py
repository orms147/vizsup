"""Series profile ("Bộ phim"): what one translated series keeps from episode to episode.

The owner translates a series episode by episode (《末日校车》 06, 07 …). Each episode used
to start cold: names, xưng hô, voice, subtitle look and render settings were redone by hand.
A profile lives in ``<storage>/_series/<slug>/series.json``::

    {name, match, glossary {zh: vi}, characters [{zh, vi, gender, note}], brief,
     style {style.json}, render {render_opts.json}, pronounce {written: spoken},
     autopilot {on, sources [urls], every_h, max_per_check}}

A project links to it through ``<job>/series_link.json`` ``{slug, name, episode, last}``;
the link is made automatically from the 《…》 in the title (or by the user's pick).

What flows where (every hook is best effort — a series problem never stops a run):

- **start of a run** (``resolve`` + ``inherit``): style.json / render_opts.json are copied
  into a project that has none;
- **translate**: ``glossary_for`` joins ``translate.load_glossary`` (above the auto-suggested
  terms, below the user's own global / project glossary), ``context_for`` adds the series
  sheet (brief + characters) and the last lines of the previous episode to the prompt;
- **TTS**: ``pronounce_for`` is a layer of ``speak.load_rules`` (global < series < project);
- **approval** (``learn``): the episode's suggested terms the user KEPT are merged into the
  series glossary — earlier episodes win — and a first style / render setting is remembered;
- **theo dõi tập mới** (``autopilot``): the creator pages ``pipeline/watch.py`` checks every
  ``every_h`` hours for new episodes (run to the edit gate, never approved). Off by default;
  keys this module does not know (platforms, slots…) are kept as they are.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.fsutil import atomic_write_text

log = logging.getLogger(__name__)

DIR = "_series"
FILE = "series.json"
LINK = "series_link.json"
LEARNED = "series_learned.json"
PREV_LINES = 8
SHEET_CHARS = 1500

_TITLE_NAME = re.compile(r"《([^《》]{1,40})》")


@dataclass
class Profile:
    slug: str
    name: str
    match: str = ""
    glossary: dict[str, str] = field(default_factory=dict)
    characters: list[dict] = field(default_factory=list)
    brief: str = ""
    style: dict = field(default_factory=dict)
    render: dict = field(default_factory=dict)
    pronounce: dict[str, str] = field(default_factory=dict)
    short: dict = field(default_factory=dict)       # the "Short" preset's settings (shorts.learn)
    autopilot: dict = field(default_factory=dict)   # "theo dõi tập mới" (autopilot_of)

    def to_dict(self) -> dict:
        return {"name": self.name, "match": self.match, "glossary": self.glossary,
                "characters": self.characters, "brief": self.brief, "style": self.style,
                "render": self.render, "pronounce": self.pronounce, "short": self.short,
                "autopilot": self.autopilot}

    @property
    def terms(self) -> int:
        return len(glossary_of(self))


# --- naming and storage ----------------------------------------------------------------

def detect_name(title: str | None) -> str | None:
    """The 《…》 text of a title ("第4集 | 《末日校车》06 …" → "末日校车"). Pure."""
    m = _TITLE_NAME.search(unicodedata.normalize("NFKC", title or ""))
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", s).strip("-. ")
    return s[:60] or "series"


def root() -> Path:
    from app.config import settings

    return Path(settings.vizsup_storage_dir) / DIR


def _clean_map(d) -> dict[str, str]:
    return {str(k).strip(): str(v) for k, v in d.items() if str(k).strip() and v is not None} \
        if isinstance(d, dict) else {}


def _from_dict(slug: str, d: dict) -> Profile:
    chars = [c for c in (d.get("characters") or []) if isinstance(c, dict) and str(c.get("zh", "")).strip()]
    return Profile(slug=slug, name=str(d.get("name") or slug), match=str(d.get("match") or ""),
                   glossary=_clean_map(d.get("glossary")), characters=chars,
                   brief=str(d.get("brief") or ""),
                   style=d["style"] if isinstance(d.get("style"), dict) else {},
                   render=d["render"] if isinstance(d.get("render"), dict) else {},
                   pronounce=_clean_map(d.get("pronounce")),
                   short=d["short"] if isinstance(d.get("short"), dict) else {},
                   autopilot=d["autopilot"] if isinstance(d.get("autopilot"), dict) else {})


def load(slug: str) -> Profile | None:
    try:
        data = json.loads((root() / slug / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _from_dict(slug, data) if isinstance(data, dict) else None


def save(p: Profile) -> None:
    d = root() / p.slug
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(d / FILE, json.dumps(p.to_dict(), ensure_ascii=False, indent=1))


def list_profiles() -> list[Profile]:
    out = []
    try:
        dirs = sorted(root().iterdir())
    except OSError:
        return out
    for d in dirs:
        p = load(d.name) if d.is_dir() else None
        if p:
            out.append(p)
    return out


def create(name: str, *, match: str | None = None) -> Profile:
    """A new empty profile for ``name`` (or the existing one with that slug)."""
    slug = slugify(name)
    return load(slug) or _saved(Profile(slug=slug, name=name, match=match or name))


def _saved(p: Profile) -> Profile:
    save(p)
    return p


# --- "theo dõi tập mới" ---------------------------------------------------------------------

AUTOPILOT_DEFAULTS = {"on": False, "sources": [], "every_h": 6, "max_per_check": 3}


def _int_in(v, lo: int, hi: int, default: int) -> int:
    try:
        return min(hi, max(lo, int(v)))
    except (TypeError, ValueError):
        return default


def normalize_autopilot(block) -> dict:
    """The defaults filled in, ``sources`` a de-duplicated list of stripped links, the hours /
    the per-check cap clamped; any other key is kept untouched. Pure."""
    out = {**AUTOPILOT_DEFAULTS, **(block if isinstance(block, dict) else {})}
    out["on"] = bool(out.get("on"))
    raw = out.get("sources")
    raw = raw.split() if isinstance(raw, str) else raw if isinstance(raw, list) else []
    sources: list[str] = []
    for s in raw:
        s = str(s).strip()
        if s and s not in sources:
            sources.append(s)
    out["sources"] = sources
    out["every_h"] = _int_in(out.get("every_h"), 1, 168, AUTOPILOT_DEFAULTS["every_h"])
    out["max_per_check"] = _int_in(out.get("max_per_check"), 1, 20,
                                   AUTOPILOT_DEFAULTS["max_per_check"])
    return out


def autopilot_of(p: Profile) -> dict:
    return normalize_autopilot(p.autopilot)


def load_autopilot(slug: str) -> dict | None:
    """The profile's watch settings (defaults when it never had any); None = no profile."""
    p = load(slug)
    return autopilot_of(p) if p else None


def save_autopilot(slug: str, **changes) -> Profile | None:
    """Merge ``changes`` (on / sources / every_h / …) into the profile's block, read fresh
    from disk so nothing else in the profile is overwritten. None = no profile."""
    p = load(slug)
    if p is None:
        return None
    p.autopilot = normalize_autopilot({**p.autopilot, **changes})
    save(p)
    return p


def followed() -> list[Profile]:
    """Profiles whose watch is on and has at least one page to look at."""
    out = []
    for p in list_profiles():
        ap = autopilot_of(p)
        if ap["on"] and ap["sources"]:
            out.append(p)
    return out


# --- the project's link ------------------------------------------------------------------

def link_of(work_dir: Path) -> dict | None:
    try:
        d = json.loads((Path(work_dir) / LINK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("slug") else None


def profile_of(job) -> Profile | None:
    """The profile this project is linked to, if any."""
    lk = link_of(job.work_dir)
    return load(lk["slug"]) if lk else None


def _write_link(job, p: Profile) -> None:
    from app.pipeline.compilation import episode_range

    ep = episode_range((job.metadata or {}).get("title"))
    atomic_write_text(job.work_dir / LINK, json.dumps(
        {"slug": p.slug, "name": p.name, "episode": ep[0] if ep else None,
         "last": ep[1] if ep else None}, ensure_ascii=False, indent=1))


def find_for_title(title: str | None) -> Profile | None:
    """An existing profile whose ``match`` text is in the title (《…》 name first)."""
    t = unicodedata.normalize("NFKC", title or "")
    name = detect_name(t)
    if name and (p := load(slugify(name))):
        return p
    for p in list_profiles():
        if p.match and unicodedata.normalize("NFKC", p.match) in t:
            return p
    return None


def resolve(job, choice: str | None = "auto", *, create_new: bool = True) -> Profile | None:
    """Link ``job`` to a profile and return it. ``choice``: "none" (no series — an old link
    is dropped), a slug (the user's pick), or "auto" (keep the link, else find / create one
    from the 《…》 in the title). A title without 《…》 and no matching profile → None."""
    choice = (choice or "auto").strip()
    if choice == "none":
        (job.work_dir / LINK).unlink(missing_ok=True)
        return None
    p: Profile | None = None
    if choice != "auto":
        p = load(choice)
    if p is None and choice == "auto":
        p = profile_of(job)
    title = (job.metadata or {}).get("title")
    if p is None and choice == "auto":
        p = find_for_title(title)
        if p is None and create_new and (name := detect_name(title)):
            p = create(name)
    if p is not None:
        _write_link(job, p)
    return p


# --- what a profile contributes ------------------------------------------------------------

def glossary_of(p: Profile) -> dict[str, str]:
    """zh → vi: the profile's terms plus its characters' names (so the glossary check
    flags a line that dropped a name)."""
    out = {str(c["zh"]).strip(): str(c["vi"]).strip() for c in p.characters
           if str(c.get("vi", "")).strip()}
    out.update(p.glossary)                 # an explicit glossary entry beats the character list
    return out


def glossary_for(job) -> dict[str, str]:
    p = profile_of(job)
    return glossary_of(p) if p else {}


def pronounce_for(job) -> dict[str, str]:
    p = profile_of(job)
    return dict(p.pronounce) if p else {}


def sheet(p: Profile) -> str:
    """The series part of the translator's brief: fixed xưng hô, names and tone."""
    parts = []
    if p.brief.strip():
        parts.append(p.brief.strip())
    chars = []
    for c in p.characters[:30]:
        line = f"{str(c['zh']).strip()} → {str(c.get('vi', '')).strip()}"
        extra = "; ".join(x for x in (str(c.get("gender") or "").strip(),
                                      str(c.get("note") or "").strip()) if x)
        chars.append(f"- {line}" + (f" ({extra})" if extra else ""))
    if chars:
        parts.append("Nhân vật (giữ nguyên ở mọi tập):\n" + "\n".join(chars))
    return "\n\n".join(parts)[:SHEET_CHARS].strip()


def summary(p: Profile | None) -> str:
    if p is None:
        return ""
    return f"Bộ phim: 《{p.name}》 · {p.terms} thuật ngữ"


def _prev_episode(job, lk: dict) -> Path | None:
    """The project of the latest EARLIER episode of the same series (approved, with a
    vi.srt). Episode numbers come from the link files; a range counts by its last number."""
    from app.config import settings
    from app.pipeline import approval
    from app.models import Job

    ep = lk.get("episode")
    if ep is None:
        return None
    best: tuple[int, Path] | None = None
    base = Path(settings.vizsup_storage_dir)
    try:
        dirs = list(base.iterdir())
    except OSError:
        return None
    for d in dirs:
        if not d.is_dir() or d.name.startswith(("_", ".")) or d.resolve() == job.work_dir.resolve():
            continue
        other = link_of(d)
        if not other or other.get("slug") != lk["slug"] or not (d / "vi.srt").exists():
            continue
        last = other.get("last") if other.get("last") is not None else other.get("episode")
        if last is None or last >= ep:
            continue
        try:
            if not approval.is_approved(Job(id=d.name, work_dir=d)):
                continue
        except Exception:  # noqa: BLE001
            continue
        if best is None or last > best[0]:
            best = (last, d)
    return best[1] if best else None


def previous_lines(job, n: int = PREV_LINES) -> list[str]:
    """The last ``n`` approved Vietnamese lines of the previous episode (continuity)."""
    from app.pipeline.srt import parse_srt

    lk = link_of(job.work_dir)
    prev = _prev_episode(job, lk) if lk else None
    if prev is None:
        return []
    try:
        cues = parse_srt(prev / "vi.srt")
    except Exception:  # noqa: BLE001
        return []
    return [" ".join(c.text.split()) for c in cues[-n:] if c.text.strip()]


def context_for(job) -> dict:
    """Private translate-context keys: ``_series`` (sheet) and ``_prev`` (lines)."""
    out: dict = {}
    p = profile_of(job)
    if p:
        if s := sheet(p):
            out["_series"] = s
        if prev := previous_lines(job):
            out["_prev"] = prev
    return out


# --- start of a run ------------------------------------------------------------------------

def inherit(job, p: Profile, *, log_fn=None) -> list[str]:
    """Copy the profile's style / render settings into a project that has none.
    Returns what was copied."""
    from app.pipeline import batch

    done = []
    if p.style and not (job.work_dir / "style.json").exists():
        atomic_write_text(job.work_dir / "style.json", json.dumps(p.style, ensure_ascii=False, indent=1))
        done.append("kiểu phụ đề")
    if p.render and not (job.work_dir / batch.RENDER_OPTS).exists():
        batch.save_opts(job.work_dir, _render_opts(p.render))
        done.append("cài đặt dựng")
    if log_fn and done:
        log_fn(f"↷ Bộ phim 《{p.name}》: dùng lại " + " + ".join(done))
    return done


# --- after approval --------------------------------------------------------------------------

def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def learn(job) -> dict:
    """Approved episode → series. The suggested terms (``glossary.suggested.json``) whose
    Chinese is in this episode and whose Vietnamese the user KEPT in vi.srt are merged
    into the series glossary — an earlier episode's rendering wins. A first subtitle
    style is remembered when the profile has none. Writes ``series_learned.json`` and
    returns ``{"added": {...}, "kept": n, "style": bool}``; {} when not in a series."""
    p = profile_of(job)
    if p is None:
        return {}
    try:
        cn = job.cn_srt.read_text(encoding="utf-8") if job.cn_srt.exists() else ""
        vi = job.vi_srt.read_text(encoding="utf-8").casefold() if job.vi_srt.exists() else ""
    except OSError:
        return {}
    known = glossary_of(p)
    added = {}
    for zh, term in _clean_map(_read(job.work_dir / "glossary.suggested.json")).items():
        if zh in known or not term.strip():
            continue                                     # earlier wins
        if zh in cn and term.strip().casefold() in vi:
            added[zh] = term.strip()
    style = False
    if not p.style and (job.work_dir / "style.json").exists():
        st = _read(job.work_dir / "style.json")
        if st:
            p.style, style = st, True
    if added or style:
        p.glossary.update(added)
        save(p)
        log.info("series %s: +%d terms%s", p.slug, len(added), ", style" if style else "")
    rep = {"added": added, "kept": len(known), "style": style,
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        atomic_write_text(job.work_dir / LEARNED, json.dumps(rep, ensure_ascii=False, indent=1))
    except OSError:
        pass
    return rep


def _render_opts(opts: dict) -> dict:
    """Render settings without the per-run keys (a quick proof, the burned-cue snapshot)."""
    return {k: v for k, v in opts.items()
            if k not in ("burn_cues", "preview") and not str(k).startswith("_")}


def learn_render(work_dir: Path, opts: dict) -> bool:
    """A real render's settings become the series' when it has none yet."""
    lk = link_of(work_dir)
    p = load(lk["slug"]) if lk else None
    if p is None or p.render:
        return False
    p.render = _render_opts(opts)
    save(p)
    return True

