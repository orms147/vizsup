"""Vietnamese subtitle lint ("Soát lỗi tiếng Việt") — deterministic, free, local.

Two halves, both pure:

**Auto-fix** (``fix_text`` — safe typography only, nothing that could change the meaning):
fullwidth punctuation (，。！？：；（）) → ASCII, ``...`` / ``..`` / ``……`` → ``…``, a space
before ``, . ! ? : ; …`` removed, the missing space after ``, ; ! ? … :`` (and after a sentence
dot between a lowercase and a capital letter: ``ok.Tiếp``) added, double spaces collapsed,
straight ``"…"`` pairs → “…”, and an accidentally repeated FUNCTION word (``là là``,
``của của`` — never ``từ từ`` / ``được được``, which are real Vietnamese). A lone trailing comma
is deliberately NOT touched: per-clause subtitles end on commas all the time.

**Flags** (``flags`` — reported into the editor's review system, ``review.cue_issues``):

- ``han``   — Chinese characters left in the Vietnamese line;
- ``latin`` — an ASCII word that cannot be a Vietnamese syllable (pinyin such as ``zhang`` /
              ``xiao``, an English name) and is not in the glossary or the allow-list;
- ``num``   — digits in the Chinese line that are neither in the Vietnamese line as digits nor
              spelled out in words;
- ``term``  — a glossary term is in the Chinese line but its Vietnamese rendering is not in the
              Vietnamese one (live; the translate-time report goes stale after edits);
- ``pron``  — the line uses a pronoun the series sheet explicitly forbids ("không dùng: tao,
              mày" in the brief or a character's note). Who is speaking is unknown, so nothing
              is inferred from the series' xưng hô notes except such an explicit ban.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# --- auto-fix ------------------------------------------------------------------------------

FIX_LABELS = {
    "fullwidth": "dấu câu toàn chiều rộng", "ellipsis": "dấu ba chấm", "space_before": "dấu cách thừa trước dấu câu",
    "space_after": "thiếu dấu cách sau dấu câu", "double_space": "dấu cách đôi", "quotes": "ngoặc kép thẳng",
    "repeat": "từ lặp liền kề",
}
_FULLWIDTH = str.maketrans({"，": ",", "。": ".", "！": "!", "？": "?", "：": ":", "；": ";",
                            "（": "(", "）": ")", "、": ","})
_FUNCTION_WORDS = ("là", "của", "và", "thì", "mà", "ở", "để", "với", "trong", "những", "các",
                   "này", "đó", "bị")
_REPEAT = re.compile(r"(?<!\w)(" + "|".join(_FUNCTION_WORDS) + r")(?:[ \t]+\1)+(?!\w)",
                     re.IGNORECASE)


def _run_around(s: str, i: int) -> str:
    """The non-space run containing index ``i`` (to recognise URLs / e-mails)."""
    a = i
    while a > 0 and not s[a - 1].isspace():
        a -= 1
    b = i
    while b < len(s) and not s[b].isspace():
        b += 1
    return s[a:b]


def _space_after(s: str) -> str:
    def after(m: re.Match[str]) -> str:
        return m.group(0) if re.search(r"[/@]|https?:|www\.", _run_around(s, m.start())) \
            else m.group(1) + " "

    s = re.sub(r"([,;!?…])(?=[^\W\d_])", after, s)
    s = re.sub(r"(?<=[^\W\d_]):(?=[^\W\d_])", lambda m: ": " if not re.search(
        r"[/@]|https?:|www\.", _run_around(s, m.start())) else ":", s)
    # a sentence dot glued to the next sentence: "ok.Tiếp" (not TP.HCM, v.v., 3.5, a.b)
    return re.sub(r"(?<=[a-zà-ỹ])\.(?=[A-ZÀ-Ỹ][a-zà-ỹ])", lambda m: ". " if not re.search(
        r"[/@]|https?:|www\.", _run_around(s, m.start())) else ".", s)


def _quotes(s: str) -> str:
    if s.count('"') == 0 or s.count('"') % 2:
        return s
    n = [0]

    def sub(_m: re.Match[str]) -> str:
        n[0] += 1
        return "“" if n[0] % 2 else "”"

    return re.sub('"', sub, s)


def fix_line(s: str) -> tuple[str, set[str]]:
    done: set[str] = set()

    def step(key: str, new: str) -> None:
        nonlocal s
        if new != s:
            done.add(key)
            s = new

    step("fullwidth", s.translate(_FULLWIDTH))
    step("ellipsis", re.sub(r"\.{2,}|…+\.*|\.+…+", "…", s))
    step("space_before", re.sub(r"[ \t]+(?=[,.!?:;…])", "", s))
    step("space_after", _space_after(s))
    step("double_space", re.sub(r"[ \t]{2,}", " ", s).strip(" \t"))
    step("quotes", _quotes(s))
    step("repeat", _REPEAT.sub(lambda m: m.group(1), s))
    return s, done


def fix_text(text: str) -> tuple[str, list[str]]:
    """Safe typography fixes for one subtitle (each of its lines). Returns the new text and
    the FIX_LABELS keys that fired, in a stable order."""
    text = unicodedata.normalize("NFC", text or "")
    lines, fired = [], set()
    for ln in text.split("\n"):
        new, done = fix_line(ln)
        lines.append(new)
        fired |= done
    return "\n".join(lines), [k for k in FIX_LABELS if k in fired]


def fix_cues(cues: Iterable, *, attr: str = "text") -> dict[str, int]:
    """Apply :func:`fix_text` to ``attr`` of every cue in place; returns {rule: lines fixed}."""
    counts: dict[str, int] = {}
    for c in cues:
        new, fired = fix_text(getattr(c, attr))
        if fired:
            setattr(c, attr, new)
            for k in fired:
                counts[k] = counts.get(k, 0) + 1
    return counts


def describe(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{FIX_LABELS[k]} ({n})" for k, n in counts.items() if k in FIX_LABELS)


def fix_job(job, *, log: Callable[[str], None] | None = None) -> dict[str, int]:
    """Pipeline step after translate: fix ``vi.srt`` (and its machine copy ``vi.mt.srt``,
    so the user's-edits learning does not see these fixes as the user's own). Returns the
    counts; {} when nothing changed or there is no vi.srt."""
    from app.pipeline.srt import parse_srt, write_srt

    if not job.vi_srt.exists():
        return {}
    cues = parse_srt(job.vi_srt)
    counts = fix_cues(cues)
    if counts:
        write_srt(cues, job.vi_srt)
        mt = job.work_dir / "vi.mt.srt"
        if mt.exists():
            mt_cues = parse_srt(mt)
            fix_cues(mt_cues)
            write_srt(mt_cues, mt)
    if log:
        log("✓ Soát lỗi tiếng Việt: " + (f"đã sửa {sum(counts.values())} chỗ — {describe(counts)}"
                                          if counts else "không có lỗi trình bày"))
    return counts


# --- flags -------------------------------------------------------------------------------------

_INIT = r"(?:ngh|ng|nh|ph|th|tr|ch|kh|gh|gi|qu|[bcdghklmnprstvx])?"
_NUCLEI = ("uyeu", "uyen", "ieu", "oai", "oao", "oay", "oeo", "uai", "uay", "uoi", "uya", "uye",
           "uyu", "yeu", "ai", "ao", "au", "ay", "eo", "eu", "ia", "ie", "iu", "oa", "oe", "oi",
           "oo", "ua", "ue", "ui", "uo", "uu", "uy", "ye", "a", "e", "i", "o", "u", "y")
_SYLLABLE = re.compile(_INIT + "(?:" + "|".join(sorted(_NUCLEI, key=len, reverse=True)) + ")"
                       + r"(?:ch|ng|nh|[cmnpt])?")
_ASCII_WORD = re.compile(r"(?<![\w'’])[A-Za-z]{3,}(?![\w'’])")
ALLOWED_LATIN = frozenset("""ok okay wifi video vlog app apps shop show game games online offline live
like share fan fans idol boss cool bye hello sorry party email internet facebook tiktok youtube
douyin bilibili wechat google iphone android pizza cafe karaoke concert sale menu mini kpop
laptop youtuber follow stream hashtag""".split())


def is_vietnamese_syllable(word: str) -> bool:
    """Could this ASCII word (accents stripped) be one Vietnamese syllable? ``zhang``,
    ``xiao``, ``shuo``, ``wang`` can not; ``lan``, ``minh``, ``dong`` can."""
    return bool(_SYLLABLE.fullmatch(word.lower()))


def latin_words(text: str, known: Iterable[str] = ()) -> list[str]:
    """ASCII words in ``text`` that are not Vietnamese syllables, ALL-CAPS acronyms, CamelCase
    brands, allow-listed loanwords or in ``known`` (the glossary's Vietnamese words)."""
    skip = {w.casefold() for w in known} | ALLOWED_LATIN
    out = []
    for m in _ASCII_WORD.finditer(text):
        w = m.group(0)
        if w.isupper() or any(ch.isupper() for ch in w[1:]):
            continue
        if w.casefold() in skip or is_vietnamese_syllable(w):
            continue
        out.append(w)
    return out


_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_missing(zh: str, vi: str) -> list[str]:
    """Digit tokens of the Chinese line that the Vietnamese line has neither as digits nor
    in words ("15" ↔ "mười lăm")."""
    from app.pipeline import speak

    zh = unicodedata.normalize("NFKC", zh or "")
    vi_n = unicodedata.normalize("NFKC", vi or "")
    have = {re.sub(r"[.,]", "", t) for t in _DIGITS.findall(vi_n)}
    words = vi_n.casefold()
    out = []
    for tok in _DIGITS.findall(zh):
        flat = re.sub(r"[.,]", "", tok)
        if flat in have or tok in vi_n:
            continue
        try:
            spoken = speak.read_token(tok).casefold()
        except (ValueError, OverflowError):
            spoken = ""
        if spoken and spoken in words:
            continue
        out.append(tok)
    return out


def glossary_missing(zh: str, vi: str, glossary: Mapping[str, str]) -> list[str]:
    """Glossary source terms present in ``zh`` whose Vietnamese rendering is absent from ``vi``."""
    low = (vi or "").casefold()
    return [k for k, v in glossary.items() if k and v and k in (zh or "") and v.casefold() not in low]


PRONOUNS = frozenset("""tôi tao ta mình anh em cậu mày tớ bạn ông bà cô chú bác cháu con chị thầy
ngươi hắn nó y nàng chàng trẫm bổn""".split())
_BAN = re.compile(r"(?:không dùng|cấm dùng|cấm|tránh dùng|tránh)\s*:?\s*([^\n.;]+)", re.IGNORECASE)


def forbidden_pronouns(*notes: str) -> frozenset[str]:
    """Pronouns an explicit ban in ``notes`` names ("không dùng: tao, mày"). Only words of
    PRONOUNS count, so a sentence like "tránh dùng từ lóng" bans nothing."""
    out: set[str] = set()
    for note in notes:
        for m in _BAN.finditer(note or ""):
            out |= {w for w in re.findall(r"[^\W\d_]+", m.group(1).casefold()) if w in PRONOUNS}
    return frozenset(out)


def flags(vi: str, zh: str = "", *, glossary: Mapping[str, str] | None = None,
          forbidden: frozenset[str] = frozenset()) -> list[str]:
    """Review-flag keys for one line: han, num, pron, term, latin (in that order)."""
    out: list[str] = []
    if not (vi or "").strip():
        return out
    if HAN.search(vi):
        out.append("han")
    if zh and numbers_missing(zh, vi):
        out.append("num")
    if forbidden and forbidden & set(re.findall(r"[^\W\d_]+", vi.casefold())):
        out.append("pron")
    if glossary and zh and glossary_missing(zh, vi, glossary):
        out.append("term")
    if latin_words(vi, [w for v in (glossary or {}).values() for w in re.findall(r"[A-Za-z]+", v)]):
        out.append("latin")
    return out


# --- per-project context (glossary + series ban), cached by file stamps ------------------------

_CACHE: dict[str, tuple[tuple, dict]] = {}


def context(job) -> dict:
    """``{"glossary": {zh: vi}, "forbidden": frozenset}`` for ``job`` — what :func:`flags`
    needs; re-read only when one of the source files changed."""
    from app.config import settings
    from app.pipeline import series, translate

    work = Path(job.work_dir)
    lk = series.link_of(work)
    files = [work / "glossary.suggested.json", work / "glossary.json",
             Path(settings.vizsup_storage_dir) / "glossary.json", work / series.LINK]
    if lk:
        files.append(series.root() / lk["slug"] / series.FILE)

    def stamp(p: Path):
        try:
            st = p.stat()
            return (str(p), st.st_mtime_ns, st.st_size)
        except OSError:
            return (str(p), 0, 0)

    key = tuple(stamp(p) for p in files)
    hit = _CACHE.get(str(work))
    if hit and hit[0] == key:
        return hit[1]
    prof = series.profile_of(job)
    notes = [prof.brief] + [str(c.get("note", "")) for c in prof.characters] if prof else []
    ctx = {"glossary": translate.load_glossary(job), "forbidden": forbidden_pronouns(*notes)}
    _CACHE[str(work)] = (key, ctx)
    return ctx
