"""E-2 fusion core (pure): snap a CN-native transcript's sentence times onto a
word-level timing track.

FunASR Paraformer gives the most accurate Mandarin TEXT but coarse sentence
times; faster-whisper/whisperx give precise word timestamps but worse hanzi.
``fuse_transcript_timing`` keeps FunASR's text and borrows the timing: greedy
monotonic character matching (zh words are 1–2 hanzi, so char anchors are
robust); a sentence matching under half its characters keeps FunASR's own
times, so fusion can never be worse than FunASR alone.
"""
from __future__ import annotations

import re

from app.models import Cue

_PUNCT = re.compile(r"[\s，。！？、：；·…~—,.!?;:\"'“”‘’()（）\[\]【】-]+")
_LOOKAHEAD = 60   # chars of forward search per anchor — bounds mismatch damage


def _chars(s: str) -> str:
    return _PUNCT.sub("", s or "")


def fuse_transcript_timing(
    sentences: list[Cue],
    words: list[tuple[str, float, float]],
) -> list[Cue]:
    """``sentences``: CN transcript cues (text wins). ``words``: (text, start,
    end) from the timing engine. Returns new cues, text unchanged, times snapped
    to the first/last matched word; monotonic by construction."""
    stream: list[tuple[str, float, float]] = []
    for w, s, e in words:
        for ch in _chars(w):
            stream.append((ch, float(s), float(e)))

    out: list[Cue] = []
    wi = 0
    for cue in sentences:
        chars = _chars(cue.text)
        matched: list[int] = []
        wj = wi
        for ch in chars:
            k = wj
            limit = min(len(stream), wj + _LOOKAHEAD)
            while k < limit and stream[k][0] != ch:
                k += 1
            if k < limit:
                matched.append(k)
                wj = k + 1
        if chars and len(matched) >= 0.5 * len(chars):
            start = stream[matched[0]][1]
            end = max(stream[matched[-1]][2], start)
            wi = matched[-1] + 1
            out.append(Cue(index=cue.index, start=start, end=end, text=cue.text))
        else:  # too little anchor evidence → trust FunASR's own times
            out.append(Cue(index=cue.index, start=cue.start, end=cue.end, text=cue.text))

    for prev, cur in zip(out, out[1:]):   # clamp any residual non-monotonicity
        if cur.start < prev.start:
            cur.start = prev.start
        if cur.end < cur.start:
            cur.end = cur.start
    return out


# --- global alignment for text-only cloud engines -----------------------------
# fuse_transcript_timing anchors one sentence at a time inside a 60-char window, and
# keeps the sentence's OWN times when anchoring fails. That is right for FunASR (its
# own times are decent) but wrong for text-only cloud engines, whose "own" times are a
# proportional guess across a whole chunk: on a real 136 s Douyin vlog one missed
# sentence knocked the following ones out of the window and cues landed 13 s off.
# align_text_to_words aligns the WHOLE chunk text to the word track at once
# (difflib, monotonic by construction) and interpolates every unmatched character
# between its nearest anchors — an error stays inside the gap between two anchors.

SECONDS_PER_CHAR = 0.22   # Mandarin ≈ 4–6 chars/s; natural length of an unanchored sentence
MIN_CUE = 0.3
CLUSTER_GAP = 2.0         # s — anchors further apart belong to different sung / spoken runs
MAX_SEC_PER_CHAR = 0.6    # an anchored sentence longer than chars × this (and > 8 s) is suspect
MAX_FREE_SEC = 8.0

Span = tuple[float, float]


def _voice_in(voice: list[Span] | None, a: float, b: float) -> list[Span]:
    if not voice or b <= a:
        return []
    return [(max(s, a), min(e, b)) for s, e in voice if s < b and e > a]


def _place_on_voice(weights: list[int], segs: list[Span]) -> list[Span]:
    """Lay consecutive items (char weights) end to end over the voice segments:
    item k occupies the voice time between cumulative fractions k/W .. (k+1)/W."""
    total_v = sum(e - s for s, e in segs)
    total_w = sum(weights) or 1

    def at(frac: float) -> float:
        left = frac * total_v
        for s, e in segs:
            if left <= e - s + 1e-9:
                return s + left
            left -= e - s
        return segs[-1][1]

    out, acc = [], 0
    for w in weights:
        out.append((at(acc / total_w), at((acc + w) / total_w)))
        acc += w
    return out


def _place_free(weights: list[int], a: float, b: float, where: str) -> list[Span]:
    """No voice data: natural length (SECONDS_PER_CHAR) against the anchored side,
    squeezed into [a, b] when it does not fit (instead of piling up on the boundary).
    A run between two anchored sentences fills its gap, as interpolation always did."""
    natural = [w * SECONDS_PER_CHAR for w in weights]
    room = max(b - a, 0.0)
    total = sum(natural) or 1.0
    if where == "middle" or total > room:
        natural = [n * room / total for n in natural]
        t = a
    elif where == "head":
        t = b - total                  # ends where the first anchored sentence starts
    else:
        t = a                          # tail: starts where the last anchored sentence ends
    out = []
    for n in natural:
        out.append((t, t + n))
        t += n
    return out


