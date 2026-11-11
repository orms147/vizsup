"""«Chữ chạy theo lời» — karaoke word highlight in the burned subtitles (render option
``o["karaoke"] = {"on": bool, "color": "#RRGGBB"}``).

Each word of a Vietnamese line turns from the subtitle colour to the highlight colour as the dub
speaks it (ASS ``\\k`` tags: SecondaryColour until the word's time, PrimaryColour after — so the
line carries ``\\2c`` = its own colour and ``\\1c`` = the highlight). There is no word timing for
the Vietnamese text — it is the TTS that speaks it — so each word's share of the line's SPEECH
span is its spoken weight: the syllables ``speak.spoken`` reads ("2026" is six), plus a hold after
punctuation where the voice pauses. The span is where the dub segment really sits on the timeline
(``assemble._plan_segments``: start, and its length after the tempo fit), so a line pushed late by
the one before highlights late too; without a dub (no segments, the exact still frame, "Không lồng
tiếng") the cue's own span is used. Pure apart from ``spans`` (reads the cached segment lengths)."""
from __future__ import annotations

import re
from pathlib import Path

HIGHLIGHT = "#FFD400"
COLOURS = (("Vàng", "#FFD400"), ("Xanh lá", "#3DDC84"), ("Hồng", "#FF5CA8"), ("Cam", "#FF8A00"),
           ("Xanh dương", "#3BA7FF"))
GLASS_HIGHLIGHT = "#C2410C"     # on the white "Kính mờ trắng" glass a light colour would not read

_SPLIT = re.compile(r"(\{[^}]*\}|\\N|\s+)")
_LETTERS = re.compile(r"[^\W_]", re.UNICODE)
_LONG_PAUSE = re.compile(r"[.!?…]+[\"'”’)\]]*$")
_SHORT_PAUSE = re.compile(r"[,;:–—]+[\"'”’)\]]*$")
MIN_SPAN = 0.2                  # a speech span shorter than this: use the cue's own span


def colour_for(style: dict | None, color: str | None) -> str:
    """The highlight colour: the owner's pick, a dark one on the white glass. Pure."""
    from app.pipeline import glass

    if glass.is_glass(style):
        return GLASS_HIGHLIGHT
    c = str(color or "").strip()
    return c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else HIGHLIGHT


def weight(word: str) -> float:
    """How long ``word`` takes to say, in syllables (+ a hold after punctuation). Pure."""
    from app.pipeline import speak

    core = word
    if any(ch.isdigit() for ch in core) or "%" in core:
        try:
            n = len(speak.spoken(core).split())
        except Exception:  # noqa: BLE001 - a weight: one syllable is a fine guess
            n = 1
    else:
        n = 1 if _LETTERS.search(core) else 0
    w = max(n, 0) * 1.0
    if _LONG_PAUSE.search(core):
        w += 0.9
    elif _SHORT_PAUSE.search(core):
        w += 0.5
    return w


def _ass(hex_str: str) -> str:
    h = hex_str.lstrip("#").upper()
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&"


def tagged(text: str, start: float, end: float, span: tuple[float, float] | None, *,
           colour: str, base: str) -> str:
    """``text`` (already ASS: ``\\N`` line breaks, override tags kept) with a ``\\k`` before every
    word, the words sharing ``span`` (seconds, absolute) by ``weight``; ``base`` is the line's own
    colour (shown before a word is spoken), ``colour`` the highlight. Pure."""
    a, b = span if span else (start, end)
    a, b = max(a, start), min(b, end)
    if b - a < MIN_SPAN:
        a, b = start, end
    parts = [p for p in _SPLIT.split(text) if p]
    words = [i for i, p in enumerate(parts)
             if not (_SPLIT.fullmatch(p)) and weight(p) > 0]
    if not words:
        return text
    weights = [weight(parts[i]) for i in words]
    total_w = sum(weights)
    total_cs = max(len(words), int(round((b - a) * 100)))
    lead = int(round((a - start) * 100))
    out = [f"{{\\1c{_ass(colour)}\\2c{_ass(base)}}}"]
    if lead > 0:
        out.append(f"{{\\k{lead}}}")
    durs, acc, done = {}, 0.0, 0
    for i, w in zip(words, weights):
        acc += w
        upto = int(round(total_cs * acc / total_w))       # cumulative: no rounding drift
        durs[i] = max(1, upto - done)
        done += durs[i]
    for i, p in enumerate(parts):
        out.append(f"{{\\k{durs[i]}}}{p}" if i in durs else p)
    return "".join(out)


def spans(segments: list) -> dict[int, tuple[float, float]]:
    """Cue index → (start, end) of its dub segment as placed on the timeline (the same plan the
    dub track uses). Empty without segments or when a length can't be read."""
    from app.pipeline import assemble

    if not segments:
        return {}
    try:
        placements, _sped, _flagged = assemble._plan_segments(
            [(c, Path(p)) for c, p in segments])
    except Exception:  # noqa: BLE001 - the cue spans are a fine fallback
        return {}
    out: dict[int, tuple[float, float]] = {}
    for p in placements:
        cue = segments[p["inp"] - 1][0]
        out[int(cue.index)] = (float(p["start"]), float(p["start"]) + float(p["eff"]))
    return out
