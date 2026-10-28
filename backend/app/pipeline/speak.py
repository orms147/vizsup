"""Spoken text: what the Vietnamese voice should SAY for a subtitle line.

A subtitle reads "15%", "13:05", "5km", "TP.HCM" — an edge-tts voice reads those badly
(digit by digit, in English, or not at all). ``spoken()`` turns the line into plain
Vietnamese words for the TTS input ONLY: the burned subtitles and ``vi.srt`` keep the
written form. The result is part of the TTS cache key (``tts._seg_hash``), so changing a
rule re-voices exactly the lines it touches.

Layers, in order (pure functions, no I/O except ``load_rules`` / ``save_rules``):

1. emoji and decoration symbols dropped;
2. the user's pronunciation dictionary (``<storage>/pronounce.json`` then
   ``<job>/pronounce.json``, the project wins) — whole word, case-insensitive; a key that
   is ALL CAPS (``AI``, ``CEO``) matches only that spelling, or "AI" would also re-read
   the Vietnamese word "ai";
3. built-in abbreviations (VN, TP.HCM, AI, CEO, 5G, OK …);
4. times, dates, currency, k/tr, ``5h30``, ``thứ 2`` / ``tháng 4``;
5. numbers (+ units and %) read the Vietnamese way;
6. punctuation: ``...`` becomes a pause, repeated marks collapse, dashes become a pause.

Anything not covered (Latin/English words, unknown acronyms) is left as is.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from app.fsutil import atomic_write_text

FILE = "pronounce.json"

# --- Vietnamese number reader ------------------------------------------------------------

_DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALES = ("", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ")
MAX_DIGITS = 15   # beyond that (ids, card numbers) the digits are read one by one


def _two(t: int, u: int) -> str:
    """Tens and units (0..99) of a group, e.g. 25 → "hai mươi lăm"."""
    if t == 0:
        return _DIGITS[u]
    if t == 1:
        return "mười" + (" lăm" if u == 5 else f" {_DIGITS[u]}" if u else "")
    tail = {0: "", 1: " mốt", 5: " lăm"}.get(u, f" {_DIGITS[u]}")
    return f"{_DIGITS[t]} mươi{tail}"


def _group(n: int, full: bool) -> str:
    """One 0..999 group. ``full``: a higher group exists, so 5 is "không trăm lẻ năm"."""
    h, rest = divmod(n, 100)
    t, u = divmod(rest, 10)
    parts: list[str] = []
    if h or full:
        parts.append(f"{_DIGITS[h]} trăm")
    if t == 0 and u:
        parts.append(f"lẻ {_DIGITS[u]}" if (h or full) else _DIGITS[u])
    elif t or u:
        parts.append(_two(t, u))
    return " ".join(parts)


def read_digits(s: str) -> str:
    """One by one: "0912" → "không chín một hai" (phone numbers, ids)."""
    return " ".join(_DIGITS[int(ch)] for ch in s if ch.isdigit())


def read_number(n: int) -> str:
    """A non-negative integer in words: 2026 → "hai nghìn không trăm hai mươi sáu"."""
    if n < 0:
        return "âm " + read_number(-n)
    if n == 0:
        return _DIGITS[0]
    if len(str(n)) > MAX_DIGITS:
        return read_digits(str(n))
    groups: list[int] = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    out: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        words = _group(g, full=i < len(groups) - 1)
        out.append(f"{words} {_SCALES[i]}".strip())
    return " ".join(out)


_NUM = r"\d+(?:[.,]\d+)*"
_LETTER_BEFORE = r"(?<![^\W\d_])"     # not glued to a letter on the left  (A4, mp3 stay)
_LETTER_AFTER = r"(?![^\W\d_])"


def read_token(tok: str) -> str:
    """A digit token with optional ``.``/``,`` separators. Vietnamese convention: ``.``
    groups thousands (1.000.000) and ``,`` is the decimal mark (1,5); both orders and
    version/IP-like tokens (1.2.3) are told apart by the shape of the groups."""
    if tok.isdigit():
        if len(tok) > 1 and tok[0] == "0":
            return read_digits(tok)            # 0912…, 007: not a quantity
        return read_number(int(tok))
    pieces = re.split(r"[.,]", tok)
    seps = re.findall(r"[.,]", tok)
    three = all(len(p) == 3 for p in pieces[1:])
    if len(set(seps)) == 1:
        if three and 1 <= len(pieces[0]) <= 3 and pieces[0] != "0" \
                and (len(seps) > 1 or seps[0] == "."):
            return read_number(int("".join(pieces)))             # 1.234 / 1,234,567
        if len(seps) == 1:
            return f"{read_token(pieces[0])} phẩy {read_digits(pieces[1])}"
        return " chấm ".join(read_token(p) for p in pieces)       # 192.168.1.1
    # mixed: the LAST separator is the decimal mark (1.234,56 and 1,234.56)
    head, frac = tok[:max(tok.rfind(","), tok.rfind("."))], pieces[-1]
    whole = int(re.sub(r"[.,]", "", head))
    return f"{read_number(whole)} phẩy {read_digits(frac)}"


# --- units, currency, abbreviations -------------------------------------------------------

# after a number, with an optional space ("5 km")
_UNITS_SPACED = {
    "km/h": "ki lô mét trên giờ", "m/s": "mét trên giây",
    "km²": "ki lô mét vuông", "km2": "ki lô mét vuông",
    "m²": "mét vuông", "m2": "mét vuông", "m³": "mét khối", "m3": "mét khối",
    "km": "ki lô mét", "cm": "xen ti mét", "mm": "mi li mét",
    "kg": "ki lô gam", "mg": "mi li gam", "ml": "mi li lít",
    "GB": "gi ga bai", "MB": "mê ga bai", "KB": "ki lô bai", "TB": "tê ra bai",
    "kWh": "ki lô oát giờ", "kW": "ki lô oát", "MHz": "mê ga héc", "GHz": "gi ga héc",
    "kHz": "ki lô héc", "Hz": "héc",
    "VNĐ": "đồng", "VND": "đồng", "vnđ": "đồng", "USD": "đô la Mỹ",
    "RMB": "nhân dân tệ", "CNY": "nhân dân tệ",
}
# only glued to the number ("5m", "50%", "36°C") — too ambiguous with a space
_UNITS_GLUED = {
    "%": "phần trăm", "°C": "độ xê", "℃": "độ xê", "°F": "độ ép", "°": "độ",
    "m": "mét", "g": "gam", "W": "oát", "V": "vôn", "đ": "đồng", "₫": "đồng",
    "¥": "tệ", "￥": "tệ", "元": "tệ",
}
_CURRENCY_PREFIX = {"$": "đô la", "€": "ơ rô", "£": "bảng Anh", "¥": "tệ", "￥": "tệ"}
_MAGNITUDE = r"(?:\s?(?:tỷ|tỉ|triệu|nghìn|ngàn|k|tr))?"


def _alt(keys) -> str:
    return "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))


_NUMBER_RE = re.compile(
    rf"{_LETTER_BEFORE}(?P<neg>(?<![\w.,)])-(?=\d))?(?P<num>{_NUM})"
    rf"(?:[  ]?(?P<u1>{_alt(_UNITS_SPACED)})|(?P<u2>{_alt(_UNITS_GLUED)}))?"
    rf"{_LETTER_AFTER}")

# Written → spoken. Keys that are ALL CAPS match only that spelling (see ``compile_rules``).
BUILTIN: dict[str, str] = {
    "TP.HCM": "thành phố Hồ Chí Minh", "TP. HCM": "thành phố Hồ Chí Minh",
    "TPHCM": "thành phố Hồ Chí Minh", "TP HCM": "thành phố Hồ Chí Minh",
    "VN": "Việt Nam", "AI": "ây ai", "CEO": "xi i âu", "ok": "ô kê",
    "TV": "ti vi", "PC": "pi xi", "GDP": "gi đi pi", "DNA": "đi en ây", "GPS": "gi pi ét",
    "IQ": "ai kiu", "VIP": "víp", "USB": "diu ét bi", "SMS": "ét em ét", "MV": "em vi",
    "CCTV": "xi xi ti vi", "24/7": "hai mươi bốn trên bảy",
    "2G": "hai gi", "3G": "ba gi", "4G": "bốn gi", "5G": "năm gi",
    "2K": "hai ca", "4K": "bốn ca", "8K": "tám ca",
}


def _is_cased(key: str) -> bool:
    """ALL-CAPS key of 2+ letters (AI, CEO, 5G): matched with exactly that casing."""
    return key.isupper() and sum(ch.isalpha() for ch in key) >= 2


@lru_cache(maxsize=16)
def _compile(items: tuple[tuple[str, str], ...]) -> tuple[re.Pattern[str] | None, tuple[str, ...]]:
    """One alternation, longest key first (ties keep ``items`` order, so earlier = higher
    priority); group k+1 ↔ values[k]."""
    entries = [(k, v) for k, v in items if k]
    entries.sort(key=lambda kv: -len(kv[0]))     # stable
    if not entries:
        return None, ()
    alts = []
    for k, _v in entries:
        pat = re.escape(k)
        pat = (r"(?<!\w)" if k[0].isalnum() or k[0] == "_" else "") + pat + \
              (r"(?!\w)" if k[-1].isalnum() or k[-1] == "_" else "")
        alts.append(f"({pat})" if _is_cased(k) else f"((?i:{pat}))")
    return re.compile("|".join(alts)), tuple(v for _k, v in entries)


def apply_rules(text: str, rules: Mapping[str, str]) -> str:
    items = tuple((unicodedata.normalize("NFC", str(k)).strip(), str(v)) for k, v in rules.items())
    rx, values = _compile(items)
    if rx is None:
        return text
    return rx.sub(lambda m: values[m.lastindex - 1], text)


# --- the layers ---------------------------------------------------------------------------

_DECOR = re.compile("[\U0001F000-\U0001FAFF←-⇿⌀-⏿☀-➿⬀-⯿"
                    "️‍⃣\U000E0000-\U000E007F]")


def _hhmm(h: str, m: str, s: str | None = None, *, minutes_word: bool = True) -> str:
    out = f"{read_number(int(h))} giờ"
    mi = int(m)
    if mi:
        out += f" lẻ {_DIGITS[mi]}" if mi < 10 else f" {read_number(mi)}"
        out += " phút" if minutes_word else ""
    if s and int(s):
        out += f" {read_number(int(s))} giây"
    return out


def _month(m: int) -> str:
    return "tư" if m == 4 else read_number(m)


def _ymd(y: int, m: int, d: int) -> str | None:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"ngày {read_number(d)} tháng {_month(m)} năm {read_number(y)}"


def _times_dates(s: str) -> str:
    s = re.sub(r"(?<![\d:.])([01]?\d|2[0-4]):([0-5]\d)(?::([0-5]\d))?(?![\d:])",
               lambda m: _hhmm(m[1], m[2], m[3]), s)
    s = re.sub(rf"{_LETTER_BEFORE}(\d{{1,2}})h(\d{{2}})?{_LETTER_AFTER}",
               lambda m: _hhmm(m[1], m[2] or "0", minutes_word=False), s)

    def iso(m: re.Match[str]) -> str:
        return _ymd(int(m[1]), int(m[2]), int(m[3])) or m[0]

    def dmy(m: re.Match[str]) -> str:
        return _ymd(int(m[3]), int(m[2]), int(m[1])) or m[0]

    def dm(m: re.Match[str]) -> str:
        d, mo = int(m[1]), int(m[2])
        if len(m[1]) == 1 and len(m[2]) == 1 or not (1 <= mo <= 12 and 1 <= d <= 31):
            return f"{read_number(d)} phần {read_number(mo)}"          # 1/2, 3/4, 15/20
        return f"ngày {read_number(d)} tháng {_month(mo)}"

    s = re.sub(r"(?<![\d/.-])(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?![\d/])", iso, s)
    s = re.sub(r"(?<![\d/.-])(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?![\d/])", dmy, s)
    s = re.sub(r"(?<![\d/.-])(\d{1,2})/(\d{1,2})(?![\d/])", dm, s)
    return re.sub(r"([Nn]gày)\s+ngày\b", r"\1", s)     # "ngày 12/10" already said it


def _ordinals(s: str) -> str:
    """"thứ 2" → "thứ hai", "thứ 1" → "thứ nhất", "tháng 4" → "tháng tư"."""
    def one(m: re.Match[str]) -> str:
        word, n = m[1], int(m[2])
        if n == 4:
            tail = "tư"
        elif n == 1 and word.casefold() == "thứ":
            tail = "nhất"
        else:
            tail = read_number(n)
        return f"{word} {tail}"

    return re.sub(rf"(?<!\w)([Tt]hứ|[Tt]háng)\s+(\d{{1,2}})(?![\d]|[.,]\d){_LETTER_AFTER}", one, s)


def _money_and_shorthand(s: str) -> str:
    # "$5 triệu" → "5 triệu đô la"; "¥20" → "20 tệ"
    sym = "".join(re.escape(c) for c in _CURRENCY_PREFIX)
    s = re.sub(rf"([{sym}])\s?({_NUM}){_MAGNITUDE}(?![^\W\d_])",
               lambda m: f"{m[0][1:].strip()} {_CURRENCY_PREFIX[m[1]]}", s)
    # 10k → 10 nghìn, 2tr → 2 triệu (glued, so "5km" and "2 tr" words are not touched)
    return re.sub(rf"{_LETTER_BEFORE}({_NUM})(k|tr){_LETTER_AFTER}",
                  lambda m: f"{m[1]} {'nghìn' if m[2] == 'k' else 'triệu'}", s)


def _ranges(s: str) -> str:
    n = r"\d{1,6}(?:[.,]\d+)?"
    return re.sub(rf"(?<![\w.,/-])({n})\s?[-–~～]\s?({n})(?![\d-]|[.,]\d)", r"\1 đến \2", s)


def _numbers(s: str) -> str:
    def one(m: re.Match[str]) -> str:
        words = read_token(m["num"])
        if m["neg"]:
            words = "âm " + words
        unit = m["u1"] or m["u2"]
        if unit:
            words += " " + (_UNITS_SPACED.get(unit) or _UNITS_GLUED[unit])
        return words

    return _NUMBER_RE.sub(one, s)


def _punctuation(s: str) -> str:
    s = re.sub(r"\s*(?:\.{2,}|…+)\s*", "\x01", s)
    s = re.sub(r"\x01+(?=\s*[,.!?;:]|\s*$)", "", s)      # a pause before a mark / at the end
    s = re.sub(r"^\s*\x01+", "", s)
    s = re.sub(r"([,.!?;:])\s*\x01+", r"\1 ", s)
    s = re.sub(r"\x01+", ", ", s)
    s = re.sub(r"[!?！？]{2,}", lambda m: "?" if "?" in m[0] or "？" in m[0] else "!", s)
    s = re.sub(r"([,.;:])\1+", r"\1", s)
    s = re.sub(r"\s[-–—―]+\s|—+", ", ", s)
    s = re.sub(r"\s*&\s*", " và ", s)
    s = re.sub(r"\s\+\s", " cộng ", s)
    s = re.sub(r"[~～*#|^]+", "", s)
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def spoken(text: str, rules: Mapping[str, str] | None = None) -> str:
    """What the voice should say for one subtitle line (see the module docstring)."""
    s = unicodedata.normalize("NFC", text or "")
    s = _DECOR.sub("", s)
    if rules:
        s = apply_rules(s, rules)
    s = apply_rules(s, BUILTIN)
    s = _money_and_shorthand(s)
    s = _times_dates(s)
    s = _ordinals(s)
    s = _ranges(s)
    s = _numbers(s)
    return _punctuation(s)


# --- the user's dictionary ----------------------------------------------------------------

def _read(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip(): str(v) for k, v in data.items() if str(k).strip() and v is not None}


def merge_rules(global_rules: Mapping[str, str], project_rules: Mapping[str, str]) -> dict[str, str]:
    """Project entries first (they win ties), then the global ones they do not shadow —
    shadowed = same word ignoring case."""
    shadow = {k.casefold() for k in project_rules}
    out = dict(project_rules)
    out.update({k: v for k, v in global_rules.items() if k.casefold() not in shadow})
    return out


def global_path() -> Path:
    from app.config import settings

    return Path(settings.vizsup_storage_dir) / FILE


def load_rules(job) -> dict[str, str]:
    """``<storage>/pronounce.json`` merged with ``<job>/pronounce.json`` (project wins).
    Missing or invalid files are skipped — a hand-edited dictionary never breaks the dub."""
    return merge_rules(_read(global_path()), _read(Path(job.work_dir) / FILE))


def save_rules(path: Path, rules: Mapping[str, str]) -> None:
    """Sorted keys, atomic write; an empty dictionary removes the file."""
    path = Path(path)
    rules = {str(k).strip(): str(v) for k, v in rules.items() if str(k).strip()}
    if not rules:
        path.unlink(missing_ok=True)
        return
    atomic_write_text(path, json.dumps(dict(sorted(rules.items())), ensure_ascii=False, indent=1))
