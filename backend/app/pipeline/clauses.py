"""Subtitles that follow the speaker clause by clause (user report 2026-10-27).

The original video's captions show "并且还自带支架，" · "可以把手机立起来，" · "真挺好。" one
after another; ASR returned them as ONE line, so the Vietnamese line showed all three at
once — "…, thật sự rất ổn." — and the viewer read the punchline before it was said.

- ``split_cn``: right after ASR, cut each Chinese line at its clause punctuation
  (，。！？；…) and time every piece from the Whisper word track (``asr_words.json``):
  a piece starts when its first word is spoken. Pieces shorter than ``min_s`` or with
  fewer than ``min_chars`` characters stay joined to a neighbour.
- ``split_pair``: the same for an already translated project — the Vietnamese line is
  cut at its own clause punctuation when it has as many clauses as the Chinese pieces,
  so no re-translation is needed (editor: "Tách vế theo nhịp nói").

Pure functions; times in seconds.
"""
from __future__ import annotations

import re
from dataclasses import replace

CN_BREAK = "，,。！？!?；;：:…"
VI_BREAK = ",.!?;:…"
_SPOKEN = re.compile(r"[\w一-鿿]")          # characters a word track covers
MIN_S = 0.6        # a clause shorter than this flashes by: keep it joined
MIN_CHARS = 2


def _cn_clauses(text: str) -> list[str]:
    """'并且还自带支架，可以把手机立起来，真挺好。' → three clauses (punctuation kept)."""
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in CN_BREAK:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    # trailing punctuation runs ("？！") belong to the clause before
    merged: list[str] = []
    for c in out:
        if merged and not _SPOKEN.search(c):
            merged[-1] += c
        else:
            merged.append(c)
    return [c for c in merged if c.strip()]


def _vi_clauses(text: str) -> list[str]:
    parts = re.split(r"(?<=[,.!?;:…])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _char_times(words, start: float, end: float) -> list[tuple[float, float]]:
    """(start, end) of each spoken character of the words inside [start, end], in order."""
    times: list[tuple[float, float]] = []
    for w in words:
        text, ws, we = w[0], float(w[1]), float(w[2])
        mid = (ws + we) / 2           # a neighbour line's last/first word must not count
        if not start - 0.1 <= mid <= end + 0.1:
            continue
        chars = _SPOKEN.findall(text or "")
        if not chars:
            continue
        # Latin words count as one unit per character, like the subtitle text does
        step = (we - ws) / len(chars)
        times += [(ws + k * step, ws + (k + 1) * step) for k in range(len(chars))]
    return times


def clause_starts(text: str, start: float, end: float, words) -> list[tuple[str, float]]:
    """[(clause, start time)] for one line. Times from the word track when its character
    count matches the line's (±25 %), else proportional to the characters."""
    clauses = _cn_clauses(text)
    if len(clauses) < 2:
        return [(text, start)]
    counts = [len(_SPOKEN.findall(c)) for c in clauses]
    total = sum(counts) or 1
    times = _char_times(words or [], start, end)
    out, k = [], 0
    for clause, n in zip(clauses, counts, strict=True):
        if times and abs(len(times) - total) <= 0.25 * total:
            idx = min(int(round(k * len(times) / total)), len(times) - 1)
            t = times[idx][0] if k else start
        else:
            t = start + (end - start) * k / total
        out.append((clause, max(start, min(t, end))))
        k += n
    return out


def _pieces(starts: list[tuple[str, float]], end: float, *, min_s: float,
            min_chars: int) -> list[tuple[str, float, float]]:
    """Clause starts → contiguous (text, start, end) pieces, short ones joined."""
    pieces = [[c, s, (starts[i + 1][1] if i + 1 < len(starts) else end)]
              for i, (c, s) in enumerate(starts)]
    changed = True
    while changed and len(pieces) > 1:
        changed = False
        for i, (c, s, e) in enumerate(pieces):
            if e - s >= min_s and len(_SPOKEN.findall(c)) >= min_chars:
                continue
            # join the short piece to the neighbour with which the result is shorter
            j = i - 1 if i == len(pieces) - 1 or (
                i > 0 and pieces[i - 1][2] - pieces[i - 1][1] <= pieces[i + 1][2] - pieces[i + 1][1]
            ) else i + 1
            a, b = sorted((i, j))
            pieces[a:b + 1] = [[pieces[a][0] + pieces[b][0], pieces[a][1], pieces[b][2]]]
            changed = True
            break
    return [(c, s, e) for c, s, e in pieces]


def split_cn(cues, words, *, min_s: float = MIN_S, min_chars: int = MIN_CHARS) -> list:
    """Chinese cues (``index/start/end/text``) → clause-level cues, renumbered. Lines with
    one clause, or whose clauses would be too short, come back unchanged."""
    out = []
    for c in cues:
        starts = clause_starts(c.text, c.start, c.end, words)
        for text, s, e in _pieces(starts, c.end, min_s=min_s, min_chars=min_chars):
            out.append(replace(c, start=round(s, 3), end=round(e, 3), text=text.strip()))
    return [replace(c, index=i) for i, c in enumerate(out, start=1)]


def split_pair(zh: str, vi: str, start: float, end: float, words, *, min_s: float = MIN_S,
               min_chars: int = MIN_CHARS) -> list[tuple[str, str, float, float]] | None:
    """One translated line → [(zh, vi, start, end)] clause pieces, or None when it can't be
    split safely (one piece, or the Vietnamese has a different number of clauses)."""
    pieces = _pieces(clause_starts(zh, start, end, words), end, min_s=min_s,
                     min_chars=min_chars)
    if len(pieces) < 2:
        return None
    vi_parts = _vi_clauses(vi)
    if len(vi_parts) != len(_cn_clauses(zh)):
        return None
    # the joined pieces map onto consecutive Vietnamese clauses in the same order
    out, k = [], 0
    for text, s, e in pieces:
        n = len(_cn_clauses(text))
        out.append((text.strip(), " ".join(vi_parts[k:k + n]).strip(), s, e))
        k += n
    return out


def split_job(job, *, log=None) -> tuple[int, int] | None:
    """ASR stage add-on: rewrite ``cn.srt`` clause by clause, timed from ``asr_words.json``.
    Returns (lines before, after), or None when there is no word track (nothing to time
    the pieces with — proportional cuts would put a clause on screen too early/late)."""
    import json

    from app.pipeline.srt import parse_srt, write_srt

    try:
        words = json.loads((job.work_dir / "asr_words.json").read_text(encoding="utf-8"))["words"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not words or not job.cn_srt.exists():
        return None
    cues = parse_srt(job.cn_srt)
    out = split_cn(cues, words)
    if len(out) != len(cues):
        write_srt(out, job.cn_srt)
    if log:
        log(f"✓ Tách vế theo nhịp nói: {len(cues)} → {len(out)} câu")
    return len(cues), len(out)