def _cluster(times: list[Span]) -> Span:
    """The densest run of anchors (gaps <= CLUSTER_GAP) — repeated chorus lines let
    difflib pin one sentence's first and last characters to different repeats."""
    times = sorted(times)
    runs: list[list[Span]] = [[times[0]]]
    for t in times[1:]:
        if t[0] - runs[-1][-1][1] > CLUSTER_GAP:
            runs.append([t])
        else:
            runs[-1].append(t)
    best = max(runs, key=len)          # max() keeps the earliest on ties
    return best[0][0], max(e for _s, e in best)


def _drop_collapsed(words: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Whisper sometimes stamps a whole sung phrase with ONE timestamp (seen
    2026-10-13: 19 words all at 136.24 s). Those times are fake anchors; drop every
    word whose start is shared by 3+ words."""
    from collections import Counter

    seen = Counter(round(float(s), 2) for _w, s, _e in words)
    return [w for w in words if seen[round(float(w[1]), 2)] < 3]


def _match_pairs(a: str, b: str) -> list[tuple[int, int]]:
    """In-order character alignment of transcript ``a`` against heard words ``b``
    (Needleman–Wunsch, free leading/trailing words): the matched (i, j) pairs that sit
    in runs of >= 2 consecutive matches. Unlike difflib's longest-block-first, a
    repeated chorus cannot be paired out of order (2026-10-13: a chorus line anchored
    on the previous chunk's copy left five lines with no anchors at all)."""
    n, m = len(a), len(b)
    match, miss, gap = 2, -1, -1
    prev = [0] * (m + 1)                       # leading words in b are free
    trace: list[bytearray] = []
    for i in range(1, n + 1):
        ai = a[i - 1]
        cur = [0] * (m + 1)
        cur[0] = prev[0] + gap
        tr = bytearray(m + 1)
        tr[0] = 1
        for j in range(1, m + 1):
            d = prev[j - 1] + (match if ai == b[j - 1] else miss)
            u = prev[j] + gap                  # transcript char left unmatched
            v = cur[j - 1] + gap               # heard char left unmatched
            if d >= u and d >= v:
                cur[j] = d
            elif u >= v:
                cur[j] = u
                tr[j] = 1
            else:
                cur[j] = v
                tr[j] = 2
        trace.append(tr)
        prev = cur
    j = max(range(m + 1), key=prev.__getitem__)   # trailing words in b are free
    i = n
    pairs: list[tuple[int, int]] = []
    while i > 0 and j > 0:
        t = trace[i - 1][j]
        if t == 0:
            if a[i - 1] == b[j - 1]:
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif t == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    keep = set()
    for k in range(1, len(pairs)):              # lone common hanzi (的/了/我) are noise
        (i0, j0), (i1, j1) = pairs[k - 1], pairs[k]
        if i1 == i0 + 1 and j1 == j0 + 1:
            keep.update((k - 1, k))
    return [pairs[k] for k in sorted(keep)]


def align_text_to_words(
    sentences: list[str],
    words: list[tuple[str, float, float]],
    span: tuple[float, float],
    *,
    voice: list[Span] | None = None,
) -> list[Cue]:
    """Time each sentence of a text-only transcript of the audio window ``span``
    using a word-level timing track (``(text, start, end)``, absolute seconds).

    Sentences with heard words take their anchors' times (interpolating unmatched
    characters between anchors). Sentences with NO heard words — Whisper often hears
    nothing over loud music — are laid over the voice (``voice``: VAD spans) in the gap
    they belong to, or at a natural speaking length when there is no voice data; they
    are never piled onto the window edge (that stacked 7 cues at 240.00 s, 2026-10-13).
    Such cues, and anchored ones cut down to their densest anchor run, come back with
    ``guess=True``. Returns cues with index 0 (callers reindex). Never raises."""
    import bisect

    lo, hi = span
    a_parts = [_chars(s) for s in sentences]
    a = "".join(a_parts)
    stream: list[tuple[str, float, float]] = []
    for w, s, e in _drop_collapsed(words):
        for ch in _chars(w):
            stream.append((ch, float(s), float(e)))
    b = "".join(ch for ch, _, _ in stream)

    anchors: dict[int, tuple[float, float]] = {}
    if a and b:
        for ia, ib in _match_pairs(a, b):
            _, s, e = stream[ib]
            anchors[ia] = (s, e)
    keys = sorted(anchors)

    def at(k: int, edge: int) -> float:       # edge 0 = start time, 1 = end time
        if k in anchors:
            return anchors[k][edge]
        j = bisect.bisect_left(keys, k)        # only called strictly between two anchors
        i0, i1 = keys[j - 1], keys[j]
        t0, t1 = anchors[i0][1], anchors[i1][0]
        return t0 + (t1 - t0) * (k - i0) / (i1 - i0)

    items = []                                # (text, first char, last char)
    pos = 0
    for text, part in zip(sentences, a_parts):
        if part:
            items.append((text, pos, pos + len(part) - 1))
            pos += len(part)

    # 1) sentences with anchors of their own
    times: list[Span | None] = []
    guess: list[bool] = []
    for _text, k0, k1 in items:
        mine = [k for k in range(k0, k1 + 1) if k in anchors]
        if not mine:
            times.append(None)
            guess.append(True)
            continue
        first, last = mine[0], mine[-1]
        s = at(first, 0) - (first - k0) * SECONDS_PER_CHAR    # unmatched leading chars
        e = at(last, 1) + (k1 - last) * SECONDS_PER_CHAR      # unmatched trailing chars
        g = False
        if e - s > max(MAX_FREE_SEC, MAX_SEC_PER_CHAR * (k1 - k0 + 1)):
            s, e = _cluster([anchors[k] for k in mine])
            g = True
        times.append((max(lo, s), min(hi, max(e, s))))
        guess.append(g)

    # 2) runs of sentences without anchors: lay them into the gap they belong to
    i = 0
    while i < len(items):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(items) and times[j] is None:
            j += 1
        gap_a = times[i - 1][1] if i > 0 else lo
        gap_b = times[j][0] if j < len(items) else hi
        if i > 0 and j < len(items):
            where = "middle"
        elif j < len(items):
            where = "head"
        elif i > 0:
            where = "tail"
        else:
            where = "middle"                    # nothing anchored at all: fill the window
        weights = [max(1, k1 - k0 + 1) for _t, k0, k1 in items[i:j]]
        segs = _voice_in(voice, gap_a, gap_b)
        placed = (_place_on_voice(weights, segs) if segs
                  else _place_free(weights, gap_a, max(gap_a, gap_b), where))
        for k, w, (ps, pe) in zip(range(i, j), weights, placed):
            # a lone guessed line must not swallow a whole stretch of voice
            times[k] = (ps, min(pe, ps + max(MAX_FREE_SEC, MAX_SEC_PER_CHAR * w)))
        i = j

    out = [Cue(index=0, start=s, end=max(e, s + MIN_CUE), text=t, guess=g)
           for (t, _k0, _k1), (s, e), g in zip(items, times, guess)]

    for prev, cur in zip(out, out[1:]):        # monotonic, no overlaps
        if cur.start < prev.start:
            cur.start = prev.start
        if prev.end > cur.start and cur.start > prev.start:
            prev.end = cur.start
        if cur.end < cur.start + MIN_CUE:
            cur.end = cur.start + MIN_CUE
    for c in out:
        c.start, c.end = round(c.start, 3), round(c.end, 3)
    return out


def _spread(sentences: list[str], lo: float, hi: float) -> list[Cue]:
    weights = [max(1, len(_chars(s))) for s in sentences]
    total, t, out = sum(weights) or 1, lo, []
    for s, w in zip(sentences, weights):
        d = (hi - lo) * w / total
        out.append(Cue(index=0, start=round(t, 3), end=round(t + d, 3), text=s, guess=True))
        t += d
    return out


def _too_long(c: Cue) -> bool:
    return c.end - c.start > max(MAX_FREE_SEC, MAX_SEC_PER_CHAR * max(1, len(_chars(c.text))))


def repair_runs(cues: list[Cue], *, voice: list[Span] | None = None,
                duration: float | None = None) -> int:
    """Local repair, no API: cues piled onto one start (stacked), zero-length, or
    absurdly long are re-laid over the voice between their nearest GOOD neighbours
    (natural length when there is no voice data) and marked ``guess``. Good cues are
    never touched. In place; returns how many cues were repaired."""
    n = len(cues)
    bad = [False] * n
    for i, c in enumerate(cues):
        same_prev = i > 0 and abs(c.start - cues[i - 1].start) < 0.05
        same_next = i + 1 < n and abs(c.start - cues[i + 1].start) < 0.05
        if same_prev or same_next or c.end - c.start < 0.05 or _too_long(c):
            bad[i] = True
    fixed = 0
    i = 0
    while i < n:
        if not bad[i]:
            i += 1
            continue
        j = i
        while j < n and bad[j]:
            j += 1
        a = cues[i - 1].end if i > 0 else 0.0
        b = cues[j].start if j < n else (duration or max(c.end for c in cues[i:j]))
        weights = [max(1, len(_chars(c.text))) for c in cues[i:j]]
        segs = _voice_in(voice, a, b)
        placed = _place_on_voice(weights, segs) if segs else _place_free(weights, a, max(a, b),
                                                                         "middle")
        for c, (s, e) in zip(cues[i:j], placed):
            c.start, c.end, c.guess = round(s, 3), round(max(e, s + MIN_CUE), 3), True
            fixed += 1
        i = j
    for prev, cur in zip(cues, cues[1:]):        # keep the no-overlap invariant
        if prev.end > cur.start and cur.start > prev.start:
            prev.end = cur.start
    return fixed
